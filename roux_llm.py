"""L'intelligence de Roux : backends (CLI, HTTP/Gemini, Anthropic) et outils.

Aucun code d'interface ici : les fonctions s'appellent depuis un fil de fond.
"""

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

from roux_core import API_KEY, BACKEND, MODELE, REGLAGES, SAUVEGARDE

def appeler_claude(systeme, messages, max_tokens=350):
    if not API_KEY:
        raise RuntimeError("il me manque la variable d'environnement ANTHROPIC_API_KEY")
    corps = json.dumps({"model": MODELE, "max_tokens": max_tokens,
                        "system": systeme, "messages": messages}).encode("utf-8")
    requete = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=corps,
        headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    with urllib.request.urlopen(requete, timeout=40) as r:
        donnees = json.load(r)
    return "".join(b.get("text", "") for b in donnees["content"] if b.get("type") == "text").strip()


def trouver_cmdc():
    """Chemin du CLI Command Code (cmdc sous Windows), ou None s'il n'est pas installé."""
    nom = REGLAGES["commande_cmdc"]
    chemin = shutil.which(nom)
    if chemin:
        return chemin
    npm = os.path.join(os.environ.get("APPDATA", ""), "npm")   # shims npm hors du PATH
    for suffixe in (".cmd", ".exe", ""):
        p = Path(npm) / (nom + suffixe)
        if p.exists():
            return str(p)
    return None


def backend_pret():
    """Le backend choisi peut-il répondre ?"""
    if BACKEND == "http":
        return bool(REGLAGES.get("url_http", "").strip())
    if BACKEND == "anthropic":
        return bool(API_KEY)
    return trouver_cmdc() is not None


def invite_roux(systeme, messages):
    """L'invite complète envoyée au CLI : le personnage, puis la conversation à plat."""
    return (systeme + "\n\n--- conversation ---\n"
            + "\n".join(("Toi : " if m["role"] == "user" else "Roux : ") + m["content"]
                        for m in messages)
            + "\n\nRéponds en tant que Roux, en 1 à 3 phrases, sans préambule "
              "et sans utiliser d'outil.")


def appeler_cmdc(systeme, messages, max_tokens=350):
    """Fait réfléchir Roux via le CLI Command Code en mode headless : aucune clé API à gérer,
    la consommation passe par ton abonnement Command Code."""
    chemin = trouver_cmdc()
    if not chemin:
        raise RuntimeError("je ne trouve pas le CLI Command Code "
                           "(installe-le : npm i -g command-code, puis cmd login)")
    options = ["-p", "--output-format", "json", "--max-turns", "4",
               "--model", MODELE, "--skip-onboarding"]
    if REGLAGES["effort"]:
        options += ["--effort", REGLAGES["effort"]]
    # l'invite passe par l'entrée standard : aucun texte utilisateur ne touche la ligne de commande
    proc = subprocess.run(subprocess.list2cmdline([chemin] + options), shell=True,
                          input=invite_roux(systeme, messages), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180,
                          cwd=str(SAUVEGARDE.parent),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for ligne in reversed(proc.stdout.splitlines()):
        ligne = ligne.strip()
        if not ligne.startswith("{"):
            continue
        try:
            d = json.loads(ligne)
        except ValueError:
            continue
        if d.get("type") == "result":
            if d.get("subtype") == "success" and d.get("finalText"):
                return d["finalText"].strip()
            raise RuntimeError(str(d.get("error") or d.get("subtype") or "réponse vide"))
    details = (proc.stderr or proc.stdout or "").strip().splitlines()
    raise RuntimeError(details[-1][:300] if details else f"code de sortie {proc.returncode}")


# --------------------------------------------------- agent persistant (ACP)
class AgentAcp:
    """Garde un « cmdc acp » vivant : son démarrage se paie une fois, pas à chaque message.

    Mesuré sur cette machine : 6,5 à 7 s par message en mode « -p », contre 2 à 2,5 s
    par message avec ce processus persistant (5 s de démarrage une seule fois).
    """

    def __init__(self):
        self.processus = None
        self.verrou = threading.Lock()
        self.identifiant = 0
        self.reponses = {}          # identifiant d'appel -> file d'attente
        self.morceaux = []          # texte reçu pour la réplique en cours
        self.partiel = None
        self.delai = 120

    def vivant(self):
        return self.processus is not None and self.processus.poll() is None

    def demarrer(self):
        chemin = trouver_cmdc()
        if not chemin:
            raise RuntimeError("je ne trouve pas le CLI Command Code")
        self.processus = subprocess.Popen(
            subprocess.list2cmdline([chemin, "acp"]), shell=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=str(SAUVEGARDE.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._lire, daemon=True).start()
        self._appeler("initialize", {"protocolVersion": 1,
                                     "clientCapabilities": {"fs": {"readTextFile": False,
                                                                   "writeTextFile": False}}},
                      delai=90)

    def arreter(self):
        if self.processus is not None:
            try:
                self.processus.kill()
            except OSError:
                pass
        self.processus = None

    def _lire(self):
        for brute in self.processus.stdout:
            ligne = brute.decode("utf-8", "replace").strip()
            if not ligne.startswith("{"):
                continue
            try:
                message = json.loads(ligne)
            except ValueError:
                continue
            self._distribuer(message)
        for attente in list(self.reponses.values()):
            attente.put(None)                 # le processus est mort : on débloque les attentes

    def _distribuer(self, message):
        attente = self.reponses.get(message.get("id"))
        if attente is not None:
            attente.put(message)
            return
        if message.get("method") == "session/update":
            maj = (message.get("params") or {}).get("update") or {}
            if maj.get("sessionUpdate") == "agent_message_chunk":
                morceau = (maj.get("content") or {}).get("text")
                if morceau:
                    self.morceaux.append(morceau)
                    if self.partiel is not None:
                        self.partiel("".join(self.morceaux))
            return
        if message.get("id") is not None:
            # demande du serveur (autorisation d'outil, lecture de fichier) : Roux n'en veut pas
            self._repondre(message["id"], {"outcome": {"outcome": "cancelled"}})

    def _envoyer(self, message):
        self.processus.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.processus.stdin.flush()

    def _repondre(self, identifiant, resultat):
        try:
            self._envoyer({"jsonrpc": "2.0", "id": identifiant, "result": resultat})
        except (OSError, ValueError, AttributeError):
            pass

    def _appeler(self, methode, params, delai=None):
        self.identifiant += 1
        identifiant = self.identifiant
        attente = queue.Queue()
        self.reponses[identifiant] = attente
        try:
            self._envoyer({"jsonrpc": "2.0", "id": identifiant, "method": methode,
                           "params": params})
            message = attente.get(timeout=delai or self.delai)
        except (OSError, ValueError, queue.Empty) as err:
            raise RuntimeError(f"agent persistant muet ({err.__class__.__name__})")
        finally:
            self.reponses.pop(identifiant, None)
        if message is None:
            raise RuntimeError("l'agent persistant s'est arrêté")
        if "error" in message:
            raise RuntimeError(str(message["error"].get("message", message["error"]))[:200])
        return message.get("result") or {}

    def _fermer(self, identifiant_session):
        try:
            self._appeler("session/close", {"sessionId": identifiant_session}, delai=15)
        except Exception:
            pass

    def repondre(self, invite, partiel=None):
        """Ouvre une session, pose la question, rend la réponse. Repli assuré par l'appelant."""
        with self.verrou:
            if not self.vivant():
                self.demarrer()
            self.morceaux, self.partiel = [], partiel
            try:
                session = self._appeler("session/new",
                                        {"cwd": str(SAUVEGARDE.parent), "mcpServers": []},
                                        delai=60)
                identifiant_session = session.get("sessionId")
                if not identifiant_session:
                    raise RuntimeError("session non ouverte")
                resultat = self._appeler("session/prompt",
                                         {"sessionId": identifiant_session,
                                          "prompt": [{"type": "text", "text": invite}]})
                texte = "".join(self.morceaux).strip()
                self._fermer(identifiant_session)
                if not texte:
                    raise RuntimeError(str(resultat.get("stopReason") or "réponse vide"))
                return texte
            except Exception:
                self.arreter()               # au prochain essai, un processus neuf
                raise
            finally:
                self.partiel = None


AGENT_ACP = None


def acp_actif():
    return REGLAGES.get("acp", "1").strip().lower() not in ("", "0", "non", "false", "off")


def appeler_acp(systeme, messages, max_tokens=350, sur_partiel=None):
    """Passe par l'agent persistant : même résultat que le CLI, sans repayer son démarrage."""
    global AGENT_ACP
    if AGENT_ACP is None:
        AGENT_ACP = AgentAcp()
    return AGENT_ACP.repondre(invite_roux(systeme, messages), sur_partiel)


def cle_http():
    """La clé du backend HTTP : « $VAR » la lit dans l'environnement, sinon elle est littérale."""
    brut = (REGLAGES.get("cle_http") or "").strip()
    if brut.startswith("{env:") and brut.endswith("}"):
        return os.environ.get(brut[5:-1], "")
    if brut.startswith("$"):
        return os.environ.get(brut[1:], "")
    return brut


def flux_actif():
    return REGLAGES.get("flux", "1").strip().lower() not in ("", "0", "non", "false", "off")


def _morceaux_sse(reponse, sur_partiel):
    """Lit un flux SSE « data: {...} » et rend le texte accumulé."""
    morceaux = []
    while True:
        brute = reponse.readline()
        if not brute:
            break
        ligne = brute.decode("utf-8", "replace").strip()
        if not ligne.startswith("data:"):
            continue
        charge = ligne[5:].strip()
        if charge == "[DONE]":
            break
        try:
            choix = json.loads(charge)["choices"]
        except (ValueError, KeyError, TypeError, IndexError):
            continue
        if not choix:
            continue
        morceau = (choix[0].get("delta") or {}).get("content")
        if morceau:
            morceaux.append(morceau)
            if sur_partiel is not None:
                sur_partiel("".join(morceaux))
    return "".join(morceaux).strip()


def appeler_http(systeme, messages, max_tokens=350, sur_partiel=None):
    """Appelle une API compatible OpenAI (LM Studio, Ollama, passerelle…), en flux si possible."""
    base = (REGLAGES.get("url_http") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("il me manque l'adresse de l'API (url_http dans roux_config.json)")
    entetes = {"Content-Type": "application/json"}
    cle = cle_http()
    if cle:
        entetes["Authorization"] = "Bearer " + cle
    en_flux = bool(sur_partiel is not None and flux_actif())
    corps = {"model": MODELE,
             "messages": [{"role": "system", "content": systeme}] + list(messages),
             "max_tokens": max_tokens,
             "stream": en_flux}
    if REGLAGES["effort"]:
        corps["reasoning_effort"] = REGLAGES["effort"]    # OpenAI, Gemini, OpenRouter…
    requete = urllib.request.Request(base + "/chat/completions", headers=entetes,
                                     data=json.dumps(corps).encode("utf-8"))
    with urllib.request.urlopen(requete, timeout=45) as reponse:
        if en_flux:
            return _morceaux_sse(reponse, sur_partiel)
        donnees = json.load(reponse)
    return (donnees["choices"][0]["message"]["content"] or "").strip()


# ------------------------------------------------------------------ outils
# Déclarations au format OpenAI, acceptées par Gemini : c'est le modèle qui choisit
# d'appeler un outil, il n'y a plus de détection par mots-clés côté Python.
OUTILS = [
    {"type": "function", "function": {
        "name": "get_system_metrics",
        "description": "État réel du PC : processeur, mémoire, disque libre, batterie.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_local_weather",
        "description": "Météo du jour pour une ville (température, ciel, vent, pluie).",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "nom de la ville"}},
            "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "start_pomodoro",
        "description": "Lance un minuteur de concentration ; Roux reste sage pendant ce temps.",
        "parameters": {"type": "object", "properties": {
            "work_min": {"type": "integer", "description": "minutes de travail"},
            "break_min": {"type": "integer", "description": "minutes de pause"}},
            "required": ["work_min"]}}},
    {"type": "function", "function": {
        "name": "run_powershell_maintenance",
        "description": ("Exécute un script de maintenance du dossier scripts/ de Roux "
                        "(nettoyage, diagnostic réseau). Aucun autre chemin n'est permis."),
        "parameters": {"type": "object", "properties": {
            "script_name": {"type": "string",
                            "description": "nom du script, par exemple nettoyer_temp.ps1"}},
            "required": ["script_name"]}}},
    {"type": "function", "function": {
        "name": "feed_roux",
        "description": "Donne à manger à Roux.",
        "parameters": {"type": "object", "properties": {
            "food": {"type": "string",
                     "enum": ["poisson", "souris", "oeuf", "baies", "pomme"]}}},
        }},
    {"type": "function", "function": {
        "name": "pet_roux", "description": "Caresse Roux.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "send_sleep", "description": "Envoie Roux faire une sieste.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "set_reminder",
        "description": "Demande à Roux de rappeler quelque chose dans N minutes.",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "number"},
            "text": {"type": "string", "description": "ce qu'il doit rappeler"}},
            "required": ["minutes", "text"]}}},
    {"type": "function", "function": {
        "name": "ask_veille",
        "description": "Lance la veille informatique : lecture des flux et résumé.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "teach_lesson",
        "description": "Apprend un fait nouveau à l'utilisateur, sur un sujet donné ou libre.",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "sujet, facultatif"}}}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": "Retient durablement un fait sur l'utilisateur (prénom, métier, goûts).",
        "parameters": {"type": "object", "properties": {
            "fact": {"type": "string", "description": "le fait à retenir"}},
            "required": ["fact"]}}},
]

SCRIPTS = SAUVEGARDE.parent / "scripts"
MOTIF_SCRIPT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,38}\.ps1$")


def scripts_disponibles():
    """Les scripts que Roux a le droit de lancer, et rien d'autre."""
    if not SCRIPTS.is_dir():
        return []
    return sorted(p.name for p in SCRIPTS.iterdir()
                  if p.is_file() and MOTIF_SCRIPT.match(p.name))


def executer_script(nom):
    """Exécute un .ps1 du dossier scripts/. Nom filtré, chemin vérifié, aucune commande libre."""
    nom = (nom or "").strip().lower()
    if not MOTIF_SCRIPT.match(nom):
        return {"erreur": "nom invalide : minuscules, chiffres et tirets, terminé par .ps1",
                "disponibles": scripts_disponibles()}
    if not SCRIPTS.is_dir():
        return {"erreur": "le dossier scripts/ n'existe pas"}
    chemin = (SCRIPTS / nom).resolve()
    if chemin.parent != SCRIPTS.resolve() or not chemin.is_file():
        return {"erreur": f"script introuvable : {nom}", "disponibles": scripts_disponibles()}
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(chemin)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"erreur": f"échec du lancement ({err.__class__.__name__})"}
    sortie = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"script": nom, "code": proc.returncode, "sortie": sortie[-1500:]}


def _poste_json(base, entetes, corps, timeout=45):
    """POST JSON avec un message d'erreur lisible : sans ça, un 401 ressemble à un bug."""
    requete = urllib.request.Request(base + "/chat/completions", headers=entetes,
                                     data=json.dumps(corps).encode("utf-8"))
    try:
        with urllib.request.urlopen(requete, timeout=timeout) as reponse:
            return json.load(reponse)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:180].replace("\n", " ")
        aide = {401: "clé refusée", 403: "accès refusé pour cette clé",
                404: "modèle ou adresse introuvable", 429: "quota dépassé",
                503: "modèle surchargé"}.get(err.code, "")
        raise RuntimeError(f"HTTP {err.code} {aide} : {detail}".strip())
    except Exception as err:
        raise RuntimeError(f"API injoignable ou trop lente ({err.__class__.__name__})")


def appeler_http_outils(systeme, messages, max_tokens, executer, sur_partiel=None):
    """Boucle d'exécution : le modèle demande un outil, Python l'exécute, le modèle conclut."""
    base = (REGLAGES.get("url_http") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("il me manque l'adresse de l'API (url_http dans roux_config.json)")
    entetes = {"Content-Type": "application/json"}
    cle = cle_http()
    if cle:
        entetes["Authorization"] = "Bearer " + cle
    conversation = [{"role": "system", "content": systeme}] + list(messages)
    for _tour in range(4):                      # quatre allers-retours au maximum
        corps = {"model": MODELE, "messages": conversation, "tools": OUTILS,
                 "max_tokens": max_tokens, "stream": False}
        if REGLAGES["effort"]:
            corps["reasoning_effort"] = REGLAGES["effort"]
        donnees = _poste_json(base, entetes, corps)
        message = (donnees.get("choices") or [{}])[0].get("message") or {}
        appels = message.get("tool_calls") or []
        if not appels:
            return (message.get("content") or "").strip()
        conversation.append(message)
        for appel in appels:
            fonction = appel.get("function") or {}
            nom = str(fonction.get("name") or "")
            try:
                arguments = json.loads(fonction.get("arguments") or "{}")
            except ValueError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                resultat = executer(nom, arguments)
            except Exception as err:
                resultat = {"erreur": str(err)}
            conversation.append({"role": "tool", "tool_call_id": appel.get("id"),
                                 "name": nom,
                                 "content": json.dumps(resultat, ensure_ascii=False)})
    return "Je me suis emmêlé dans mes outils… tu peux reformuler ?"


def appeler_llm(systeme, messages, max_tokens=350, sur_partiel=None, executer=None):
    if BACKEND == "http":
        if executer is not None:
            return appeler_http_outils(systeme, messages, max_tokens, executer, sur_partiel)
        return appeler_http(systeme, messages, max_tokens, sur_partiel)
    if BACKEND == "anthropic":
        return appeler_claude(systeme, messages, max_tokens)
    # backend CLI : l'agent persistant évite de repayer six secondes de démarrage à chaque
    # message ; s'il fait défaut, on retombe proprement sur le mode « -p »
    if acp_actif():
        try:
            return appeler_acp(systeme, messages, max_tokens, sur_partiel)
        except Exception:
            pass
    return appeler_cmdc(systeme, messages, max_tokens)


def arreter_agent():
    """Arrête le processus persistant, s'il tourne encore."""
    if AGENT_ACP is not None:
        AGENT_ACP.arreter()



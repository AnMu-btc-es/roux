"""Serveur MCP (stdio) : expose Roux le renard à Command Code et à tout autre agent.

Roux doit tourner en même temps (il écrit roux_api.json, avec le port et le jeton,
à côté de ce fichier). Enregistrement auprès de Command Code :

    cmdc mcp add roux -- python "chemin\\vers\\compagnon_mcp.py"
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

FICHIER_API = Path(__file__).with_name("roux_api.json")
PROTOCOLE = "2024-11-05"

OUTILS = [
    {
        "name": "roux_etat",
        "description": "L'état de Roux : faim, énergie, humeur, âge, pomodoros, et ce qu'il a retenu.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "roux_nourrir",
        "description": "Donner à manger à Roux. Il adore le poisson.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "aliment": {
                    "type": "string",
                    "enum": ["poisson", "souris", "oeuf", "baies", "pomme"],
                    "description": "Ce que tu lui donnes (poisson par défaut).",
                }
            },
        },
    },
    {
        "name": "roux_caresser",
        "description": "Caresser Roux, ce qui remonte son humeur.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "roux_dire",
        "description": "Faire dire quelque chose à Roux dans sa bulle (un message, un avertissement).",
        "inputSchema": {
            "type": "object",
            "properties": {"texte": {"type": "string", "description": "Le texte à afficher."}},
            "required": ["texte"],
        },
    },
    {
        "name": "roux_dormir",
        "description": "Envoyer Roux faire une sieste.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "roux_rappel",
        "description": "Demander à Roux de te rappeler quelque chose dans N minutes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "Délai en minutes."},
                "texte": {"type": "string", "description": "Ce qu'il doit te rappeler."},
            },
            "required": ["minutes", "texte"],
        },
    },
    {
        "name": "roux_pomodoro",
        "description": "Lancer un pomodoro ; Roux reste sage pendant la phase de travail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "travail": {"type": "number", "description": "Minutes de travail (25 par défaut)."},
                "pause": {"type": "number", "description": "Minutes de pause (5 par défaut)."},
            },
        },
    },
    {
        "name": "roux_arreter_pomodoro",
        "description": "Arrêter le pomodoro en cours.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "roux_apprendre",
        "description": "Demander à Roux de t'apprendre quelque chose (sujet libre ou imposé).",
        "inputSchema": {
            "type": "object",
            "properties": {"sujet": {"type": "string", "description": "Sujet de la leçon."}},
        },
    },
    {
        "name": "roux_veille",
        "description": ("Lancer la veille informatique : Roux lit ses flux d'actualité et en fait "
                        "un résumé. Le résumé s'affiche dans sa bulle, pas ici."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

CORRESPONDANCE = {
    "roux_nourrir": "nourrir",
    "roux_caresser": "caresser",
    "roux_dire": "dire",
    "roux_dormir": "dormir",
    "roux_rappel": "rappel",
    "roux_pomodoro": "pomodoro",
    "roux_arreter_pomodoro": "arreter_pomodoro",
    "roux_apprendre": "apprendre",
    "roux_veille": "veille",
}


def api(chemin, donnees=None):
    """Appelle l'API locale de Roux."""
    try:
        reglages = json.loads(FICHIER_API.read_text(encoding="utf-8"))
        port, jeton = int(reglages["port"]), str(reglages["jeton"])
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("Roux n'est pas lancé (roux_api.json introuvable ou illisible).")
    entetes = {"X-Jeton": jeton}
    corps = None
    if donnees is not None:
        corps = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
        entetes["Content-Type"] = "application/json"
    requete = urllib.request.Request(f"http://127.0.0.1:{port}{chemin}", data=corps, headers=entetes)
    try:
        with urllib.request.urlopen(requete, timeout=15) as reponse:
            return json.load(reponse)
    except urllib.error.HTTPError as err:
        try:
            return json.load(err)
        except ValueError:
            raise RuntimeError(f"Roux a répondu avec le code {err.code}.")
    except (urllib.error.URLError, OSError):
        raise RuntimeError("Roux ne répond pas (est-il toujours ouvert ?).")


def appeler_outil(nom, arguments):
    if nom == "roux_etat":
        reponse = api("/etat")
        if "erreur" in reponse:
            raise RuntimeError(reponse["erreur"])
        return reponse.get("resultat")
    outil = CORRESPONDANCE.get(nom)
    if outil is None:
        raise RuntimeError(f"outil inconnu : {nom}")
    reponse = api("/action", {"outil": outil, "arguments": arguments or {}})
    if "erreur" in reponse:
        raise RuntimeError(reponse["erreur"])
    return {"ok": True, "outil": outil}


def repondre(identifiant, resultat=None, erreur=None):
    message = {"jsonrpc": "2.0", "id": identifiant}
    if erreur is not None:
        message["error"] = {"code": -32000, "message": str(erreur)}
    else:
        message["result"] = resultat
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def traiter(message):
    methode, identifiant = message.get("method"), message.get("id")
    if methode == "initialize":
        repondre(identifiant, {"protocolVersion": PROTOCOLE, "capabilities": {"tools": {}},
                               "serverInfo": {"name": "roux", "version": "1.0"}})
    elif methode == "tools/list":
        repondre(identifiant, {"tools": OUTILS})
    elif methode == "tools/call":
        params = message.get("params") or {}
        nom = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        try:
            resultat = appeler_outil(nom, arguments)
        except Exception as err:
            repondre(identifiant, {"content": [{"type": "text", "text": str(err)}], "isError": True})
            return
        repondre(identifiant, {"content": [{"type": "text",
                                            "text": json.dumps(resultat, ensure_ascii=False)}],
                               "isError": False})
    elif methode == "ping":
        repondre(identifiant, {})
    elif methode in ("notifications/initialized", "notifications/cancelled"):
        pass
    elif identifiant is not None:
        repondre(identifiant, None, f"méthode inconnue : {methode}")


def main():
    for flux in (sys.stdin, sys.stdout):
        try:
            flux.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    for ligne in sys.stdin:
        ligne = ligne.strip().lstrip("\ufeff").strip()   # certains tuyaux Windows glissent un BOM
        if not ligne:
            continue
        try:
            message = json.loads(ligne)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        try:
            traiter(message)
        except Exception as err:
            if message.get("id") is not None:
                repondre(message["id"], None, err)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

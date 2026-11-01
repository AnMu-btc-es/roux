"""Le socle de Roux : configuration, état, système, météo et veille.

Aucun code d'interface ici : tout est appelable depuis n'importe quel fil.
"""

import email.utils
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- réglages
NOM_DEFAUT = "Roux"
SAUVEGARDE = Path(__file__).with_name("renard_save.json")
PAUSE_MIN = 45              # rappel de pause toutes les N minutes (0 = désactivé)
SUGGESTION_MIN = 90         # petite leçon spontanée toutes les N minutes (0 = désactivé)
ALERTES_SYSTEME = True      # il te prévient si le PC souffre (processeur, mémoire, batterie, disque)
TICK_MS = 60                # fluidité de l'animation

# ------------------------------------------------------- intelligence (LLM)
CONFIG = Path(__file__).with_name("roux_config.json")
REGLAGES_DEFAUT = {
    # « cmdc » = CLI Command Code, « http » = API compatible OpenAI, « anthropic » = API directe
    "backend": "cmdc",
    "modele": "claude-haiku-5-5",   # identifiant exact, propre au backend choisi
    "commande_cmdc": "cmdc",        # nom du CLI Command Code (cmdc sous Windows)
    "effort": "",                   # low, medium, high… (vide = laissé au modèle)
    "veille_heure": "",             # « 09:00 » = veille automatique quotidienne, vide = jamais
    "taille": "3",                  # 2 à 6 : nombre de pixels par point du renard
    "url_http": "",                 # racine de l'API compatible OpenAI, ex. LM Studio
    "cle_http": "",                 # clé, ou « $MA_VARIABLE » pour la lire dans l'environnement
    "flux": "1",                    # 1 = écrire la réponse au fil de l'eau (streaming)
    "acp": "1",                     # 1 = garder le CLI vivant (bien plus rapide) ; 0 = mode « -p »
}


def charger_reglages():
    """Les réglages de roux_config.json, complétés par les valeurs par défaut."""
    r = dict(REGLAGES_DEFAUT)
    try:
        # utf-8-sig : un fichier enregistré par le Bloc-notes peut porter un BOM,
        # que json refuse sinon — les réglages seraient ignorés en silence
        d = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return r
    if isinstance(d, dict):
        for cle in r:
            valeur = d.get(cle)
            if isinstance(valeur, str) and valeur.strip():
                r[cle] = valeur.strip()
    return r


REGLAGES = charger_reglages()
BACKEND = REGLAGES["backend"]
MODELE = REGLAGES["modele"]
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def reglage_entier(cle, defaut, mini, maxi):
    """Un réglage numérique borné, tolérant aux valeurs absentes ou farfelues."""
    try:
        return max(mini, min(maxi, int(str(REGLAGES.get(cle, "")).strip())))
    except (TypeError, ValueError):
        return defaut


ALIMENTS = {
    "poisson": {"nom": "Poisson", "faim": 30, "humeur": 15, "phrase": "Du poisson ! Mon plat préféré !"},
    "souris": {"nom": "Souris", "faim": 35, "humeur": 8, "phrase": "Miam ! L'instinct du chasseur !"},
    "oeuf": {"nom": "Oeuf", "faim": 20, "humeur": 5, "phrase": "Un oeuf ! Je n'y ai pas touché avant, promis."},
    "baies": {"nom": "Baies", "faim": 12, "humeur": 6, "phrase": "Des baies ! Sucrées, juste comme j'aime."},
    "pomme": {"nom": "Pomme", "faim": 10, "humeur": 2, "phrase": "Croc ! Une pomme, c'est bon pour la santé."},
}


# ------------------------------------------------------------ vie du renard
def _baisse(valeur, delta, plancher):
    return max(min(valeur, plancher), valeur - delta)


class Etat:
    """Les jauges du tamagotchi (sans interface : facile à tester)."""

    def __init__(self):
        self.nom = NOM_DEFAUT
        self.faim = 70.0          # 100 = rassasié
        self.energie = 90.0
        self.humeur = 70.0
        self.malice = 30.0        # 100 = rusé et farceur
        self.naissance = time.time()
        self.derniere_maj = time.time()
        self.carnet = []          # leçons apprises
        self.souvenirs = []       # ce qu'il retient de son humain
        self.liens_vus = []       # titres de veille déjà résumés
        self.derniere_veille = ""  # jour de la dernière veille
        self.historique = []      # derniers messages échangés
        self.nouveau = True
        self.ville = ""
        self.lat = None
        self.lon = None
        self.pomodoros = 0

    @classmethod
    def charger(cls):
        e = cls()
        try:
            d = json.loads(SAUVEGARDE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return e
        if not isinstance(d, dict):
            return e
        e.nouveau = False
        e.nom = str(d.get("nom", e.nom))[:20] or NOM_DEFAUT
        for champ in ("faim", "energie", "humeur", "malice", "naissance", "derniere_maj"):
            try:
                setattr(e, champ, float(d.get(champ, getattr(e, champ))))
            except (TypeError, ValueError):
                pass
        e.carnet = [c for c in d.get("carnet", []) if isinstance(c, dict)][-200:]
        e.souvenirs = [s for s in d.get("souvenirs", []) if isinstance(s, str)][-40:]
        e.liens_vus = [l for l in d.get("liens_vus", []) if isinstance(l, str)][-400:]
        e.derniere_veille = str(d.get("derniere_veille", ""))[:10]
        e.historique = [m for m in d.get("historique", [])
                        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                        and isinstance(m.get("content"), str)]
        e.ville = str(d.get("ville", ""))[:60]
        for champ in ("lat", "lon"):
            try:
                setattr(e, champ, float(d[champ]))
            except (KeyError, TypeError, ValueError):
                setattr(e, champ, None)
        try:
            e.pomodoros = int(d.get("pomodoros", 0))
        except (TypeError, ValueError):
            e.pomodoros = 0
        e.nettoyer_historique()
        e.absence((time.time() - e.derniere_maj) / 60)
        return e

    def sauver(self):
        self.derniere_maj = time.time()
        donnees = {
            "nom": self.nom, "faim": self.faim, "energie": self.energie,
            "humeur": self.humeur, "malice": self.malice, "naissance": self.naissance,
            "derniere_maj": self.derniere_maj,
            "carnet": self.carnet[-200:], "souvenirs": self.souvenirs[-40:],
            "liens_vus": self.liens_vus[-400:], "derniere_veille": self.derniere_veille,
            "historique": self.historique[-12:],
            "ville": self.ville, "lat": self.lat, "lon": self.lon, "pomodoros": self.pomodoros,
        }
        try:
            # écriture atomique : fichier temporaire puis renommage, pour qu'une coupure
            # en pleine écriture ne puisse pas détruire la sauvegarde
            temporaire = SAUVEGARDE.with_name(SAUVEGARDE.name + ".tmp")
            temporaire.write_text(json.dumps(donnees, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            os.replace(temporaire, SAUVEGARDE)
        except OSError:
            pass

    def nettoyer_historique(self):
        self.historique = self.historique[-12:]
        while self.historique and self.historique[0]["role"] != "user":
            self.historique.pop(0)

    def absence(self, minutes):
        """Pendant que l'ordinateur était éteint : il a dormi, mais il a un peu faim."""
        minutes = max(0.0, min(minutes, 480))
        if minutes < 1:
            return
        self.faim = _baisse(self.faim, 0.15 * minutes, 5.0)
        self.energie = 100.0
        self.humeur = _baisse(self.humeur, 0.05 * minutes, 0.0)

    def evoluer(self, minutes, dort):
        if dort:
            self.energie = min(100.0, self.energie + 3.0 * minutes)
            self.faim = max(0.0, self.faim - 0.25 * minutes)
        else:
            self.energie = max(0.0, self.energie - 0.3 * minutes)
            self.faim = max(0.0, self.faim - 0.5 * minutes)
        if self.faim < 25 or self.energie < 15:
            self.humeur = max(0.0, self.humeur - 0.4 * minutes)
        elif self.humeur < 70:
            self.humeur = min(70.0, self.humeur + 0.2 * minutes)
        elif self.humeur > 70:
            self.humeur = max(70.0, self.humeur - 0.1 * minutes)
        # l'esprit s'aiguise quand il vit sa vie ; les jeux le font monter plus vite
        self.malice = min(100.0, self.malice + 0.05 * minutes)

    def donner(self, cle):
        a = ALIMENTS[cle]
        if self.faim >= 90:
            return False, "Je n'ai vraiment plus faim, merci !"
        self.faim = min(100.0, self.faim + a["faim"])
        self.humeur = min(100.0, self.humeur + a["humeur"])
        self.malice = min(100.0, self.malice + 2)
        return True, a["phrase"]

    def caresser(self):
        self.humeur = min(100.0, self.humeur + 6)
        self.malice = min(100.0, self.malice + 3)

    def age_jours(self):
        return max(0, int((time.time() - self.naissance) / 86400))

    def contexte(self):
        return (f"faim/satiété {int(self.faim)}/100 (100 = ventre plein), "
                f"énergie {int(self.energie)}/100, humeur {int(self.humeur)}/100")


def extraire_liste(reponse):
    """Lit un tableau JSON de chaînes, même entouré de texte."""
    texte = (reponse or "").strip()
    debut, fin = texte.find("["), texte.rfind("]")
    if debut < 0 or fin <= debut:
        return []
    try:
        d = json.loads(texte[debut:fin + 1])
    except ValueError:
        return []
    return [f.strip() for f in d if isinstance(f, str) and f.strip()][:2]


# mots de liaison et de cadrage : ils ne distinguent pas un souvenir d'un autre
MOTS_VIDES = {
    "le", "la", "les", "un", "une", "des", "de", "du", "au", "aux", "et", "est", "son", "sa",
    "ses", "il", "elle", "ils", "elles", "se", "ce", "qui", "que", "sur", "sous", "avec",
    "sans", "pour", "par", "dans", "en", "nom", "prénom", "prenom", "appelle", "appeler",
    "surnom", "s'appelle",
}


def mots_significatifs(texte):
    """Les mots qui portent le sens d'un souvenir, sans les mots de liaison."""
    trouves = re.findall(r"[\wàâäéèêëîïôöùûüç]+", (texte or "").lower())
    return {mot for mot in trouves if len(mot) > 2 and mot not in MOTS_VIDES}


def deja_connu(fait, souvenirs):
    """Vrai si ce fait dit déjà la même chose qu'un souvenir existant."""
    si = mots_significatifs(fait)
    for ancien in souvenirs:
        sa = mots_significatifs(ancien)
        if not si or not sa:
            if fait.lower() in ancien.lower() or ancien.lower() in fait.lower():
                return True
            continue
        commun = len(si & sa) / len(si | sa)
        if commun >= 0.6:
            return True
    return False


def nombre(valeur, defaut):
    """Un nombre utilisable, sinon le défaut."""
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return float(defaut)


# ------------------------------------------------------------ météo (Open-Meteo, sans clé)
CODES_METEO = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant", 51: "bruine légère", 53: "bruine", 55: "bruine forte",
    56: "bruine verglaçante", 57: "bruine verglaçante", 61: "pluie faible", 63: "pluie", 65: "forte pluie",
    66: "pluie verglaçante", 67: "pluie verglaçante", 71: "neige faible", 73: "neige", 75: "forte neige",
    77: "grains de neige", 80: "averses faibles", 81: "averses", 82: "fortes averses",
    85: "averses de neige", 86: "fortes averses de neige", 95: "orage", 96: "orage avec grêle",
    99: "orage avec forte grêle",
}


def http_json(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def geocoder(nom):
    d = http_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": nom, "count": 1, "language": "fr", "format": "json"}))
    resultats = d.get("results") or []
    if not resultats:
        raise RuntimeError(f"je ne connais pas la ville « {nom} »")
    r = resultats[0]
    return r["name"], r["latitude"], r["longitude"]


def previsions(lat, lon):
    return http_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"}))


def texte_meteo(ville, d):
    cur, jour = d["current"], d["daily"]
    desc = CODES_METEO.get(cur.get("weather_code"), "temps incertain")
    t = cur["temperature_2m"]
    pluie = jour["precipitation_probability_max"][0] or 0
    texte = (f"{ville} : {t:.0f} °C (ressenti {cur['apparent_temperature']:.0f} °C), {desc}, "
             f"vent {cur['wind_speed_10m']:.0f} km/h. Aujourd'hui : {jour['temperature_2m_min'][0]:.0f} "
             f"à {jour['temperature_2m_max'][0]:.0f} °C, pluie {pluie:.0f} %.")
    if pluie >= 50:
        texte += " Prends un parapluie !"
    elif t <= 3:
        texte += " Couvre-toi bien, il fait froid."
    elif t >= 28:
        texte += " Pense à boire de l'eau."
    return "*renifle l'air* " + texte


# ------------------------------------------------------------ veille info
# Ces flux ont été testés depuis la machine : ils répondent tous en RSS ou Atom.
# On ne garde que les titres et les liens, jamais le corps des articles.
AGENT = "Mozilla/5.0 (compatible; Roux/1.0)"
FLUX_VEILLE = (
    ("Journal du Hacker", "https://www.journalduhacker.net/rss"),
    ("Next", "https://next.ink/feed/"),
    ("Numerama", "https://www.numerama.com/feed/"),
    ("Clubic", "https://www.clubic.com/feed/news.rss"),
    ("Frandroid", "https://www.frandroid.com/feed"),
    ("Le Monde Informatique",
     "https://www.lemondeinformatique.fr/flux-rss/thematique/toutes-les-actualites/rss.xml"),
    ("Hacker News", "https://hnrss.org/frontpage?points=100"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("BleepingComputer", "https://www.bleepingcomputer.com/feed/"),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
    ("ANSSI", "https://www.cert.ssi.gouv.fr/feed/"),
)
VEILLE_PAR_FLUX = 6        # entrées lues par flux
VEILLE_MAX = 12            # titres confiés au modèle : au-delà, le premier token tarde
VEILLE_FRAICHEUR = 3       # on ignore au-delà de N jours


def _balise(element):
    return element.tag.split("}")[-1]


def date_flux(valeur):
    """Une date RSS ou Atom convertie en datetime, ou None."""
    if not valeur:
        return None
    try:
        return email.utils.parsedate_to_datetime(valeur)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(valeur.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def lire_flux(nom, url, limite=VEILLE_PAR_FLUX, delai=8):
    """Rend [(source, titre, lien, date)] pour un flux RSS ou Atom. 8 s : un flux mort ne bloque pas."""
    requete = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(requete, timeout=delai) as reponse:
        racine = ET.fromstring(reponse.read())
    entrees = []
    for noeud in racine.iter():
        if _balise(noeud) not in ("item", "entry"):
            continue
        titre = lien = brut = ""
        for enfant in noeud:
            nom_balise = _balise(enfant)
            if nom_balise == "title" and not titre:
                titre = " ".join((enfant.text or "").split())
            elif nom_balise == "link" and not lien:
                lien = (enfant.get("href") or enfant.text or "").strip()
            elif nom_balise in ("pubDate", "updated", "published") and not brut:
                brut = enfant.text or ""
        if titre:
            entrees.append((nom, titre, lien, date_flux(brut)))
        if len(entrees) >= limite:
            break
    return entrees


# cache mémoire : redemander la veille dans la demi-heure ne relance aucun appel HTTP
VEILLE_CACHE = {"quand": 0.0, "entrees": []}
VEILLE_CACHE_MINUTES = 30


def vider_cache_veille():
    VEILLE_CACHE["quand"], VEILLE_CACHE["entrees"] = 0.0, []


def collecter_veille(forcer=False):
    """Interroge tous les flux de front, en parallèle. Rend les plus récentes d'abord."""
    maintenant = time.time()
    if (not forcer and VEILLE_CACHE["entrees"]
            and maintenant - VEILLE_CACHE["quand"] < VEILLE_CACHE_MINUTES * 60):
        return list(VEILLE_CACHE["entrees"])     # instantané, aucun accès réseau

    trouvees, verrou = [], threading.Lock()

    def moissonner(nom, url):
        try:
            entrees = lire_flux(nom, url)
        except Exception:                     # flux injoignable ou XML cassé : on l'ignore
            return
        with verrou:
            trouvees.extend(entrees)

    fils = [threading.Thread(target=moissonner, args=(nom, url), daemon=True)
            for nom, url in FLUX_VEILLE]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(timeout=10)                  # un flux lent ne doit pas retenir les autres

    plancher = (datetime.now().astimezone() - timedelta(days=VEILLE_FRAICHEUR)).timestamp()
    toutes = []
    for nom, titre, lien, date in trouvees:
        horodate = plancher + 1.0            # flux sans date : frais, mais classé en dernier
        if date is not None:
            if date.tzinfo is None:
                date = date.replace(tzinfo=datetime.now().astimezone().tzinfo)
            horodate = date.timestamp()
        toutes.append((nom, titre, lien, horodate))
    toutes.sort(key=lambda entree: entree[3], reverse=True)
    recentes = [entree for entree in toutes if entree[3] >= plancher]
    # week-end calme ou flux lents : plutôt que rien, on prend les plus récentes telles quelles
    resultat = recentes if len(recentes) >= 10 else toutes[:VEILLE_MAX]
    if resultat:
        VEILLE_CACHE["quand"], VEILLE_CACHE["entrees"] = maintenant, list(resultat)
    return resultat


# ------------------------------------------------------------ état de l'ordinateur (Windows)
class Systeme:
    """Mesures via l'API Windows (ctypes) : aucune bibliothèque externe. Renvoie None si indisponible."""

    def __init__(self):
        self.dernier = self._temps_cpu()

    @staticmethod
    def _temps_cpu():
        try:
            import ctypes
            repos, noyau, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
            if not ctypes.windll.kernel32.GetSystemTimes(
                    ctypes.byref(repos), ctypes.byref(noyau), ctypes.byref(user)):
                return None
            return repos.value, noyau.value + user.value      # le temps noyau inclut le repos
        except Exception:
            return None

    @staticmethod
    def _pourcent(a, b):
        if a is None or b is None or b[1] - a[1] <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (1 - (b[0] - a[0]) / (b[1] - a[1]))))

    def cpu_depuis_dernier(self):
        b = self._temps_cpu()
        p = self._pourcent(self.dernier, b)
        self.dernier = b
        return p

    def cpu_instantane(self, attente=0.5):
        a = self._temps_cpu()
        time.sleep(attente)
        return self._pourcent(a, self._temps_cpu())

    @staticmethod
    def memoire():
        """(pourcentage utilisé, Go utilisés, Go total)"""
        try:
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None
            total = m.ullTotalPhys / 2 ** 30
            return float(m.dwMemoryLoad), total - m.ullAvailPhys / 2 ** 30, total
        except Exception:
            return None

    @staticmethod
    def disque():
        """(pourcentage utilisé, Go libres)"""
        try:
            import shutil
            u = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
            return 100.0 * u.used / u.total, u.free / 2 ** 30
        except Exception:
            return None

    @staticmethod
    def batterie():
        """(pourcentage, branché ?) ou None s'il n'y a pas de batterie"""
        try:
            import ctypes

            class ETAT(ctypes.Structure):
                _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("pourcent", ctypes.c_ubyte),
                            ("reserve", ctypes.c_ubyte), ("duree", ctypes.c_ulong), ("duree_max", ctypes.c_ulong)]
            e = ETAT()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(e)):
                return None
            if e.pourcent == 255 or e.flag & 128:
                return None
            return int(e.pourcent), e.ac == 1
        except Exception:
            return None

    def resume(self, attente=0.5):
        lignes = []
        cpu = self.cpu_instantane(attente)
        if cpu is not None:
            lignes.append(f"Processeur : {cpu:.0f} %")
        mem = self.memoire()
        if mem:
            lignes.append(f"Mémoire : {mem[0]:.0f} % ({mem[1]:.1f} / {mem[2]:.1f} Go)")
        dsk = self.disque()
        if dsk:
            lignes.append(f"Disque : {dsk[1]:.0f} Go libres ({dsk[0]:.0f} % utilisé)")
        bat = self.batterie()
        if bat:
            lignes.append(f"Batterie : {bat[0]} % ({'sur secteur' if bat[1] else 'sur batterie'})")
        if not lignes:
            raise RuntimeError("je n'arrive pas à lire l'état de l'ordinateur (Windows requis)")
        return "\n".join(lignes)



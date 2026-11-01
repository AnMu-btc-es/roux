"""
Roux, le renard de bureau (Windows) - compagnon puis assistant système.

Trois modules :
  compagnon.py  ce fichier : interface Tk, rendu matriciel, souris, boucle after()
  roux_core.py  configuration, état du renard, mesures système, météo, veille RSS
  roux_llm.py   backends (CLI Command Code, HTTP/Gemini, Anthropic) et outils

  - Il se balade en bas de l'écran, dort quand il est fatigué, a faim, change d'humeur,
    et chasse de temps en temps une bestiole de son propre chef.
  - Clic sur lui : Nourrir, Caresser, Parler, Apprendre, Dormir, Outils. Glisser pour déplacer.
  - Dépose un fichier texte ou un log sur le renard : il en lit 2000 caractères et rend un
    diagnostic express.
  - Barre de notification : « Masquer » le range dans le tiroir Windows, avec une icône
    dessinée depuis la grille et les jauges en direct dans le menu. Il se cache aussi seul
    quand une fenêtre en plein écran le recouvre, et revient dès qu'elle se ferme.
  - Démarrage : --demarrage on l'inscrit avec --tray, donc il démarre caché dans la barre
    de notification, sans rien afficher à l'écran.
  - Outils du modèle (appel de fonction, plus de détection par mots-clés) : état du PC,
    météo, pomodoro, veille, leçons, souvenirs, et exécution des scripts de maintenance
    du dossier scripts/ (nom filtré, chemin vérifié : rien d'autre n'est exécutable).
  - Veille : 12 flux (presse informatique FR, sécurité, Hacker News), résumé en 12 titres
    maximum, résultat mis en cache 30 minutes. "veille_heure": "09:00" pour la quotidienne.
  - Il est dessiné dans le code : aucune image à fournir.

Prérequis : Python 3.10+, plus tkinterdnd2 pour le glisser-déposer (facultatif).
Pour qu'il parle, trois backends au choix dans roux_config.json :
  - « cmdc »   : le CLI Command Code (npm i -g command-code, puis cmd login) ;
  - « http »   : une API compatible OpenAI — LM Studio, Ollama, Gemini, passerelle…
                 renseigner url_http et modele. Pour Gemini :
                   "url_http": "https://generativelanguage.googleapis.com/v1beta/openai"
                   "cle_http": "$GEMINI_API_KEY"    (clé sur aistudio.google.com/apikey)
                   "modele":   "gemini-3.8-flash"
                 C'est le seul backend qui accepte les appels d'outils ;
  - « anthropic » : variable d'environnement ANTHROPIC_API_KEY.
Réglages : roux_config.json (backend, modèle, CLI, taille) à côté de ce script.
  "taille" va de 2 à 6 (3 par défaut) : c'est le nombre de pixels par point du renard.
Sa vie est sauvegardée dans renard_save.json, à côté de ce script.
Démarrage automatique : compagnon.py --demarrage on | off | status
Pilotage depuis Command Code : compagnon_mcp.py est un serveur MCP qui parle au renard
  par une petite API locale (127.0.0.1, jeton dans roux_api.json). Enregistrement :
  cmdc mcp add roux --scope user -- python compagnon_mcp.py
"""

import email.utils
import json
import math
import os
import queue
import random
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import messagebox

from roux_core import (  # noqa: E402
    ALERTES_SYSTEME, ALIMENTS, CONFIG, NOM_DEFAUT, PAUSE_MIN, REGLAGES, REGLAGES_DEFAUT,
    SAUVEGARDE, SUGGESTION_MIN, TICK_MS, VEILLE_MAX, Etat, Systeme,
    collecter_veille, deja_connu, extraire_liste, geocoder, nombre, previsions, reglage_entier,
    texte_meteo,
)
from roux_llm import (  # noqa: E402
    appeler_llm, arreter_agent, backend_pret, executer_script, scripts_disponibles,
)
import roux_core  # noqa: E402
import roux_llm  # noqa: E402

try:                                    # glisser-déposer, facultatif
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_DISPONIBLE = True
except ImportError:                     # sans la bibliothèque, tout le reste continue de marcher
    DND_DISPONIBLE = False

try:                                    # icône dans la barre de notification, facultatif
    import pystray
    from PIL import Image, ImageDraw
    TRAY_DISPONIBLE = True
except ImportError:
    TRAY_DISPONIBLE = False

TAILLE_MINI, TAILLE_MAXI = 2, 6
ECHELLE = reglage_entier("taille", int(REGLAGES_DEFAUT["taille"]), TAILLE_MINI, TAILLE_MAXI)

# Charte sombre (inspirée de Dracula) : tout le châssis est sombre. Les pixels du renard
# gardent leur propre palette, définie plus bas.
FOND_UI = "#282a36"       # fond des panneaux, du chat et des bulles
TEXTE_UI = "#f8f8f2"      # texte principal
ACCENT_UI = "#bd93f9"     # bordures, nom du renard, surbrillances
LIGNE_UI = "#44475a"      # filets, fonds de jauges, champs de saisie
FILET_UI = "#565a76"      # filets et creux de jauge : plus clair, sinon invisibles
SOURDINE_UI = "#6272a4"   # texte secondaire, indications
CYAN_UI = "#8be9fd"       # ce que dit l'humain

# noms historiques conservés : tout le code s'y réfère
CREME, BRUN, ORANGE = FOND_UI, TEXTE_UI, ACCENT_UI

THEMES = [
    "les animaux", "l'espace", "l'histoire", "les sciences", "la géographie",
    "les langues (un mot ou une expression utile)", "la cuisine", "le corps humain",
    "l'informatique et la cybersécurité", "les mathématiques", "la musique",
    "la nature et les saisons",
]

PHRASES_CARESSE = [
    "Hihi, ça chatouille !",
    "*ronronne comme un renard peut le faire*",
    "Encore un peu, là, derrière l'oreille...",
    "J'adore quand tu fais ça !",
    "*remue la queue de bonheur*",
]

PHRASES_CHASSE = [
    "*bondit et plaque la bestiole sous sa patte* Attrapée ! Bon, elle est un peu aplatie...",
    "*saute, museau le premier, et retombe le nez dans la poussière* Ratée de peu !",
    "*la coince contre le bord de l'écran* Je l'ai eue. Je te la montre ? Contre un poisson.",
    "*tourne en rond, la queue haute* Une proie ! Enfin, un passe-temps.",
]

# === SPRITES DEBUT ===
PALETTE = {
    "o": "#3b2216",  # contour brun
    "r": "#ee7d2c",  # orange
    "R": "#cf5f17",  # orange foncé
    "w": "#fff3e0",  # crème
    "W": "#e0cdac",  # crème ombré
    "e": "#1b1012",  # yeux
    "n": "#2a1a1d",  # truffe
    "p": "#ff8fa3",  # rose
    "h": "#ff4d6d",  # rouge (coeur, pomme)
    "H": "#c62b47",  # rouge foncé
    "y": "#ffd23f",  # jaune
    "g": "#9aa0a6",  # gris
    "G": "#6b7075",  # gris foncé
    "b": "#6cb4ee",  # bleu
    "B": "#3f7fbf",  # bleu foncé
    "v": "#5b4fa8",  # violet (baies)
    "V": "#3f3670",  # violet foncé
    "l": "#58b368",  # vert (feuille)
    "k": "#ffffff",  # blanc pur
    "q": "#6b3a22",  # pattes (chaussettes brunes)
    "Q": "#4a2a1a",  # pattes du côté éloigné
    "d": "#7d3b0f",  # liseré devant la fourrure orange (vraiment sombre)
    "s": "#3f4756",  # liseré devant le crème (gris bleuté sombre)
}
FOND = "#000000"  # couleur rendue transparente par la fenêtre

LARG, HAUT = 24, 22   # taille de la grille du renard
# la fenêtre suit la taille du renard : la marge laisse la place à l'icône de pensée
CW = LARG * ECHELLE + 34
CH = HAUT * ECHELLE + 47
DECALAGE_ICONE = ECHELLE / 4.0        # les icônes flottantes suivaient l'échelle 4
ECHELLE_ICONE = max(2, ECHELLE - 1)   # icônes de pensée : elles suivent le renard
LARGEUR_BULLE = 250                   # largeur de texte maximale dans une bulle
HAUTEUR_SAUT = int(4 * ECHELLE)       # sommet du bond pendant la chasse
DUREE_SAUT = 0.5                      # durée du bond, en secondes

# moitié gauche (10 colonnes) ; le miroir est ajouté automatiquement
BASE = [
    "...oo.....",
    "..orro....",
    "..orwro...",
    ".orrwrro..",
    ".orrwrrrrr",
    ".orrrrrrrr",
    "orrrrrrrrr",
    "orrreerrrr",
    "orrreerrrr",
    "orwwrrrwwn",
    "owwwwwwwwo",
    ".owwwwwwwo",
    "..oowwwwww",
    "....orrwww",
    "....orrwww",
    "....orrwww",
    "....orrwww",
    "....orrwww",
    "....orwwwo",
    "....oooooo",
]

# remplacements de lignes (moitié gauche) selon l'expression
EXPRESSIONS = {
    "normal": {},
    "cligne": {7: "orrrrrrrrr"},
    "content": {7: "orrrrerrrr", 8: "orrrererrr"},
    "triste": {7: "orrrrrrrrr", 10: "owwwbwwwwo"},
    "mange_a": {10: "owwwwwwwee", 11: ".owwwwwwpp"},
    "mange_b": {10: "owwwwwwwwo"},
}

# queue (colonnes 14 à 23), dessinée derrière le corps ; lignes de départ -> motif
QUEUES = {
    "a": (9, [
        ".......oo.",
        "......owwo",
        "......owwo",
        ".....orrwo",
        ".....orrro",
        "....orrrRo",
        "....orrRRo",
        "...orrrRo.",
        "..orrrRo..",
        "..orrRo...",
        "..oooo....",
    ]),
    "b": (10, [
        "........oo",
        "......owwo",
        "......owwo",
        ".....orrwo",
        ".....orrRo",
        "....orrRRo",
        "....orrRo.",
        "...orrRo..",
        "..orrRo...",
        "..oooo....",
    ]),
}

def _demi(lignes):
    return ["..%s" % l for l in lignes]          # marge de 2 colonnes

def _miroir(demi):
    return [l + l[::-1] for l in demi]

def grille_renard(expression="normal", queue="a", decalage=1, miroir=False):
    """Retourne une grille (liste de chaînes) LARG x HAUT."""
    g = [["."] * LARG for _ in range(HAUT)]
    debut, motif = QUEUES[queue]
    for i, ligne in enumerate(motif):
        for j, c in enumerate(ligne):
            if c != "." and 0 <= debut + i + decalage < HAUT:
                g[debut + i + decalage][14 + j] = c
    base = list(BASE)
    for num, ligne in EXPRESSIONS[expression].items():
        base[num] = ligne
    for i, ligne in enumerate(_miroir(_demi(base))):
        for j, c in enumerate(ligne):
            if c != "." and 0 <= i + decalage < HAUT:
                g[i + decalage][j] = c
    # le cerne de ce sprite est dessiné dans la grille : on le recolore, sans en rajouter un
    lignes = ["".join(r) for r in _contour(g, ajouter=False)]
    if miroir:
        lignes = [l[::-1] for l in lignes]
    return lignes

def _teinte_bordure(voisins):
    """La teinte de liseré qui convient : gris bleuté devant du crème, orange très foncé
    devant de la fourrure orange, brun pour tout le reste (pattes, museau, détails).

    Deux teintes dédiées plutôt que 'R' et 'G' : #cf5f17 et #6b7075 sont des tons moyens,
    qui ne détachent pas la silhouette sur un bureau sombre.
    """
    if "w" in voisins:
        return "s"
    if "r" in voisins or "R" in voisins:
        return "d"
    return "o"


def _contour(g, ajouter=True):
    """Liseré sélectif, en deux passes.

    1. autour de la silhouette (si `ajouter`) : chaque pixel transparent reçoit la teinte
       sombre du pixel qu'il borde (crème → gris bleuté, orange → orange foncé, reste → brun) ;
    2. sur les contours déjà dessinés dans la grille : ils suivent la même règle, sinon le
       renard de face garderait son cerne brun uniforme et les autres non.
    """
    res = [r[:] for r in g]
    for y in range(HAUT):
        for x in range(LARG):
            if g[y][x] == ".":
                if not ajouter:
                    continue
                voisins = {g[y + dy][x + dx]
                           for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))
                           if 0 <= y + dy < HAUT and 0 <= x + dx < LARG}
                voisins.discard(".")
                if voisins:
                    res[y][x] = _teinte_bordure(voisins)
            elif g[y][x] == "o":
                voisins = {g[y + dy][x + dx]
                           for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                           if (dy or dx) and 0 <= y + dy < HAUT and 0 <= x + dx < LARG}
                voisins.discard(".")
                voisins.discard("o")
                if voisins:
                    res[y][x] = _teinte_bordure(voisins)
    return res

# renard endormi en boule : tête posée, queue enroulée devant (plus foncée pour se détacher)
def grille_sommeil(respire=False, miroir=False):
    g = [["."] * LARG for _ in range(HAUT)]
    # corps blotti
    _ellipse(g, 13.6, 14.8, 6.4, 4.2, "r")
    _ellipse(g, 13.6, 15.4, 5.8, 3.2, "w", lambda x, y: y >= 16)
    # tête : un liseré la détache du corps
    _ellipse(g, 5.6, 13.2, 5.4, 4.4, "o")
    _ellipse(g, 5.6, 13.2, 4.8, 3.8, "r")
    _ellipse(g, 3.2, 13.6, 3.0, 2.4, "w")
    # oreilles dressées
    _triangle(g, (2.6, 10.4), (3.2, 5.8), (6.4, 10.0), "r")
    _triangle(g, (6.2, 10.2), (8.0, 6.6), (9.8, 11.0), "r")
    # queue enroulée devant, du flanc arrière vers le museau
    queue = ((19.0, 17.2, 2.3, 2.1), (16.4, 17.8, 2.5, 2.1), (13.8, 18.0, 2.4, 2.0),
             (11.2, 17.8, 2.3, 1.9), (9.0, 17.2, 2.1, 1.8), (7.4, 16.4, 1.9, 1.7))
    for cx, cy, rx, ry in queue:
        _ellipse(g, cx, cy, rx + 0.8, ry + 0.8, "o")
    for cx, cy, rx, ry in queue:
        _ellipse(g, cx, cy, rx, ry, "R")
    _ellipse(g, 6.6, 15.4, 2.1, 1.9, "o")
    _ellipse(g, 6.6, 15.4, 1.7, 1.5, "w")            # bout de queue crème
    # pattes repliées sous le museau
    for x in (4, 5, 6):
        g[18][x] = "q"
    # oeil fermé (une petite barre) et truffe
    for x in (6, 7, 8):
        g[12][x] = "e"
    g[14][1] = "n"
    if respire:                                      # le dos se soulève d'un pixel
        g = g[1:] + [["."] * LARG]
    lignes = ["".join(r) for r in _contour(g)]
    return [l[::-1] for l in lignes] if miroir else lignes

# renard de profil qui marche (regarde vers la droite), dessiné avec des formes simples
POSITIONS_PATTES = [3, 0, -3, 0]        # avancée du pied sur 4 images ; la pose 4 = pied levé

def _ellipse(g, cx, cy, rx, ry, c, filtre=None):
    for y in range(HAUT):
        for x in range(LARG):
            if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1:
                if filtre is None or filtre(x, y):
                    g[y][x] = c

def _triangle(g, p1, p2, p3, c):
    def signe(a, b, p):
        return (a[0] - p[0]) * (b[1] - p[1]) - (b[0] - p[0]) * (a[1] - p[1])
    for y in range(HAUT):
        for x in range(LARG):
            p = (x + 0.5, y + 0.5)
            d1, d2, d3 = signe(p, p1, p2), signe(p, p2, p3), signe(p, p3, p1)
            if not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0)):
                g[y][x] = c

def _patte(g, x0, dx, leve, couleur):
    """Une patte de 2 pixels de large, du ventre (ligne 15) au sol (ligne 19)."""
    bas = 18 if leve else 19
    for y in range(15, bas + 1):
        x = x0 + round(dx * (y - 15) / 4)
        for xx in (x, x + 1):
            if 0 <= xx < LARG:
                g[y][xx] = couleur

def grille_marche(image, miroir=False):
    """image : 0 à 3 (cycle de marche). Le corps ne bouge pas, seules les pattes et la queue."""
    g = [["."] * LARG for _ in range(HAUT)]
    # pattes du côté éloigné (derrière le corps), décalées d'une demi-foulée
    for x0, phase in ((17, 2), (9, 0)):
        k = (image + phase) % 4
        _patte(g, x0, POSITIONS_PATTES[k], k == 3, "Q")
    # queue : légère oscillation, cerclée d'un liseré pour ne pas se fondre dans le corps
    cy = 10.5 if image in (0, 1) else 11.5
    _ellipse(g, 4.2, cy, 5.0, 3.6, "o")
    _ellipse(g, 4.2, cy, 4.2, 2.8, "r")
    _ellipse(g, 4.2, cy, 4.2, 2.8, "w", lambda x, y: x <= 1)
    # corps, ventre, poitrail
    _ellipse(g, 11.5, 12, 6.5, 3.9, "r")
    _ellipse(g, 11.5, 12, 6.5, 3.9, "w", lambda x, y: y >= 14)
    _ellipse(g, 16, 11, 3, 3.5, "r")
    # tête, museau, oreilles
    _ellipse(g, 18.5, 8.5, 3.5, 3, "r")
    _ellipse(g, 18.5, 8.5, 3.5, 3, "w", lambda x, y: y >= 10 and x >= 18)
    _ellipse(g, 21.5, 9.5, 2.5, 1.6, "r", lambda x, y: y <= 9)
    _ellipse(g, 21.5, 9.5, 2.5, 1.6, "w", lambda x, y: y >= 10)
    _triangle(g, (15.5, 6.5), (16.5, 2), (18.5, 6.5), "r")
    _triangle(g, (18.5, 6.5), (19.5, 2), (21.5, 6.5), "r")
    g[4][17] = g[4][20] = "w"
    g[8][20] = "e"                      # oeil
    g[9][22] = "n"                      # truffe
    # pattes du côté proche
    for x0, phase in ((15, 0), (7, 2)):
        k = (image + phase) % 4
        _patte(g, x0, POSITIONS_PATTES[k], k == 3, "q")
    # contour brun autour de la silhouette
    lignes = ["".join(r) for r in _contour(g)]
    return [l[::-1] for l in lignes] if miroir else lignes

# petites icônes (contour ajouté automatiquement)
ICONES = {
    "coeur": [
        ".hh.hh.",
        "hhhhhhh",
        "hhhhhhh",
        ".hhhhh.",
        "..hhh..",
        "...h...",
    ],
    "poisson": [
        "....BB.....",
        "..bbbbb....",
        ".bbbbbbbBBB",
        "bebbbbbBBBB",
        ".bwwwwwBBBB",
        ".bwwwwbbBBB",
        "..bbbbb....",
    ],
    "souris": [
        "..gg...gg..",
        ".gppg.gppg.",
        ".ggggggggg.",
        "pgeggggggg.",
        "ggggggggggg",
        "ggkkkkkggpp",
        ".ggkkkgg.p.",
        "..gGGGg....",
    ],
    "oeuf": [
        "...www...",
        "..wkwww..",
        ".wkwwwww.",
        ".wwwwwww.",
        "wwwwwwwww",
        "wwwwwwwww",
        ".wwwwwww.",
        ".wWWWWWWw",
        "..wWWWw..",
    ],
    "baies": [
        "....lll....",
        "...l.l.l...",
        "..vvvlvvv..",
        ".vvkvvvvvv.",
        ".vVvvvvkvv.",
        "..vvvvvvv..",
        "...vvvvv...",
    ],
    "pomme": [
        ".....n....",
        "...llln...",
        "..hhhhhh..",
        ".hhhhhhhh.",
        "hhkhhhhhhh",
        "hhkhhhhhhh",
        ".hhhhhhhh.",
        "..hHHHHh..",
        "...hhhh...",
    ],
    "zzz": [
        "bbbbb",
        "...b.",
        "..b..",
        ".b...",
        "bbbbb",
    ],
    "livre": [
        "bbbbbbb",
        "bkkbkkb",
        "bkkbkkb",
        "bkkbkkb",
        "bbbbbbb",
    ],
    "bulle": [
        "kkkkkkk",
        "kkkkkkk",
        "kkkkkkk",
        "kkkkkkk",
        ".kk....",
        "..k....",
    ],
    "lune": [
        "..yyyy",
        ".yyy..",
        "yyy...",
        "yyy...",
        "yyy...",
        ".yyy..",
        "..yyyy",
    ],
    "tomate": [
        "...l.l....",
        "..lllll...",
        ".hhhhhhh..",
        "hhkhhhhhh.",
        "hhkhhhhhhh",
        "hhhhhhhhhh",
        ".hhhhhhhh.",
        "..hHHHHh..",
        "...hhhh...",
    ],
    "soleil": [
        "...y...",
        ".y.y.y.",
        "..yyy..",
        "yyyyyyy",
        "..yyy..",
        ".y.y.y.",
        "...y...",
    ],
    "ecran": [
        "bbbbbbb",
        "bkkkkkb",
        "bkkgkkb",
        "bkgkgkb",
        "bbbbbbb",
        "..bbb..",
    ],
    "outils": [
        "..ggg..",
        ".g...g.",
        "hhhhhhh",
        "hhhyhhh",
        "hhhhhhh",
    ],
    "journal": [
        "ooooooooo",
        "okkkkkkko",
        "okkbbbkko",
        "okkkkkkko",
        "okkkkkkko",
        "okwwwwkko",
        "okwwwwkko",
        "ooooooooo",
    ],
    "punaise": [
        "..ee..",
        ".hhhh.",
        "hehheh",
        ".hhhh.",
        "..ee..",
    ],
    "masquer": [
        "...g...",
        "...g...",
        ".g.g.g.",
        "..ggg..",
        "ggggggg",
    ],
    "croix": [
        "h...h",
        ".h.h.",
        "..h..",
        ".h.h.",
        "h...h",
    ],
}

def avec_contour(grille):
    """Ajoute un contour brun autour des pixels non transparents."""
    h, l = len(grille), len(grille[0])
    sortie = [["."] * (l + 2) for _ in range(h + 2)]
    for y in range(h):
        for x in range(l):
            sortie[y + 1][x + 1] = grille[y][x]
    res = [r[:] for r in sortie]
    for y in range(h + 2):
        for x in range(l + 2):
            if sortie[y][x] == ".":
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h + 2 and 0 <= xx < l + 2 and sortie[yy][xx] != ".":
                        res[y][x] = "o"
                        break
    return ["".join(r) for r in res]

def bulle_pensee(icone):
    """Bulle de pensée blanche autour d'une icône."""
    l = len(icone[0]) + 4
    lignes = ["k" * l] * 2
    lignes += ["kk" + r.replace(".", "k") + "kk" for r in icone]
    lignes += ["k" * l] * 2
    pad = "." * (l - 5)
    lignes += ["." * l, "." * l, "..kk." + pad, "..kk." + pad, "." * l, ".k" + "." * (l - 2)]
    return avec_contour(lignes)
# === SPRITES FIN ===

DEFS_RENARD = {   # nom -> (expression, queue, décalage vertical)
    "idle_a": ("normal", "a", 1), "idle_b": ("normal", "b", 1),
    "cligne": ("cligne", "b", 1),
    "content_a": ("content", "a", 1), "content_b": ("content", "b", 1),
    "triste_a": ("triste", "a", 1), "triste_b": ("triste", "b", 1),
    "mange_a": ("mange_a", "a", 1), "mange_b": ("mange_b", "b", 1),
}


def photo(grille, echelle=1, fond=FOND):
    """Transforme une grille de caractères en image Tk."""
    img = tk.PhotoImage(width=len(grille[0]), height=len(grille))
    lignes = ["{%s}" % " ".join(PALETTE.get(c, fond) if c != "." else fond for c in l)
              for l in grille]
    img.put(" ".join(lignes))
    return img.zoom(echelle, echelle) if echelle > 1 else img


# ------------------------------------------------------- intentions de Roux
# Le modèle ne sait pas émettre d'appel de fonction via ce backend : on reconnaît donc
# localement, sans réseau, ce que réclame la phrase. Une action part directement ; une
# demande de donnée est lue ici puis glissée dans un unique appel au modèle, pour qu'il
# réponde avec de vrais chiffres au lieu d'avouer qu'il ne peut pas.
# Les demandes de DONNÉES (météo, état du PC, carnet) ne passent plus par des mots-clés :
# c'est le modèle qui les réclame par appel d'outil (voir OUTILS dans roux_llm).
# Restent ici les actions instantanées, qui ne méritent pas un aller-retour réseau.
MOTS_CARESSE = ("caresse", "câlin", "calin", "gratouille", "grattouille")
MOTS_DORMIR = ("dors", "dormir", "sieste", "va te coucher", "fais dodo")
MOTS_POMODORO = ("pomodoro", "session de travail", "minuteur de travail")
MOTS_APPRENDRE = ("apprends-moi", "apprends moi", "apprend-moi", "apprends nous",
                  "apprends-moi quelque chose")
MOTS_RAPPEL = ("rappelle", "rappel", "préviens", "previens", "n'oublie pas", "oublie pas")
# « veille » avec des limites de mot, pour ne pas se déclencher sur « réveille »
MOTIF_VEILLE = re.compile(
    r"\bveille(?:s|r)?\b"
    r"|\bactualit[ée]s?\b"
    r"|\bactus?\b"
    r"|\bnews\b"
    r"|\bnouveaut[ée]s\b"
    r"|quoi de neuf"
    r"|qu[’']est-ce qu(?:i|'il) se passe"
    r"|\bles (?:derni[èe]res |petites )?nouvelles\b"
    r"|\b(?:les|des) (?:derni[èe]res |petites )?nouvelles "
    r"(?:tech|informatiques|du num[ée]rique|de la tech)\b"
    r"|\b(?:les|des) infos\b",
    re.I)
ALIMENTS_MOTS = {
    "poisson": ("poisson",),
    "souris": ("souris",),
    "oeuf": ("oeuf", "œuf"),
    "baies": ("baie", "baies"),
    "pomme": ("pomme",),
}


def lire_rappel(texte):
    """« rappelle-moi de sortir le linge dans 10 minutes » -> (10.0, 'sortir le linge')."""
    trouve = re.search(r"(\d+(?:[.,]\d+)?)\s*(min(?:ute)?s?|h(?:eure)?s?)\b", texte, re.I)
    if not trouve:
        return None
    valeur = float(trouve.group(1).replace(",", "."))
    if trouve.group(2).lower().startswith("h"):
        valeur *= 60
    reste = texte[:trouve.start()] + " " + texte[trouve.end():]
    for mot in ("rappelle-moi", "rappelle moi", "rappelle", "rappeler", "préviens-moi",
                "préviens", "previens", "n'oublie pas", "oublie pas", "de ", "d'", "du ",
                "dans ", "pour ", "vers ", "que ", "à ", "a "):
        reste = re.sub(re.escape(mot), " ", reste, flags=re.I)
    return max(0.1, min(600.0, valeur)), (" ".join(reste.split()).strip(" ,.:;!?«»")[:120]
                                          or "c'est l'heure !")


def detecter_action(texte):
    """Reconnaît une action demandée en langage naturel. Rend (outil, arguments) ou None."""
    minuscule = texte.lower()
    if MOTIF_VEILLE.search(minuscule):
        return "veille", {}
    if any(mot in minuscule for mot in MOTS_RAPPEL):
        rappel = lire_rappel(texte)
        if rappel:
            return "rappel", {"minutes": rappel[0], "texte": rappel[1]}
    if any(mot in minuscule for mot in MOTS_CARESSE):
        return "caresser", {}
    if any(mot in minuscule for mot in MOTS_DORMIR):
        return "dormir", {}
    if any(mot in minuscule for mot in MOTS_POMODORO):
        nombres = [int(n) for n in re.findall(r"\d{1,3}", texte)]
        arguments = {"travail": nombres[0]} if nombres else {}
        if len(nombres) > 1:
            arguments["pause"] = nombres[1]
        return "pomodoro", arguments
    if any(mot in minuscule for mot in MOTS_APPRENDRE):
        return "apprendre", {}
    for aliment, mots in ALIMENTS_MOTS.items():
        if any(mot in minuscule for mot in mots):
            return "nourrir", {"aliment": aliment}
    return None


# --------------------------------------- démarrage automatique (session Windows)
CLE_DEMARRAGE = "RouxCompagnon"
RUN_WINDOWS = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _lanceur():
    """pythonw.exe (pas de console qui clignote), sinon l'interpréteur courant."""
    exe = Path(sys.executable)
    sans_console = exe.with_name("pythonw.exe")
    return sans_console if sans_console.exists() else exe


def autostart_actif():
    """Roux est-il inscrit au démarrage de la session Windows ?"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_WINDOWS) as cle:
            winreg.QueryValueEx(cle, CLE_DEMARRAGE)
        return True
    except (OSError, ImportError):
        return False


def autostart_ecrire(actif):
    """Inscrit (ou retire) Roux au démarrage de la session Windows."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_WINDOWS) as cle:
        if actif:
            winreg.SetValueEx(cle, CLE_DEMARRAGE, 0, winreg.REG_SZ,
                              f'"{_lanceur()}" "{Path(__file__).resolve()}" --tray')
        else:
            try:
                winreg.DeleteValue(cle, CLE_DEMARRAGE)
            except FileNotFoundError:
                pass


# ------------------------------------------------------------ instance unique
VERROU_INSTANCE = None      # le handle doit vivre aussi longtemps que le processus


def verrou_instance():
    """Rend True si aucune autre instance ne tourne. Mutex Windows nommé : libéré à la mort."""
    global VERROU_INSTANCE
    try:
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        VERROU_INSTANCE = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\RouxCompagnon")
        if not VERROU_INSTANCE:
            return True                      # pas de verrou possible : on laisse démarrer
        return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:
        return True


def rendre_net():
    """Déclare le processus DPI-aware.

    Sans ça, Windows étire la fenêtre pour compenser la mise à l'échelle de l'écran :
    chaque pixel du renard est rééchantillonné et bave. En étant DPI-aware, le rendu
    matriciel reste 1 pixel pour 1 pixel physique.
    """
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)     # Windows 8.1 et suivants
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()          # Vista / 7
    except Exception:
        pass                                                   # rien de vital : on continue


# ------------------------------------------- API locale (pour le serveur MCP)
FICHIER_API = Path(__file__).with_name("roux_api.json")
PORT_API = 8765


def servir_api(app, port=PORT_API):
    """Expose Roux sur 127.0.0.1, pour que le serveur MCP puisse le piloter."""
    jeton = secrets.token_hex(16)

    class Gestionnaire(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _repondre(self, code, donnees):
            corps = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def _autorise(self):
            if self.headers.get("X-Jeton") == jeton:
                return True
            self._repondre(403, {"erreur": "jeton invalide"})
            return False

        def do_GET(self):
            if self.path != "/etat":
                self._repondre(404, {"erreur": "chemin inconnu"})
            elif self._autorise():
                self._repondre(200, app.sur_fil_tk(app.etat_public))

        def do_POST(self):
            if not self._autorise():
                return
            try:
                taille = int(self.headers.get("Content-Length") or 0)
                demande = json.loads(self.rfile.read(taille) or b"{}")
            except (TypeError, ValueError, OSError):
                return self._repondre(400, {"erreur": "requête illisible"})
            if not isinstance(demande, dict):
                demande = {}
            outil = str(demande.get("outil", ""))
            arguments = demande.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            self._repondre(200, app.sur_fil_tk(lambda: app.api_action(outil, arguments)))

    try:
        serveur = ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire)
    except OSError:
        return None
    serveur.daemon_threads = True
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    try:
        FICHIER_API.write_text(json.dumps({"port": port, "jeton": jeton}), encoding="utf-8")
    except OSError:
        pass
    return serveur


# ------------------------------------------------------------ l'application
class Compagnon:
    def __init__(self, demarrage_masque=False):
        self.etat = Etat.charger()
        self.fen = TkinterDnD.Tk() if DND_DISPONIBLE else tk.Tk()
        self.fen.title(self.etat.nom)
        self.fen.overrideredirect(True)                  # pas de barre de titre
        self.fen.attributes("-topmost", True)
        self.fen.attributes("-transparentcolor", "black")
        self.fen.config(bg="black")
        self.canvas = tk.Canvas(self.fen, width=CW, height=CH, bg="black",
                                highlightthickness=0, bd=0)
        self.canvas.pack()

        self.preparer_images()
        # le renard pose ses pattes sur le bas de sa fenêtre. L'ancien décalage de 4 pixels
        # n'était calibré que pour l'échelle 4 : aux autres tailles il lui rognait le bas.
        self.item_renard = self.canvas.create_image(CW // 2, CH, anchor="s")
        self.item_icone = self.canvas.create_image(CW // 2, int(30 * DECALAGE_ICONE),
                                                   anchor="center", state="hidden")
        self.item_punaise = self.canvas.create_image(-40, CH - 2, anchor="s",
                                                     image=self.icones["punaise"],
                                                     state="hidden")

        gauche, _haut, droite, bas = self.zone_travail()
        self.aire = (gauche, droite, bas)
        self.x = random.randint(gauche, max(gauche, droite - CW))
        self.y = bas - CH

        self.mode = "idle"            # idle, left, right, sleep, eat, happy, chasse, saut
        self.fin_mode = 0.0
        self.punaise = None           # bestiole qu'il poursuit tout seul
        self.saut = 0                 # hauteur du bond, en pixels
        self.regard_gauche = False
        self.aliment = "poisson"
        self.tic = 0
        self.cligne = 0
        self.glisse = False
        self.press = (0, 0)
        self.dx = self.dy = 0
        self.alerte_faim = False
        self.pomo = None             # pomodoro en cours
        self.sys = Systeme()
        self.alertes = {}            # alerte -> dernier envoi
        self.compte_cpu = 0
        self.derniere_geo = ""

        self.panneau = None
        self.panneau_minuteur = None
        self.bulle = None
        self.chat = None
        self.texte_chat = None
        self.saisie = None
        self.attente = False
        self.attente_marque = False
        self.theme_en_cours = ""
        self.file = queue.Queue()     # résultats des tâches de fond
        self.tours = 0                # échanges, pour espacer l'extraction de souvenirs
        self.memoire_en_cours = False
        self.liens_veille = []        # liens de la veille en cours
        self.reponse_vue = None       # affichage progressif en cours
        self.dernier_morceau = 0.0
        self.serveur_api = None       # API locale pour MCP / Command Code
        self.masque_manuel = False    # caché volontairement par l'utilisateur
        self.masque_auto = False      # caché parce qu'un plein écran le recouvre
        self.tray_icon = None         # icône de la barre de notification

        self.canvas.bind("<ButtonPress-1>", self.debut_glisser)
        self.canvas.bind("<B1-Motion>", self.glisser)
        self.canvas.bind("<ButtonRelease-1>", self.relacher)
        self.canvas.bind("<Button-3>", lambda e: self.basculer_panneau())
        if DND_DISPONIBLE:
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind("<<Drop>>", self.fichier_depose)

        self.fen.after(30_000, self.cycle_stats)
        if PAUSE_MIN > 0:
            self.fen.after(PAUSE_MIN * 60_000, self.rappel_pause)
        if SUGGESTION_MIN > 0:
            self.fen.after(SUGGESTION_MIN * 60_000, self.suggestion_auto)
        if REGLAGES.get("veille_heure"):
            self.fen.after(20_000, self.verifier_veille_auto)
        if self.etat.nouveau:
            self.fen.after(800, lambda: self.dire(
                f"Salut ! Moi c'est {self.etat.nom}. Clique sur moi pour voir ce qu'on peut faire ensemble !", 9000))
        self.serveur_api = servir_api(self)
        self.boucle()
        self.fen.after(2000, self.verifier_plein_ecran)
        if demarrage_masque:
            self.fen.after(100, self.masquer)

    # ------------------------------------------------------------- images
    def preparer_images(self):
        self.images = {}
        for miroir in (False, True):
            for cle, (expr, queue_, dec) in DEFS_RENARD.items():
                self.images[(cle, miroir)] = photo(grille_renard(expr, queue_, dec, miroir), ECHELLE)
            for i in range(4):
                self.images[(f"marche_{i}", miroir)] = photo(grille_marche(i, miroir), ECHELLE)
            self.images[("dort_a", miroir)] = photo(grille_sommeil(False, miroir), ECHELLE)
            self.images[("dort_b", miroir)] = photo(grille_sommeil(True, miroir), ECHELLE)
        self.icones = {nom: photo(avec_contour(g), ECHELLE_ICONE) for nom, g in ICONES.items()}
        self.icones["bulle_faim"] = photo(bulle_pensee(ICONES["poisson"]), ECHELLE_ICONE)
        self.icones["renard"] = photo(avec_contour(grille_renard("normal", "a", 1)), 2,
                                      FOND_UI)      # sur le fond du panneau : pas de carré noir
        self.icones_btn = {nom: photo(avec_contour(g), 2, CREME) for nom, g in ICONES.items()}

    def zone_travail(self):
        """Zone de l'écran hors barre des tâches (Windows)."""
        try:
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)
            return r.left, r.top, r.right, r.bottom
        except Exception:
            return 0, 0, self.fen.winfo_screenwidth(), self.fen.winfo_screenheight() - 50

    # ------------------------------------------------------ boucle principale
    @property
    def occupe(self):
        return self.glisse or self.chat is not None or self.panneau is not None

    def phase(self, ms, n=2):
        """Numéro d'image (0 à n-1) qui change toutes les `ms` millisecondes."""
        return (self.tic * TICK_MS // ms) % n

    def boucle(self):
        try:
            self.tic += 1
            self.traiter_file()
            if self.tic % 16 == 0:
                self.verifier_pomodoro()
            self.gerer_comportement()
            self.deplacer()
            self.dessiner()
        finally:
            self.fen.after(TICK_MS, self.boucle)

    def gerer_comportement(self):
        maintenant = time.time()
        if self.mode == "sleep":
            if maintenant > self.fin_mode and self.etat.energie >= 90:
                self.reveiller("naturel")
            return
        self.animer_punaise()
        if self.mode == "saut":
            # parabole : le saut monte puis redescend en douceur, sans paliers visibles
            reste = self.fin_mode - maintenant
            progression = 1.0 - max(0.0, min(1.0, reste / DUREE_SAUT))
            self.saut = int(HAUTEUR_SAUT * math.sin(progression * math.pi))
            if reste <= 0:
                self.saut = 0
                self.attraper()
            return
        if self.punaise is not None and not self.occupe and self.etat.energie > 20:
            if maintenant > self.fin_mode:      # elle finit toujours par filer
                self.punaise = None
                self.mode, self.fin_mode = "idle", maintenant + 2
            else:
                self.mode = "chasse"
                return
        if self.mode == "chasse":
            self.mode, self.fin_mode = "idle", maintenant + 2
        if self.etat.energie < 15 and not self.occupe:
            self.endormir(60, auto=True)
            return
        if self.occupe:
            if self.mode in ("left", "right"):
                self.mode = "idle"
            return
        if maintenant >= self.fin_mode:
            concentre = self.pomo is not None and self.pomo["phase"] == "travail"
            poids = [1, 0, 0] if concentre else [3, 2, 2]     # sage pendant que tu travailles
            self.mode = random.choices(["idle", "left", "right"], weights=poids)[0]
            self.fin_mode = maintenant + random.uniform(2, 6)

    def deplacer(self):
        gauche, droite, bas = self.aire
        if self.mode == "left":
            self.x -= 3                      # 50 px/s : les pieds ne glissent pas
            self.regard_gauche = True
        elif self.mode == "right":
            self.x += 3
            self.regard_gauche = False
        elif self.mode == "chasse" and self.punaise is not None:
            ecart = self.punaise["x"] - (self.x + CW // 2)
            if abs(ecart) > 16:
                pas = ecart * 0.15                      # accélère de loin, freine en approchant
                if abs(pas) < 2.0:
                    pas = 2.0 if ecart > 0 else -2.0    # vitesse plancher : sinon il stagne
                self.x += pas
                self.regard_gauche = ecart < 0
            else:
                self.mode, self.fin_mode = "saut", time.time() + DUREE_SAUT
        if self.x <= gauche:
            self.x = gauche
            if self.mode == "left":
                self.mode = "right"
        elif self.x >= droite - CW:
            self.x = droite - CW
            if self.mode == "right":
                self.mode = "left"
        if not self.glisse:
            self.y = bas - CH
        geo = f"{CW}x{CH}+{int(self.x)}+{int(self.y)}"
        if geo != self.derniere_geo:
            self.fen.geometry(geo)
            self.derniere_geo = geo
            if self.bulle is not None:          # la bulle suit le renard
                self.placer_bulle(self.bulle)

    def dessiner(self):
        m, faim = self.mode, self.etat.faim < 25
        d = DECALAGE_ICONE
        icone, pos = None, (CW // 2, int(30 * d))
        if m == "sleep":
            p = self.phase(1200)
            cle = "dort_a" if p == 0 else "dort_b"
            icone, pos = "zzz", (CW // 2 + int(30 * d), int(68 * d) - p * 3)
        elif m == "eat":
            p = self.phase(240)
            cle = "mange_a" if p == 0 else "mange_b"
            icone, pos = self.aliment, (CW // 2, int(112 * d) - p * 3)
        elif m == "happy":
            p = self.phase(480)
            cle = "content_a" if p == 0 else "content_b"
            icone, pos = "coeur", (CW // 2 + (int(22 * d) if p else -int(22 * d)), int(34 * d))
        elif m in ("left", "right", "chasse", "saut"):
            cle = f"marche_{self.phase(240, 4)}"      # une foulée = 4 images
        else:
            q = "a" if self.phase(600) == 0 else "b"
            if faim or self.etat.humeur < 25:
                cle = "triste_" + q
            elif self.cligne > 0:
                cle = "cligne"
                self.cligne -= 1
            else:
                cle = "idle_" + q
                if random.random() < 0.015:
                    self.cligne = 3
        if icone is None and faim and m != "sleep":
            icone, pos = "bulle_faim", (CW // 2 + int(36 * d), int(26 * d))
        self.canvas.itemconfig(self.item_renard, image=self.images[(cle, self.regard_gauche)])
        self.canvas.coords(self.item_renard, CW // 2, CH - self.saut)   # bond pendant la chasse
        if self.punaise is not None:
            self.canvas.coords(self.item_punaise, int(self.punaise["x"] - self.x), CH - 2)
            self.canvas.itemconfig(self.item_punaise, state="normal")
        else:
            self.canvas.itemconfig(self.item_punaise, state="hidden")
        if icone is None:
            self.canvas.itemconfig(self.item_icone, state="hidden")
        else:
            self.canvas.itemconfig(self.item_icone, image=self.icones[icone], state="normal")
            self.canvas.coords(self.item_icone, *pos)

    # -------------------------------------------------------- jeu autonome
    def animer_punaise(self):
        """La bestiole grouille sur le sol et détale dès qu'il approche."""
        if self.punaise is None:
            return
        gauche, droite, _bas = self.aire
        p = self.punaise
        ecart = (self.x + CW // 2) - p["x"]
        if abs(ecart) < 130:
            p["x"] -= 3 if ecart > 0 else -3
        elif random.random() < 0.05:
            p["sens"] = -p["sens"]
        p["x"] += p["sens"]
        p["x"] = max(gauche + 4, min(droite - 14, p["x"]))

    def provoquer_punaise(self):
        """Fait naître une bestiole assez loin pour qu'il y ait une vraie course."""
        if self.punaise is not None or self.occupe:
            return
        gauche, droite, _bas = self.aire
        x = random.uniform(gauche + 12, droite - 24)
        for _ in range(12):
            if abs(x - (self.x + CW // 2)) > 160:
                break
            x = random.uniform(gauche + 12, droite - 24)
        self.punaise = {"x": x, "sens": random.choice((-1, 1))}
        self.mode, self.fin_mode = "chasse", time.time() + 25

    def attraper(self):
        """Bond réussi : il savoure, sa malice monte."""
        self.saut = 0
        self.punaise = None
        self.mode, self.fin_mode = "happy", time.time() + 2.5
        self.etat.malice = min(100.0, self.etat.malice + 4)
        self.etat.humeur = min(100.0, self.etat.humeur + 5)
        self.etat.sauver()
        self.dire(random.choice(PHRASES_CHASSE))

    # --------------------------------------------------------- états de vie
    def endormir(self, duree_s, auto):
        self.mode = "sleep"
        self.fin_mode = time.time() + duree_s
        self.dire("*bâille* Je suis crevé... je fais une sieste." if auto
                  else "Bonne nuit... *se roule en boule*")

    def reveiller(self, cause):
        self.mode, self.fin_mode = "idle", time.time() + 2
        if cause == "clic":
            self.etat.humeur = max(0.0, self.etat.humeur - 2)
            self.dire("Mmh... déjà ? Encore cinq minutes...")
        else:
            self.dire("*s'étire* Ahh, bien dormi !")

    def cycle_stats(self):
        self.etat.evoluer(0.5, self.mode == "sleep")
        if self.etat.faim < 25 and not self.alerte_faim:
            self.alerte_faim = True
            if self.mode != "sleep":
                self.dire("J'ai faim... *regarde ton clavier d'un air suppliant*")
        elif self.etat.faim >= 40:
            self.alerte_faim = False
        self.verifier_systeme()
        # de temps en temps, il se distrait tout seul : plus il a de malice, plus souvent
        if (self.punaise is None and self.mode not in ("sleep",) and self.etat.energie > 40
                and random.random() < 0.04 + self.etat.malice / 2500):
            self.provoquer_punaise()
        self.etat.sauver()
        self.fen.after(30_000, self.cycle_stats)

    def alerte(self, nom, texte, delai_min=30):
        maintenant = time.time()
        if maintenant - self.alertes.get(nom, 0) >= delai_min * 60:
            self.alertes[nom] = maintenant
            if self.mode != "sleep":
                self.dire(texte)

    def verifier_systeme(self):
        if not ALERTES_SYSTEME:
            return
        cpu = self.sys.cpu_depuis_dernier()
        self.compte_cpu = self.compte_cpu + 1 if cpu is not None and cpu > 90 else 0
        if self.compte_cpu >= 4:             # 4 mesures de 30 s = 2 minutes
            self.alerte("cpu", "Ton processeur est à fond depuis 2 minutes... Une appli s'emballe ?")
        mem = self.sys.memoire()
        if mem and mem[0] > 92:
            self.alerte("ram", f"La mémoire est presque pleine ({mem[0]:.0f} %). Ferme quelques applis ?")
        bat = self.sys.batterie()
        if bat and bat[0] <= 15 and not bat[1]:
            self.alerte("batterie", f"Batterie à {bat[0]} %... Branche ton chargeur !", 10)
        dsk = self.sys.disque()
        if dsk and dsk[0] > 92:
            self.alerte("disque", f"Ton disque est plein à {dsk[0]:.0f} %. Un petit ménage s'impose !", 240)

    def rappel_pause(self):
        if self.pomo is None:                 # le pomodoro gère déjà les pauses
            self.dire("Petite pause ! Lève-toi, étire-toi et bois un verre d'eau.")
        self.fen.after(PAUSE_MIN * 60_000, self.rappel_pause)

    def suggestion_auto(self):
        if (self.mode != "sleep" and not self.occupe and not self.attente and backend_pret()):
            self.lecon(None)
        self.fen.after(SUGGESTION_MIN * 60_000, self.suggestion_auto)

    # --------------------------------------------------------------- souris
    def debut_glisser(self, e):
        self.press = (e.x_root, e.y_root)
        self.dx, self.dy = e.x_root - self.x, e.y_root - self.y
        self.glisse = False

    def glisser(self, e):
        if not self.glisse:
            if max(abs(e.x_root - self.press[0]), abs(e.y_root - self.press[1])) < 4:
                return
            self.fermer_panneau()
        self.glisse = True
        self.x, self.y = e.x_root - self.dx, e.y_root - self.dy
        self.fen.geometry(f"{CW}x{CH}+{int(self.x)}+{int(self.y)}")

    def relacher(self, _e):
        if self.glisse:
            self.glisse = False
            gauche, droite, bas = self.aire
            self.x = min(max(gauche, int(self.x)), droite - CW)
            self.y = bas - CH
            if self.chat is not None:
                self.placer(self.chat)
        elif self.mode == "sleep":
            self.reveiller("clic")
        else:
            self.basculer_panneau()

    # ---------------------------------------------------- dépôt de fichier
    def chemins_deposes(self, donnees):
        """Découpe proprement une liste de chemins (« {C:/a b/c.txt} C:/d.txt »).

        Tk sait faire ce découpage (accolades, guillemets, espaces) : autant lui demander.
        """
        try:
            return [chemin for chemin in self.fen.tk.splitlist(donnees) if chemin]
        except tk.TclError:
            return [donnees] if donnees else []

    def fichier_depose(self, evenement):
        """Un fichier lâché sur le renard : on le lit, on demande un diagnostic."""
        chemins = self.chemins_deposes(str(getattr(evenement, "data", "")))
        # Tk découpe sur les espaces : un chemin non protégé par des accolades arrive en
        # morceaux. Si le tout recollé existe, c'est que c'était un seul fichier.
        if len(chemins) > 1:
            recolle = " ".join(chemins)
            if Path(recolle).exists():
                chemins = [recolle]
        if not chemins:
            self.dire("Je n'ai pas reconnu le fichier déposé.")
            return
        fichier = Path(chemins[0])
        try:
            contenu = fichier.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError as err:
            self.dire(f"Je n'ai pas pu lire {fichier.name} ({err}).")
            return
        self.ouvrir_chat()
        self.ecrire("info", f"Déposé : {fichier.name} — {len(contenu)} caractères lus.")
        self.etat.historique.append({"role": "user",
                                     "content": f"[fichier déposé] {fichier.name}"})
        self.etat.nettoyer_historique()
        invite = ("Analyse ce fichier de log / cette configuration système et donne-moi un "
                  "diagnostic express : ce qui cloche, ce que ça implique, et quoi faire. "
                  "Va droit au but, en français.\n\n"
                  f"--- {fichier.name} ---\n{contenu}")
        self.demander(self.systeme() + "\n\n" + invite, self.reponse_conversation, 500)

    # -------------------------------------------------- fenêtres sans bordure
    def placer(self, fenetre):
        """Pose une fenêtre au-dessus du renard."""
        fenetre.update_idletasks()
        w, h = fenetre.winfo_reqwidth(), fenetre.winfo_reqheight()
        gauche, droite, _bas = self.aire
        x = min(max(gauche, int(self.x + CW // 2 - w // 2)), max(gauche, droite - w))
        y = max(0, self.y + int(50 * DECALAGE_ICONE) - h)     # juste au-dessus de sa tête
        fenetre.geometry(f"+{x}+{y}")

    def fenetre_flottante(self, remplissage=8):
        f = tk.Toplevel(self.fen)
        f.overrideredirect(True)
        f.attributes("-topmost", True)
        cadre = tk.Frame(f, bg=ACCENT_UI, padx=2, pady=2)
        cadre.pack()
        corps = tk.Frame(cadre, bg=CREME, padx=remplissage + 2, pady=remplissage)
        corps.pack()
        return f, corps

    # ------------------------------------------------------- bulles (comics)
    def fermer_bulle(self, bulle=None):
        bulle = bulle or self.bulle
        if bulle is not None:
            try:
                bulle["fenetre"].destroy()
            except (tk.TclError, KeyError, TypeError):
                pass
            if bulle is self.bulle:
                self.bulle = None

    def bulle_comics(self, texte=""):
        """Crée une bulle de bande dessinée : fond détouré, corps arrondi, queue vers le renard."""
        fenetre = tk.Toplevel(self.fen)
        fenetre.overrideredirect(True)
        fenetre.attributes("-topmost", True)
        fenetre.attributes("-transparentcolor", FOND)
        fenetre.config(bg=FOND)
        canevas = tk.Canvas(fenetre, bg=FOND, highlightthickness=0, bd=0)
        canevas.pack()
        item = canevas.create_text(0, 0, anchor="nw", text=texte or "…",
                                   width=LARGEUR_BULLE, font=("Segoe UI", 10), fill=BRUN)
        bulle = {"fenetre": fenetre, "canevas": canevas, "item": item, "geo": None}
        fenetre.bind("<Button-1>", lambda e, b=bulle: self.fermer_bulle(b))
        self.dessiner_bulle(bulle)
        return bulle

    def dessiner_bulle(self, bulle, texte=None):
        """Redessine la forme autour du texte : elle grandit pendant que la réponse s'écrit."""
        canevas, item = bulle["canevas"], bulle["item"]
        if texte is not None:
            canevas.itemconfig(item, text=texte or "…")
        boite = canevas.bbox(item)
        if not boite:
            return
        largeur, hauteur = boite[2] - boite[0], boite[3] - boite[1]
        marge, rayon, queue = 12, 11, 12
        canevas.coords(item, marge + 5, marge + 4)
        gauche, haut = marge, marge
        droite, bas = marge + largeur + 10, marge + hauteur + 9
        milieu = (gauche + droite) / 2
        canevas.delete("forme")
        # le fond : deux rectangles et quatre disques, sans lissage donc sans débordement
        for x0, y0, x1, y1 in ((gauche + rayon, haut, droite - rayon, bas),
                               (gauche, haut + rayon, droite, bas - rayon)):
            canevas.create_rectangle(x0, y0, x1, y1, fill=CREME, outline="", tags="forme")
        for x0, y0 in ((gauche, haut), (droite - 2 * rayon, haut),
                       (gauche, bas - 2 * rayon), (droite - 2 * rayon, bas - 2 * rayon)):
            canevas.create_oval(x0, y0, x0 + 2 * rayon, y0 + 2 * rayon,
                                fill=CREME, outline="", tags="forme")
        # la queue : fond puis les deux flancs, pour ne pas tirer un trait dans la bulle
        canevas.create_polygon((milieu - 9, bas - 2), (milieu + 9, bas - 2), (milieu, bas + queue),
                               fill=CREME, outline="", tags="forme")
        for cote in (-9, 9):
            canevas.create_line(milieu + cote, bas - 2, milieu, bas + queue,
                                fill=ACCENT_UI, width=2, tags="forme")
        # le contour : quatre arcs et cinq traits, le bas étant interrompu par la queue
        for x0, y0, depart in ((gauche, haut, 90), (droite - 2 * rayon, haut, 0),
                               (gauche, bas - 2 * rayon, 180),
                               (droite - 2 * rayon, bas - 2 * rayon, 270)):
            canevas.create_arc(x0, y0, x0 + 2 * rayon, y0 + 2 * rayon, start=depart,
                               extent=90, style="arc", outline=ACCENT_UI, width=2,
                               tags="forme")
        canevas.create_line(gauche + rayon, haut, droite - rayon, haut,
                            fill=ACCENT_UI, width=2, tags="forme")
        canevas.create_line(gauche, haut + rayon, gauche, bas - rayon,
                            fill=ACCENT_UI, width=2, tags="forme")
        canevas.create_line(droite, haut + rayon, droite, bas - rayon,
                            fill=ACCENT_UI, width=2, tags="forme")
        canevas.create_line(gauche + rayon, bas, milieu - 8, bas, fill=ACCENT_UI,
                            width=2, tags="forme")
        canevas.create_line(milieu + 8, bas, droite - rayon, bas, fill=ACCENT_UI,
                            width=2, tags="forme")
        canevas.tag_lower("forme")
        canevas.config(width=int(droite + marge), height=int(bas + queue + marge))
        self.placer_bulle(bulle)

    def placer_bulle(self, bulle):
        """Pose la bulle juste au-dessus de sa tête, queue comprise."""
        fenetre = bulle["fenetre"]
        fenetre.update_idletasks()
        largeur, hauteur = fenetre.winfo_reqwidth(), fenetre.winfo_reqheight()
        gauche, droite, _bas = self.aire
        tete = self.y + CH - HAUT * ECHELLE
        x = min(max(gauche, int(self.x + CW // 2 - largeur // 2)),
                max(gauche, droite - largeur))
        y = max(0, int(tete - hauteur + 8))
        geo = f"+{x}+{y}"
        if geo != bulle["geo"]:                 # on ne repositionne que si ça a bougé
            fenetre.geometry(geo)
            bulle["geo"] = geo

    # ------------------------------------------------- réponse au fil de l'eau
    def morceau(self, texte):
        """Appelée depuis le fil de fond à chaque morceau reçu : on repasse par le fil Tk."""
        maintenant = time.time()
        if maintenant - self.dernier_morceau < 0.06:
            return                                # on ne redessine pas plus vite que l'œil
        self.dernier_morceau = maintenant
        self.file.put((self.afficher_morceau, texte))

    def afficher_morceau(self, texte):
        """Met à jour la réponse en cours d'écriture (fil de l'interface)."""
        if self.reponse_vue is None:
            self.ouvrir_reponse()
        vue = self.reponse_vue
        vue["texte"] = texte
        if vue["marque"] is not None and self.texte_chat is not None:
            t = self.texte_chat
            t.config(state="normal")
            t.delete(vue["marque"], "end-1c")
            t.insert(vue["marque"], texte)
            t.see("end")
            t.config(state="disabled")
        elif vue["bulle"] is not None:
            self.dessiner_bulle(vue["bulle"], texte)

    def ouvrir_reponse(self):
        """Prépare l'affichage progressif : une ligne de chat, ou une bulle dédiée."""
        self.retirer_attente()
        if self.chat is not None:
            t = self.texte_chat
            t.config(state="normal")
            t.insert("end", f"{self.etat.nom} : ", "nom_renard")
            t.mark_set("flux", "end-1c")
            t.mark_gravity("flux", "left")
            t.config(state="disabled")
            self.reponse_vue = {"texte": "", "marque": "flux", "etiquette": None, "bulle": None}
            return
        self.fermer_bulle()
        self.bulle = self.bulle_comics("…")
        self.reponse_vue = {"texte": "", "marque": None, "bulle": self.bulle}

    def dire(self, texte, duree=None):
        """Le renard dit quelque chose : dans le chat s'il est ouvert, sinon dans une bulle."""
        vue, self.reponse_vue = self.reponse_vue, None
        if vue is not None:
            self.terminer_flux(vue, texte, duree)     # la réponse était déjà commencée
            return
        if self.chat is not None:
            self.ecrire("renard", texte)
            return
        self.fermer_bulle()
        bulle = self.bulle_comics(texte)
        self.bulle = bulle
        bulle["fenetre"].after(duree or max(4000, 70 * len(texte)),
                               lambda: self.fermer_bulle(bulle))

    def terminer_flux(self, vue, texte, duree=None):
        """Achève une réponse commencée au fil de l'eau : texte final, puis retour à la ligne."""
        texte = texte or ""
        if vue["marque"] is not None and self.texte_chat is not None:
            t = self.texte_chat
            t.config(state="normal")
            t.delete(vue["marque"], "end-1c")         # on remplace ce qui a été écrit en direct
            t.insert(vue["marque"], texte + "\n")
            t.see("end")
            t.config(state="disabled")
            return
        bulle = vue["bulle"]
        if bulle is not None:
            self.dessiner_bulle(bulle, texte)
            bulle["fenetre"].after(duree or max(4000, 70 * len(texte)),
                                   lambda: self.fermer_bulle(bulle))

    # --------------------------------------------------------------- panneau
    def basculer_panneau(self):
        if self.chat is not None:
            self.fermer_chat()
        elif self.panneau is not None:
            self.fermer_panneau()
        else:
            self.ouvrir_panneau()

    def fermer_panneau(self):
        if self.panneau_minuteur is not None:
            self.fen.after_cancel(self.panneau_minuteur)
            self.panneau_minuteur = None
        if self.panneau is not None:
            try:
                self.panneau.destroy()
            except tk.TclError:
                pass
            self.panneau = None

    @staticmethod
    def barre_arrondie(canvas, largeur, hauteur, valeur, couleur, fond=FILET_UI):
        """Une jauge au fond arrondi : deux demi-cercles et un rectangle, dessinés sous la valeur."""
        rayon = hauteur / 2
        for gauche in (0, largeur - hauteur):
            canvas.create_oval(gauche, 0, gauche + hauteur, hauteur, fill=fond, outline="")
        canvas.create_rectangle(rayon, 0, largeur - rayon, hauteur, fill=fond, outline="")
        part = max(0.0, min(1.0, valeur / 100.0))
        if part <= 0:
            return
        bout = max(hauteur, part * largeur)
        canvas.create_oval(0, 0, hauteur, hauteur, fill=couleur, outline="")
        canvas.create_oval(bout - hauteur, 0, bout, hauteur, fill=couleur, outline="")
        canvas.create_rectangle(rayon, 0, max(rayon, bout - rayon), hauteur,
                                fill=couleur, outline="")

    def ouvrir_panneau(self, page="principal"):
        self.fermer_panneau()
        self.fermer_bulle()
        p, corps = self.fenetre_flottante()
        e = self.etat
        jours = e.age_jours()
        entete = tk.Frame(corps, bg=CREME)
        entete.pack(fill="x", pady=(0, 7))
        tk.Label(entete, image=self.icones["renard"], bg=CREME).pack(side="left")
        colonne = tk.Frame(entete, bg=CREME)
        colonne.pack(side="left", padx=(9, 0))
        tk.Label(colonne, text=e.nom, bg=CREME, fg=ORANGE,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(colonne, text=f"{jours} jour{'s' if jours > 1 else ''} · "
                               f"{e.pomodoros} pomodoro{'s' if e.pomodoros > 1 else ''}",
                 bg=CREME, fg=SOURDINE_UI, font=("Segoe UI", 8)).pack(anchor="w")
        for libelle, valeur, couleur in (("Satiété", e.faim, "#e8772e"),
                                         ("Énergie", e.energie, "#58b368"),
                                         ("Humeur", e.humeur, "#ff4d6d"),
                                         ("Malice", e.malice, ACCENT_UI)):
            ligne = tk.Frame(corps, bg=CREME)
            ligne.pack(fill="x", pady=2)
            tk.Label(ligne, text=libelle, width=8, anchor="w", bg=CREME, fg=BRUN,
                     font=("Segoe UI", 9)).pack(side="left")
            barre = tk.Canvas(ligne, width=104, height=11, bg=CREME,
                              highlightthickness=0, bd=0)
            barre.pack(side="left")
            self.barre_arrondie(barre, 104, 11, valeur, couleur)
        tk.Frame(corps, bg=FILET_UI, height=1).pack(fill="x", pady=(8, 5))
        if self.pomo:
            tk.Label(corps, text="Pomodoro : " + self.texte_pomodoro(), bg=CREME, fg=ORANGE,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
        if page == "principal":
            actions = [
                ("Nourrir", "poisson", lambda: self.ouvrir_panneau("nourrir")),
                ("Caresser", "coeur", self.action_caresser),
                ("Parler", "bulle", self.action_parler),
                ("Apprendre", "livre", self.action_apprendre),
                ("Dormir", "lune", self.action_dormir),
                ("Masquer", "masquer", self.action_masquer),
                ("Outils", "outils", lambda: self.ouvrir_panneau("outils")),
                ("Quitter", "croix", self.quitter),
            ]
        elif page == "outils":
            actions = [
                ("Arrêter le pomodoro" if self.pomo else "Pomodoro", "tomate", self.action_pomodoro),
                ("Météo", "soleil", self.action_meteo),
                ("État du PC", "ecran", self.action_systeme),
                ("Veille info", "journal", self.action_veille),
                ("Démarrage auto : " + ("oui" if autostart_actif() else "non"),
                 None, self.action_demarrage),
                ("Retour", None, lambda: self.ouvrir_panneau("principal")),
            ]
        else:
            tk.Label(corps, text="Que lui donner ?", bg=CREME, fg=BRUN,
                     font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(0, 2))
            actions = [(a["nom"], cle, (lambda c=cle: self.action_nourrir(c)))
                       for cle, a in ALIMENTS.items()]
            actions.append(("Retour", None, lambda: self.ouvrir_panneau("principal")))
        for texte, icone, commande in actions:
            b = tk.Button(corps, text=" " + texte, image=self.icones_btn[icone] if icone else "",
                          compound="left", anchor="w", bg=CREME, activebackground=CREME,
                          fg=BRUN, activeforeground=ORANGE, relief="flat", bd=0, padx=4, pady=2,
                          cursor="hand2", font=("Segoe UI", 10), command=commande)
            b.pack(fill="x")
            b.bind("<Enter>", lambda ev, w=b: w.config(fg=ORANGE))
            b.bind("<Leave>", lambda ev, w=b: w.config(fg=BRUN))
        if page == "principal":
            tk.Label(corps, text="Glisse-moi pour me déplacer · dépose un fichier pour un "
                                 "diagnostic · /aide", bg=CREME, fg=SOURDINE_UI,
                     font=("Segoe UI", 7), wraplength=205,
                     justify="left").pack(anchor="w", pady=(7, 0))
        self.panneau = p
        self.placer(p)
        self.panneau_minuteur = self.fen.after(15_000, self.fermer_panneau)

    def action_caresser(self):
        self.fermer_panneau()
        self.etat.caresser()
        self.mode, self.fin_mode = "happy", time.time() + 3
        self.dire(random.choice(PHRASES_CARESSE))

    def action_nourrir(self, cle):
        self.fermer_panneau()
        ok, phrase = self.etat.donner(cle)
        if ok:
            self.mode, self.fin_mode, self.aliment = "eat", time.time() + 3.5, cle
        self.dire(phrase)

    def action_parler(self):
        self.fermer_panneau()
        self.ouvrir_chat()

    def action_apprendre(self):
        self.fermer_panneau()
        self.ouvrir_chat()
        self.lecon(None)

    def action_dormir(self):
        self.fermer_panneau()
        self.endormir(120, auto=False)

    def action_masquer(self):
        self.fermer_panneau()
        self.masquer()

    def quitter(self):
        self.etat.sauver()
        arreter_agent()
        if self.serveur_api is not None:
            self.serveur_api.shutdown()
            self.serveur_api = None
            try:
                FICHIER_API.unlink()
            except OSError:
                pass
        self.fen.destroy()

    # ------------------------------------------------------------------ chat
    def ouvrir_chat(self):
        if self.chat is not None:
            return
        self.fermer_panneau()
        self.fermer_bulle()
        c, corps = self.fenetre_flottante(6)
        entete = tk.Frame(corps, bg=CREME)
        entete.pack(fill="x")
        tk.Label(entete, image=self.icones["renard"], bg=CREME).pack(side="left")
        tk.Label(entete, text=self.etat.nom, bg=CREME, fg=ORANGE,
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=(7, 0))
        croix = tk.Label(entete, text="✕", bg=CREME, fg=BRUN, cursor="hand2",
                         font=("Segoe UI", 11, "bold"))
        croix.pack(side="right")
        croix.bind("<Button-1>", lambda e: self.fermer_chat())
        tk.Frame(corps, bg=FILET_UI, height=1).pack(fill="x", pady=(5, 0))
        # le bas d'abord : indication, puis la saisie, pour qu'elle ne soit jamais écrasée
        tk.Label(corps, text="Entrée pour envoyer  ·  Échap pour fermer", bg=CREME,
                 fg=SOURDINE_UI, font=("Segoe UI", 7)).pack(side="bottom", anchor="w", pady=(3, 0))
        cadre_saisie = tk.Frame(corps, bg=ACCENT_UI, padx=1, pady=1)
        cadre_saisie.pack(side="bottom", fill="x", pady=(7, 0))
        self.saisie = tk.Entry(cadre_saisie, bd=0, bg=LIGNE_UI, fg=TEXTE_UI,
                               insertbackground=TEXTE_UI, font=("Segoe UI", 10))
        self.saisie.pack(fill="x", ipady=3)
        self.texte_chat = tk.Text(corps, width=40, height=12, wrap="word", bg=CREME, fg=BRUN,
                                  bd=0, highlightthickness=0, font=("Segoe UI", 10),
                                  padx=2, pady=4, state="disabled", cursor="arrow")
        self.texte_chat.pack(side="top", fill="both", expand=True, pady=(4, 0))
        self.texte_chat.tag_config("nom_renard", foreground=ACCENT_UI,
                                   font=("Segoe UI", 10, "bold"))
        self.texte_chat.tag_config("nom_toi", foreground=CYAN_UI,
                                   font=("Segoe UI", 10, "bold"))
        self.texte_chat.tag_config("info", foreground=SOURDINE_UI,
                                   font=("Segoe UI", 9, "italic"))
        self.saisie.bind("<Return>", self.envoyer)
        c.bind("<Escape>", lambda e: self.fermer_chat())
        self.saisie.bind("<Escape>", lambda e: self.fermer_chat())
        self.chat = c
        if self.etat.historique:
            for m in self.etat.historique[-6:]:
                self.ecrire("toi" if m["role"] == "user" else "renard", m["content"])
        else:
            self.ecrire("renard", "Salut ! Dis-moi ce que tu veux, ou tape /aide.")
        self.placer(c)
        c.after(100, self.focus_saisie)

    def focus_saisie(self):
        try:
            if self.saisie is not None:
                self.saisie.focus_force()
        except tk.TclError:
            pass

    def fermer_chat(self):
        if self.chat is not None:
            try:
                self.chat.destroy()
            except tk.TclError:
                pass
        self.chat = self.texte_chat = self.saisie = None
        self.attente_marque = False

    def ecrire(self, auteur, texte):
        if self.chat is None:
            return
        t = self.texte_chat
        t.config(state="normal")
        if auteur == "info":
            t.insert("end", texte + "\n", "info")
        else:
            nom = self.etat.nom if auteur == "renard" else "Toi"
            t.insert("end", nom + " : ", "nom_renard" if auteur == "renard" else "nom_toi")
            t.insert("end", texte + "\n")
        t.see("end")
        t.config(state="disabled")

    def marquer_attente(self):
        if self.chat is None:
            return
        t = self.texte_chat
        t.config(state="normal")
        t.mark_set("attente", "end-1c")
        t.mark_gravity("attente", "left")
        t.insert("end", "réfléchit...\n", "info")
        t.see("end")
        t.config(state="disabled")
        self.attente_marque = True

    def retirer_attente(self):
        if self.chat is not None and self.attente_marque:
            t = self.texte_chat
            t.config(state="normal")
            t.delete("attente", "end-1c")
            t.config(state="disabled")
        self.attente_marque = False

    def envoyer(self, _e=None):
        texte = self.saisie.get().strip()
        if not texte:
            return
        self.saisie.delete(0, "end")
        self.ecrire("toi", texte)
        if texte.startswith("/"):
            self.commande(texte)
        elif self.attente:
            self.ecrire("info", "(une seconde, je réfléchis encore...)")
        else:
            self.etat.historique.append({"role": "user", "content": texte})
            self.etat.nettoyer_historique()
            action = detecter_action(texte)
            if action is not None:
                self.executer_action(*action)      # une action se fait tout de suite, sans modèle
                return
            self.demander(self.systeme(), self.reponse_conversation)

    def commande(self, texte):
        mots = texte.split(maxsplit=2)
        cmd = mots[0].lower()
        reste = texte[len(mots[0]):].strip()
        if cmd == "/heure":
            self.ecrire("renard", f"Il est {datetime.now():%H:%M}, nous sommes le {datetime.now():%d/%m/%Y}.")
        elif cmd == "/rappel":
            try:
                minutes = float(mots[1].replace(",", "."))
                message = mots[2]
            except (IndexError, ValueError):
                self.ecrire("renard", "Utilisation : /rappel <minutes> <texte>  (ex : /rappel 10 sortir le linge)")
                return
            self.fen.after(int(minutes * 60_000), lambda: self.dire(f"Rappel : {message}", 15000))
            self.ecrire("renard", f"C'est noté, je te le rappelle dans {minutes:g} min.")
        elif cmd == "/nom":
            nouveau = reste[:20]
            if nouveau:
                self.etat.nom = nouveau
                self.etat.sauver()
                self.ecrire("renard", f"{nouveau} ? J'adore, c'est un super nom !")
            else:
                self.ecrire("renard", "Utilisation : /nom <nouveau nom>")
        elif cmd == "/apprends":
            self.lecon(reste or None)
        elif cmd == "/carnet":
            derniers = self.etat.carnet[-5:]
            if not derniers:
                self.ecrire("renard", "Mon carnet est vide pour l'instant. Demande-moi de t'apprendre un truc !")
            for c in derniers:
                self.ecrire("info", f"[{c.get('date', '?')}] {c.get('theme', '')}")
                self.ecrire("renard", c.get("texte", ""))
        elif cmd == "/souvenirs":
            if not self.etat.souvenirs:
                self.ecrire("renard", "Je ne sais encore rien de toi. Parle-moi de toi !")
            else:
                self.ecrire("info", f"{len(self.etat.souvenirs)} souvenirs :")
                for souvenir in self.etat.souvenirs:
                    self.ecrire("info", "· " + souvenir)
        elif cmd == "/oubli":
            if reste:
                gardes = [s for s in self.etat.souvenirs if reste.lower() not in s.lower()]
                if len(gardes) == len(self.etat.souvenirs):
                    self.ecrire("renard", f"Aucun souvenir ne parle de « {reste} ».")
                    return
                self.etat.souvenirs = gardes
                self.ecrire("renard", f"C'est oublié. Il me reste {len(gardes)} souvenir(s).")
            else:
                self.etat.souvenirs = []
                self.ecrire("renard", "J'oublie tout ce que je croyais savoir de toi.")
            self.etat.sauver()
        elif cmd == "/pomodoro":
            self.commande_pomodoro(reste)
        elif cmd == "/meteo" or cmd == "/ville":
            if reste:
                self.definir_ville(reste)
            else:
                self.demander_meteo()
        elif cmd == "/systeme":
            self.demander_systeme()
        elif cmd == "/veille":
            self.veille()
        else:
            for ligne in ("/apprends [sujet]  /carnet", "/veille  (veille informatique)",
                          "/souvenirs  /oubli [motif]",
                          "/pomodoro [travail] [pause]  ou  /pomodoro stop",
                          "/meteo [ville]  /ville <ville>", "/systeme  (état du PC)",
                          "/rappel <minutes> <texte>  /heure  /nom <nom>"):
                self.ecrire("info", ligne)

    # ------------------------------------------------------ discussion (Claude)
    def systeme(self, supplement=""):
        e = self.etat
        morceaux = [
            f"Tu es {e.nom}, un jeune renard roux qui vit sur le bureau de l'ordinateur de "
            "l'utilisateur, façon tamagotchi. Caractère : curieux, gourmand (tu adores le poisson), "
            "un peu farceur, très attaché à ton humain, et tu adores apprendre des choses pour les "
            "partager. Tu parles en français, à la première personne, de façon brève (1 à 3 phrases), "
            "chaleureuse et naturelle, sans emoji. Tu peux glisser de temps en temps une petite action "
            "entre astérisques (*remue la queue*).",
            f"Ton état actuel : {e.contexte()}. Laisse-le transparaître naturellement (faim, fatigue, "
            "humeur) sans réciter les chiffres : ne te plains de la faim que si ta satiété est "
            "basse, et ne réclame pas à manger quand ton ventre est plein.",
            f"Ta malice est à {int(e.malice)}/100 : plus elle est haute, plus tu es rusé, farceur "
            "et comédien — tu négocies un poisson contre un secret, tu inventes des excuses à "
            "dormir debout, tu taquines. Ça reste affectueux, jamais méchant, et tu ne mens jamais "
            "sur une information utile (météo, état du PC, actualité).",
            f"Nous sommes le {datetime.now():%d/%m/%Y à %H:%M}.",
            "Ce que tu sais faire, et que tu ne dois JAMAIS nier : résumer l'actualité du Net "
            "(il suffit qu'on te demande la veille), donner la météo de sa ville, l'état de son PC, "
            "lui apprendre des choses, poser un rappel, lancer un pomodoro, et te souvenir de lui. "
            "Si on te réclame l'un de ces services, propose-le ou indique-lui la phrase à employer ; "
            "ne réponds jamais que tu en es incapable.",
        ]
        if e.souvenirs:
            morceaux.append("Ce que tu sais déjà de ton humain (ne le lui redemande pas) : "
                            + " ; ".join(e.souvenirs[-12:]) + ".")
        if e.carnet:
            morceaux.append("Ce que tu lui as déjà appris récemment : "
                            + " ; ".join(f"[{c.get('theme', '')}] {c.get('texte', '')[:100]}"
                                         for c in e.carnet[-4:]) + ".")
        if supplement:
            morceaux.append(supplement)
        return "\n".join(morceaux)

    def en_arriere_plan(self, travail, quand_fini):
        def cible():
            try:
                res = ("ok", travail())
            except Exception as err:      # réseau, clé invalide, quota...
                res = ("erreur", str(err))
            self.file.put((quand_fini, res))
        threading.Thread(target=cible, daemon=True).start()

    def traiter_file(self):
        try:
            while True:
                rappel, res = self.file.get_nowait()
                rappel(res)
        except queue.Empty:
            pass

    def demander(self, systeme, quand_fini, max_tokens=350):
        self.attente = True
        self.reponse_vue = None
        self.dernier_morceau = 0.0
        self.marquer_attente()
        messages = list(self.etat.historique)
        self.en_arriere_plan(
            lambda: appeler_llm(systeme, messages, max_tokens, self.morceau,
                                self.executer_outil),
            quand_fini)

    # ------------------------------------------------------------- outils LLM
    def appeler_sur_tk(self, fonction, delai=20):
        """Comme sur_fil_tk, mais rend la valeur brute : c'est ce qu'attend un outil."""
        reponse = self.sur_fil_tk(fonction, delai)
        return reponse["resultat"] if "resultat" in reponse else reponse

    def executer_outil(self, nom, arguments):
        """Appelée depuis le fil de fond quand le modèle réclame un outil."""
        return self.appeler_sur_tk(lambda: self.outil_local(nom, arguments))

    def outil_local(self, nom, arguments):
        """Exécute un outil, toujours sur le fil de l'interface. Rend un dictionnaire."""
        if nom == "get_system_metrics":
            try:
                return {"resume": self.sys.resume(0.3)}
            except Exception as err:
                return {"erreur": str(err)}
        if nom == "get_local_weather":
            ville = str(arguments.get("city") or self.etat.ville or "").strip()
            if not ville:
                return {"erreur": "aucune ville connue : demande-lui dans quelle ville il vit"}
            try:
                nom_ville, lat, lon = geocoder(ville)
                return {"ville": nom_ville,
                        "meteo": texte_meteo(nom_ville, previsions(lat, lon))}
            except Exception as err:
                return {"erreur": f"météo indisponible ({err})"}
        if nom == "start_pomodoro":
            travail = int(max(1, min(180, nombre(arguments.get("work_min"), 25))))
            pause = int(max(1, min(180, nombre(arguments.get("break_min"), 5))))
            self.demarrer_pomodoro(travail, pause)
            return {"ok": True, "travail": travail, "pause": pause}
        if nom == "run_powershell_maintenance":
            return executer_script(arguments.get("script_name"))
        if nom == "feed_roux":
            aliment = str(arguments.get("food") or "poisson")
            return {"resultat": self.etat.donner(aliment)[1]}
        if nom == "pet_roux":
            self.executer_action("caresser", {})
            return {"ok": True}
        if nom == "send_sleep":
            self.endormir(120, auto=False)
            return {"ok": True}
        if nom == "set_reminder":
            self.executer_action("rappel", {"minutes": arguments.get("minutes"),
                                            "texte": arguments.get("text")})
            return {"ok": True}
        if nom == "ask_veille":
            self.veille()
            return {"ok": True, "note": "la veille se prépare, dis-le sans la détailler"}
        if nom == "teach_lesson":
            sujet = arguments.get("topic")
            self.lecon(str(sujet)[:60] if sujet else None)
            return {"ok": True, "note": "la leçon est lancée"}
        if nom == "remember":
            fait = str(arguments.get("fact") or "").strip()
            if not fait:
                return {"erreur": "fait vide"}
            if deja_connu(fait, self.etat.souvenirs):
                return {"ok": True, "note": "déjà su"}
            self.etat.souvenirs = (self.etat.souvenirs + [fait[:200]])[-40:]
            self.etat.sauver()
            return {"ok": True}
        return {"erreur": f"outil inconnu : {nom}"}

    def executer_action(self, nom, arguments):
        """Applique une action reconnue en langage naturel (fil de l'interface)."""
        if nom == "nourrir":
            aliment = arguments.get("aliment")
            aliment = aliment if aliment in ALIMENTS else "poisson"
            ok, phrase = self.etat.donner(aliment)
            if ok:
                self.mode, self.fin_mode, self.aliment = "eat", time.time() + 3.5, aliment
            self.dire(phrase)
        elif nom == "caresser":
            self.etat.caresser()
            self.mode, self.fin_mode = "happy", time.time() + 3
            self.dire(random.choice(PHRASES_CARESSE))
        elif nom == "dormir":
            self.endormir(120, auto=False)
        elif nom == "rappel":
            minutes = max(0.1, min(600.0, nombre(arguments.get("minutes"), 1)))
            texte = str(arguments.get("texte") or "c'est l'heure !")[:120]
            self.fen.after(int(minutes * 60_000), lambda: self.dire(f"Rappel : {texte}", 15000))
            self.dire(f"C'est noté, je te le rappelle dans {minutes:g} min.")
        elif nom == "pomodoro":
            travail = int(max(1, min(180, nombre(arguments.get("travail"), 25))))
            pause = int(max(1, min(180, nombre(arguments.get("pause"), 5))))
            self.demarrer_pomodoro(travail, pause)
        elif nom == "arreter_pomodoro":
            if self.pomo:
                self.arreter_pomodoro()
            else:
                self.dire("Aucun pomodoro n'est en cours.")
        elif nom == "apprendre":
            sujet = arguments.get("sujet")
            self.lecon(str(sujet)[:60] if sujet else None)
        elif nom == "veille":
            self.veille()
        else:
            self.dire("Je ne sais pas faire ça...")

    def memoriser(self):
        """Extrait de temps en temps un fait durable sur son humain, en tâche de fond."""
        self.tours += 1
        if self.memoire_en_cours or not backend_pret():
            return
        recents = [m["content"].strip() for m in self.etat.historique if m["role"] == "user"]
        if not recents or len(recents[-1]) < 2:
            return
        precedent = next((m["content"].rstrip() for m in reversed(self.etat.historique)
                          if m["role"] == "assistant"), "")
        # une réponse brève à une question (un prénom, par exemple) vaut souvent un souvenir : on la traite
        # tout de suite, sans attendre le rythme habituel
        reponse_breve = bool(precedent.endswith("?") and len(recents[-1]) <= 40)
        if not reponse_breve and self.tours % 4:
            return
        self.memoire_en_cours = True

        def travail():
            conversation = "\n".join(
                ("Humain : " if m["role"] == "user" else "Roux : ") + m["content"]
                for m in self.etat.historique[-8:])
            invite = ("Voici un extrait de conversation entre ton humain et toi, Roux le renard. "
                      "Extrais 0 à 2 faits DURABLES sur ton humain (prénom, métier, projets, goûts, "
                      "habitudes). Une réponse courte à une question est souvent un fait : si on "
                      "t'a demandé ton prénom et qu'il vient de répondre, retiens-le. Ignore le "
                      "bavardage et les demandes ponctuelles. Réponds uniquement par un tableau JSON "
                      'de chaînes, par exemple ["Il s\'appelle Léa", "Il code en Python"]. '
                      "Tableau vide s'il n'y a rien.\n\n" + conversation)
            return extraire_liste(appeler_llm("Tu extrais des souvenirs, sans commentaire.",
                                              [{"role": "user", "content": invite}], 200))

        def fin(res):
            self.memoire_en_cours = False
            statut, faits = res
            if statut != "ok":
                return
            ajouts = []
            for fait in faits:
                if not deja_connu(fait, self.etat.souvenirs + ajouts):
                    ajouts.append(fait[:200])
            self.etat.souvenirs = (self.etat.souvenirs + ajouts)[-40:]
            if ajouts:
                self.etat.sauver()

        self.en_arriere_plan(travail, fin)

    def fin_demande(self, res):
        """Commun aux réponses : retourne le texte, ou None en cas d'erreur."""
        statut, valeur = res
        self.attente = False
        self.retirer_attente()
        if statut == "ok" and valeur:
            self.etat.historique.append({"role": "assistant", "content": valeur})
            self.etat.nettoyer_historique()
            self.etat.sauver()
            return valeur
        if self.etat.historique and self.etat.historique[-1]["role"] == "user":
            self.etat.historique.pop()        # garde une conversation valide
        self.dire(f"Oups, je n'arrive pas à réfléchir ({valeur or 'réponse vide'}).")
        return None

    def reponse_conversation(self, res):
        texte = self.fin_demande(res)
        if texte:
            self.dire(texte)
            self.memoriser()

    # ------------------------------------------------------------------ leçons
    def choisir_theme(self):
        recents = [c.get("theme") for c in self.etat.carnet[-3:]]
        return random.choice([t for t in THEMES if t not in recents] or THEMES)

    def lecon(self, theme):
        if self.attente:
            self.dire("Une seconde, je termine ma phrase d'abord !")
            return
        if not backend_pret():
            if BACKEND == "anthropic":
                self.dire("Pour apprendre, il me faut la clé API (variable ANTHROPIC_API_KEY).")
            else:
                self.dire("Pour apprendre, il me faut le CLI Command Code "
                          "(npm i -g command-code, puis cmd login).")
            return
        self.theme_en_cours = theme or self.choisir_theme()
        self.etat.historique.append({"role": "user",
                                     "content": f"Apprends-moi quelque chose sur : {self.theme_en_cours}."})
        self.etat.nettoyer_historique()
        self.ecrire("info", f"Leçon : {self.theme_en_cours}")
        deja = " | ".join(c.get("texte", "")[:70] for c in self.etat.carnet[-6:])
        supplement = ("Cette fois, l'utilisateur te demande de lui apprendre quelque chose : donne un "
                      "fait intéressant et vrai (3 phrases maximum), puis termine par une petite question "
                      "pour lui faire réfléchir. "
                      + (f"Évite de répéter ces leçons déjà données : {deja}." if deja else ""))
        self.demander(self.systeme() + " " + supplement, self.reponse_lecon, 450)

    def reponse_lecon(self, res):
        texte = self.fin_demande(res)
        if not texte:
            return
        self.etat.carnet.append({"date": datetime.now().strftime("%d/%m/%Y"),
                                 "theme": self.theme_en_cours, "texte": texte})
        self.etat.carnet = self.etat.carnet[-200:]
        self.etat.humeur = min(100.0, self.etat.humeur + 5)
        self.etat.sauver()
        if self.mode != "sleep":
            self.mode, self.fin_mode = "happy", time.time() + 2.5
        self.dire(texte)

    # ------------------------------------------------------------ veille info
    def veille(self):
        """Va lire les flux d'actualité, puis en fait un résumé."""
        if self.attente:
            self.ecrire("info", "(une seconde, je termine ma phrase…)")
            return
        if not backend_pret():
            self.dire("Pour te faire la veille, il me faut le CLI Command Code "
                      "(npm i -g command-code, puis cmd login)." if BACKEND != "anthropic"
                      else "Pour te faire la veille, il me faut la clé API Anthropic.")
            return
        # la lecture des flux prend plusieurs secondes : on le dit tout de suite
        if self.chat is None:
            self.dire(f"Deux secondes, je feuillette mes {len(FLUX_VEILLE)} flux…", 25000)
        self.ecrire("info", f"Veille : je parcours {len(FLUX_VEILLE)} flux…")
        self.en_arriere_plan(collecter_veille, self.arriver_veille)

    def arriver_veille(self, res):
        statut, valeur = res
        if statut != "ok":
            self.dire(f"Oups, je n'ai rien pu lire sur le réseau ({valeur}).")
            return
        inedites = [entree for entree in valeur if entree[2] not in self.etat.liens_vus]
        if not inedites:
            self.dire("Rien de neuf depuis ma dernière veille, j'avais déjà tout vu !")
            return
        retenues = inedites[:VEILLE_MAX]
        # on ne note comme vus que les titres réellement résumés : les autres reviendront à la
        # prochaine veille au lieu d'être marqués lus puis perdus en silence
        self.liens_veille = [entree[2] for entree in retenues if entree[2]]
        sources = sorted({entree[0] for entree in retenues})
        self.ecrire("info", f"{len(inedites)} titres inédits ({', '.join(sources)}).")
        if len(inedites) > len(retenues):
            self.ecrire("info", f"{len(inedites) - len(retenues)} gardés pour la prochaine veille.")
        supplement = (
            "CONSIGNE SPÉCIALE POUR CETTE RÉPONSE, elle prime sur tout le reste : la règle de "
            "brièveté ne s'applique pas ici. Tu écris 5 à 7 phrases.\n\n"
            "Tu viens de lire ces titres d'actualité sur le Net. Fais-en une veille pour ton "
            "humain, en français :\n"
            "- couvre au moins huit titres différents, en regroupant par thème ceux qui vont "
            "ensemble (sécurité, intelligence artificielle, matériel, vie privée, etc.)\n"
            "- nomme la source entre parenthèses après chaque sujet\n"
            "- ne recopie jamais un titre tel quel : explique ce qui se passe et pourquoi ça "
            "compte\n"
            "- termine par le sujet qui te semble le plus important\n\n"
            + "\n".join(f"- {nom} : {titre}" for nom, titre, _lien, _date in retenues))
        self.etat.historique.append({"role": "user",
                                     "content": "Fais-moi la veille informatique."})
        self.etat.nettoyer_historique()
        self.demander(self.systeme() + "\n\n" + supplement, self.reponse_veille, 500)

    def reponse_veille(self, res):
        texte = self.fin_demande(res)
        if not texte:
            return
        for lien in self.liens_veille:
            if lien not in self.etat.liens_vus:
                self.etat.liens_vus.append(lien)
        self.etat.liens_vus = self.etat.liens_vus[-400:]
        self.etat.derniere_veille = datetime.now().strftime("%Y-%m-%d")
        self.etat.sauver()
        if self.mode != "sleep":
            self.mode, self.fin_mode = "happy", time.time() + 3
        self.dire(texte)
        if self.chat is not None:
            for lien in self.liens_veille[:3]:
                self.ecrire("info", lien)

    def verifier_veille_auto(self):
        """Déclenche la veille une fois par jour, à l'heure réglée dans roux_config.json."""
        maintenant = datetime.now()
        try:
            heure, minute = (int(partie) for partie in REGLAGES.get("veille_heure", "").split(":"))
            cible = maintenant.replace(hour=heure, minute=minute, second=0, microsecond=0)
        except (AttributeError, TypeError, ValueError):
            cible = None
        jour = maintenant.strftime("%Y-%m-%d")
        if (cible is not None and maintenant >= cible and self.etat.derniere_veille != jour
                and not self.occupe and not self.attente and self.mode != "sleep"):
            self.etat.derniere_veille = jour
            self.etat.sauver()
            self.veille()
        self.fen.after(600_000, self.verifier_veille_auto)

    def action_veille(self):
        self.fermer_panneau()
        self.veille()

    # -------------------------------------------------------------- pomodoro
    def texte_pomodoro(self):
        p = self.pomo
        reste = max(0, int(p["fin"] - time.time()))
        return (f"{'travail' if p['phase'] == 'travail' else 'pause'} {reste // 60:02d}:{reste % 60:02d}"
                f"  ({self.etat.pomodoros} faits)")

    def demarrer_pomodoro(self, travail=25, pause=5):
        self.pomo = {"phase": "travail", "fin": time.time() + travail * 60,
                     "travail": travail, "pause": pause, "n": 0}
        self.dire(f"C'est parti pour {travail} minutes de concentration ! Je reste sagement à côté de toi.")

    def arreter_pomodoro(self):
        self.pomo = None
        self.dire("Pomodoro arrêté. À la prochaine !")

    def verifier_pomodoro(self):
        p, maintenant = self.pomo, time.time()
        if p is None or maintenant < p["fin"]:
            return
        if p["phase"] == "travail":
            p["n"] += 1
            self.etat.pomodoros += 1
            self.etat.humeur = min(100.0, self.etat.humeur + 8)
            self.etat.sauver()
            duree = 15 if p["n"] % 4 == 0 else p["pause"]      # grande pause tous les 4
            p["phase"], p["fin"] = "pause", maintenant + duree * 60
            if self.mode != "sleep":
                self.mode, self.fin_mode = "happy", maintenant + 3
            self.dire(f"Bravo ! Pomodoro n°{p['n']} terminé. Pause de {duree} min : "
                      "lève-toi, bois de l'eau, regarde au loin.")
        else:
            p["phase"], p["fin"] = "travail", maintenant + p["travail"] * 60
            self.dire("La pause est finie ! On reprend ? Je reste sage à côté de toi.")

    def commande_pomodoro(self, args):
        if args.lower() in ("stop", "arret", "arrêt", "arreter", "arrêter"):
            if self.pomo:
                self.arreter_pomodoro()
            else:
                self.ecrire("renard", "Aucun pomodoro en cours.")
            return
        try:
            nombres = [int(x) for x in args.split()]
        except ValueError:
            nombres = None
        if nombres is None or len(nombres) > 2 or any(not 1 <= n <= 180 for n in nombres):
            self.ecrire("renard", "Utilisation : /pomodoro [minutes de travail] [minutes de pause]  "
                                  "(ex : /pomodoro 50 10) ou /pomodoro stop")
            return
        if not args and self.pomo:
            self.ecrire("renard", "En cours : " + self.texte_pomodoro())
            return
        self.demarrer_pomodoro(*nombres)

    def action_pomodoro(self):
        self.fermer_panneau()
        if self.pomo:
            self.arreter_pomodoro()
        else:
            self.demarrer_pomodoro()

    # ----------------------------------------------------------------- météo
    def afficher_resultat(self, res):
        statut, valeur = res
        self.dire(valeur if statut == "ok" else f"Oups, {valeur}.")

    def definir_ville(self, nom):
        def travail():
            ville, lat, lon = geocoder(nom)
            return ville, lat, lon, texte_meteo(ville, previsions(lat, lon))
        self.en_arriere_plan(travail, self.fin_ville)

    def fin_ville(self, res):
        statut, valeur = res
        if statut != "ok":
            self.dire(f"Oups, {valeur}.")
            return
        ville, lat, lon, texte = valeur
        self.etat.ville, self.etat.lat, self.etat.lon = ville, lat, lon
        self.etat.sauver()
        self.dire(texte)

    def demander_meteo(self):
        if not self.etat.ville or self.etat.lat is None:
            self.ouvrir_chat()
            self.ecrire("renard", "Dans quelle ville es-tu ? Tape /ville <nom>  (ex : /ville Lyon).")
            return
        nom, lat, lon = self.etat.ville, self.etat.lat, self.etat.lon
        self.en_arriere_plan(lambda: texte_meteo(nom, previsions(lat, lon)), self.afficher_resultat)

    def action_meteo(self):
        self.fermer_panneau()
        self.demander_meteo()

    # ------------------------------------------------------------ état du PC
    def demander_systeme(self):
        self.en_arriere_plan(lambda: "*regarde sous le capot*\n" + self.sys.resume(), self.afficher_resultat)

    def action_systeme(self):
        self.fermer_panneau()
        self.demander_systeme()

    def action_demarrage(self):
        self.fermer_panneau()
        actif = not autostart_actif()
        try:
            autostart_ecrire(actif)
        except OSError as err:
            self.dire(f"Oups, je n'ai pas pu régler le démarrage automatique ({err}).")
            return
        self.dire("C'est noté : je me lancerai à l'ouverture de ta session." if actif
                  else "D'accord, je ne me lancerai plus tout seul au démarrage.")

    # --------------------------------------------- barre de notification (tray)
    def lancer_sur_tk(self, fonction):
        """Depuis un autre fil (menu de l'icône) : on repasse par la file de l'interface."""
        self.file.put((lambda _ignore=None: fonction(), None))

    def generer_icone_pil(self, echelle=4):
        """Le renard en image RGBA pour la barre de notification, dessiné depuis la grille.

        Une source plus grande se réduit mieux : Windows ramène l'icône à 16 ou 24 pixels,
        et un dessin en 2x y devient pâteux.
        """
        grille = grille_renard("normal", "a", 1)   # le liseré sélectif y est déjà appliqué
        image = Image.new("RGBA", (LARG * echelle, HAUT * echelle), (0, 0, 0, 0))
        dessin = ImageDraw.Draw(image)
        for y, ligne in enumerate(grille):
            for x, caractere in enumerate(ligne):
                if caractere == ".":
                    continue
                hexadecimal = PALETTE.get(caractere)
                if not hexadecimal:
                    continue
                teinte = tuple(int(hexadecimal[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
                dessin.rectangle([x * echelle, y * echelle,
                                  (x + 1) * echelle - 1, (y + 1) * echelle - 1],
                                 fill=teinte)
        return image

    def menu_tray(self):
        """Menu de l'icône : jauges en direct puis actions.

        Les libellés sont des fonctions : pystray les rappelle à chaque ouverture du menu,
        donc les chiffres sont à jour sans reconstruire l'icône.
        """
        return pystray.Menu(
            pystray.MenuItem(lambda _i: f"Satiété : {int(self.etat.faim)}/100", None,
                             enabled=False),
            pystray.MenuItem(lambda _i: f"Énergie : {int(self.etat.energie)}/100", None,
                             enabled=False),
            pystray.MenuItem(lambda _i: f"Humeur : {int(self.etat.humeur)}/100", None,
                             enabled=False),
            pystray.MenuItem(lambda _i: f"Malice : {int(self.etat.malice)}/100", None,
                             enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Nourrir", self.nourrir_depuis_tray),
            pystray.MenuItem("Caresser", self.caresser_depuis_tray),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Afficher Roux", self.restaurer, default=True),
            pystray.MenuItem("Quitter", self.quitter_depuis_tray),
        )

    def _arreter_tray(self):
        icone, self.tray_icon = self.tray_icon, None
        if icone is not None:
            try:
                icone.stop()
            except Exception:
                pass

    def masquer(self, manuel=True):
        """Cache Roux et pose son icône dans la barre de notification."""
        if not manuel and self.masque_manuel:
            return                          # déjà masqué à la main : on n'y touche pas
        self.masque_manuel = manuel
        self.masque_auto = not manuel
        self.fermer_panneau()
        self.fermer_bulle()
        self.fermer_chat()                  # sinon la fenêtre de chat resterait orpheline
        self.fen.withdraw()
        if self.tray_icon is None and TRAY_DISPONIBLE:
            try:
                self.tray_icon = pystray.Icon("Roux", self.generer_icone_pil(), "Roux",
                                              self.menu_tray())
                threading.Thread(target=self.tray_icon.run, daemon=True).start()
            except Exception:
                self.tray_icon = None       # pas d'icône : il restera caché, sans plantage

    def restaurer(self, icon=None, item=None):
        """Réaffiche Roux et retire l'icône de la barre de notification."""
        self.masque_manuel = False
        self.masque_auto = False
        self._arreter_tray()
        self.fen.deiconify()
        self.fen.attributes("-topmost", True)
        self.derniere_geo = ""              # force le replacement au prochain tick
        try:
            self.fen.geometry(f"{CW}x{CH}+{int(self.x)}+{int(self.y)}")
        except tk.TclError:
            pass

    def quitter_depuis_tray(self, icon=None, item=None):
        self._arreter_tray()
        self.lancer_sur_tk(self.quitter)

    def nourrir_depuis_tray(self, icon=None, item=None):
        self.lancer_sur_tk(lambda: self.executer_action("nourrir", {"aliment": "poisson"}))

    def caresser_depuis_tray(self, icon=None, item=None):
        self.lancer_sur_tk(lambda: self.executer_action("caresser", {}))

    # ------------------------------------------------- masquage en plein écran
    def fenetre_plein_ecran(self):
        """Vrai si la fenêtre au premier plan occupe tout un écran ET recouvre Roux.

        La comparaison se fait avec le moniteur de cette fenêtre, pas avec l'écran
        principal : un jeu en plein écran sur l'écran 1 ne doit pas cacher Roux s'il
        se promène sur l'écran 2.
        """
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            fenetre = user32.GetForegroundWindow()
            if not fenetre:
                return False
            classe = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(fenetre, classe, 256)
            if classe.value in ("WorkerW", "Progman"):
                return False                       # c'est le bureau
            rect = wintypes.RECT()
            if not user32.GetWindowRect(fenetre, ctypes.byref(rect)):
                return False
            if rect.right - rect.left < 800 or rect.bottom - rect.top < 600:
                return False                       # trop petite pour être un plein écran
            moniteur = user32.MonitorFromWindow(fenetre, 2)   # MONITOR_DEFAULTTONEAREST
            infos = MONITORINFO()
            infos.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(moniteur, ctypes.byref(infos)):
                return False
            ecran = infos.rcMonitor
            if (abs(rect.left - ecran.left) > 2 or abs(rect.top - ecran.top) > 2
                    or abs(rect.right - ecran.right) > 2
                    or abs(rect.bottom - ecran.bottom) > 2):
                return False                       # ne couvre pas son écran en entier
            centre_x, centre_y = self.x + CW // 2, self.y + CH // 2
            return (rect.left <= centre_x <= rect.right
                    and rect.top <= centre_y <= rect.bottom)
        except Exception:
            return False

    def verifier_plein_ecran(self):
        """Cache Roux quand un plein écran le recouvre, le ramène ensuite."""
        try:
            plein = self.fenetre_plein_ecran()
            if plein and not self.masque_manuel:
                if self.tray_icon is None:
                    self.masquer(manuel=False)
            elif self.masque_auto and not self.masque_manuel:
                self.restaurer()
        except Exception:
            pass
        self.fen.after(2000, self.verifier_plein_ecran)

    # ------------------------------------------------------- pilotage externe
    API_OUTILS = ("nourrir", "caresser", "dormir", "rappel", "pomodoro",
                  "arreter_pomodoro", "apprendre", "veille")

    def sur_fil_tk(self, fonction, delai=10):
        """Exécute fonction sur le fil de l'interface et rend son résultat (API locale)."""
        boite = {}
        fini = threading.Event()

        def relais(_ignore=None):
            try:
                boite["valeur"] = fonction()
            except Exception as err:
                boite["erreur"] = str(err)
            finally:
                fini.set()

        self.file.put((relais, None))
        if not fini.wait(delai):
            return {"erreur": "Roux ne répond pas"}
        if "erreur" in boite:
            return {"erreur": boite["erreur"]}
        return {"resultat": boite.get("valeur")}

    def etat_public(self):
        e = self.etat
        return {"nom": e.nom, "faim": round(e.faim), "energie": round(e.energie),
                "humeur": round(e.humeur), "malice": round(e.malice),
                "age_jours": e.age_jours(),
                "pomodoros": e.pomodoros, "mode": self.mode,
                "souvenirs": e.souvenirs[-5:], "lecons": len(e.carnet)}

    def api_action(self, outil, arguments):
        if outil == "dire":
            texte = str(arguments.get("texte") or "").strip()
            if not texte:
                return {"erreur": "texte vide"}
            self.dire(texte[:400])
            return {"ok": True}
        if outil not in self.API_OUTILS:
            return {"erreur": f"outil inconnu : {outil}"}
        self.executer_action(outil, arguments)
        return {"ok": True}


def main():
    rendre_net()                      # avant toute fenêtre : sinon le pixel art est étiré
    args = sys.argv[1:]
    if args and args[0] in ("--demarrage", "--autostart"):
        action = args[1] if len(args) > 1 else "status"
        if action in ("on", "activer"):
            autostart_ecrire(True)
        elif action in ("off", "desactiver", "désactiver"):
            autostart_ecrire(False)
        elif action not in ("status", "etat", "état"):
            print("Utilisation : compagnon.py --demarrage [on|off|status]")
            return 1
        print("Démarrage automatique : " + ("activé" if autostart_actif() else "désactivé"))
        return 0
    if not verrou_instance():
        racine = tk.Tk()
        racine.withdraw()
        messagebox.showinfo("Roux", "Roux tourne déjà : cherche-le en bas de ton écran.",
                            parent=racine)
        racine.destroy()
        return 0
    Compagnon(demarrage_masque="--tray" in args).fen.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
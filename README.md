# Roux, le renard de bureau

Un compagnon de bureau pour Windows, écrit en Python/Tkinter : un renard qui se balade en
bas de l'écran, dort, a faim, chasse — et se transforme en assistant système quand on lui
parle.

Aucune image à fournir : le renard est entièrement dessiné en pixels par le programme.

## Ce qu'il fait

- Il vit en bas de l'écran : il se déplace, dort quand il est fatigué, a faim, change
  d'humeur et chasse parfois une bestiole de son propre chef.
- Clic sur lui : Nourrir, Caresser, Parler, Apprendre, Dormir, Outils. Glisser pour le
  déplacer.
- Dépose un fichier texte ou un log sur le renard : il en lit 2 000 caractères et rend un
  diagnostic express.
- Barre de notification : « Masquer » le range dans le tiroir Windows, avec une icône
  dessinée depuis la même grille et les jauges en direct dans le menu. Il se cache aussi
  seul quand une fenêtre en plein écran le recouvre, et revient dès qu'elle se ferme.
- Outils du modèle (appel de fonction, pas de détection par mots-clés) : état du PC,
  météo, pomodoro, veille, leçons, souvenirs, et exécution des scripts de maintenance du
  dossier `scripts/` (nom filtré, chemin vérifié : rien d'autre n'est exécutable).
- Veille : 12 flux (presse informatique FR, sécurité, Hacker News), résumé en 12 titres
  maximum, résultat mis en cache 30 minutes.

## Prérequis

- Python 3.10 minimum
- Windows : le rendu matriciel, la barre de notification et le verrou d'instance
  s'appuient sur des API Windows
- Facultatif, selon ce que tu veux activer :

```
pip install tkinterdnd2 pystray pillow
```

`tkinterdnd2` pour le glisser-déposer, `pystray` et `Pillow` pour l'icône de la barre de
notification. Sans eux, tout le reste continue de fonctionner.

## Démarrage

```
python compagnon.py
```

## Configuration

Les réglages vivent dans `roux_config.json`, à côté du script. Copie le modèle :

```
copy roux_config.exemple.json roux_config.json
```

| Clé | Rôle |
| --- | --- |
| `backend` | `cmdc`, `http` ou `anthropic` |
| `modele` | nom du modèle |
| `commande_cmdc` | nom ou chemin du CLI Command Code (`cmdc` par défaut) |
| `effort` | effort de raisonnement, si le backend le gère |
| `veille_heure` | `"09:00"` pour une veille quotidienne automatique (vide : désactivée) |
| `taille` | taille du renard, de 2 à 6 (3 par défaut) |
| `url_http`, `cle_http` | backend HTTP (compatible OpenAI) |
| `flux` | réponse écrite au fil de l'eau |
| `acp` | agent persistant pour le backend `cmdc` |

`cle_http` accepte `$VARIABLE` pour lire la clé depuis l'environnement plutôt que de
l'écrire en clair. `roux_config.json` n'est pas versionné, précisément pour ça.

La vie du renard est sauvegardée dans `renard_save.json`, non versionné lui aussi.

## Backends

- **`cmdc`** — le CLI Command Code (`npm i -g command-code`, puis `cmd login`).
- **`http`** — toute API compatible OpenAI : LM Studio, Ollama, Gemini, passerelle. C'est
  le seul backend qui accepte les appels d'outils. Pour Gemini :

  ```json
  "url_http": "https://generativelanguage.googleapis.com/v1beta/openai",
  "cle_http": "$GEMINI_API_KEY",
  "modele": "gemini-3.8-flash"
  ```

- **`anthropic`** — via la variable d'environnement `ANTHROPIC_API_KEY`.

## Pilotage depuis un agent (MCP)

`compagnon_mcp.py` est un serveur MCP : il parle au renard par une petite API locale
(127.0.0.1, jeton écrit dans `roux_api.json`). Roux doit tourner en même temps.

```
cmdc mcp add roux -- python "chemin\vers\compagnon_mcp.py"
```

Il expose l'état, la nourriture, les caresses, la parole, le dodo, les rappels, le
pomodoro, les leçons et la veille.

> Sous Windows, si un chemin contient un espace, entoure-le de guillemets dans la
> configuration : les arguments sont concaténés sans être protégés avant d'être passés au
> shell, et un chemin non protégé est coupé au premier espace.

## Démarrage automatique

```
python compagnon.py --demarrage on
```

`off` et `status` sont également acceptés.

## Structure

| Fichier | Rôle |
| --- | --- |
| `compagnon.py` | interface Tk, rendu matriciel, souris, boucle `after()` |
| `roux_core.py` | configuration, état du renard, mesures système, météo, veille RSS |
| `roux_llm.py` | backends (CLI, HTTP, Anthropic) et outils |
| `compagnon_mcp.py` | serveur MCP stdio |
| `scripts/` | scripts de maintenance que le renard est autorisé à lancer, et rien d'autre |

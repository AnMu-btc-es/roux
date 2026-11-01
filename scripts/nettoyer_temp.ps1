# Vide les fichiers temporaires de l'utilisateur vieux de plus de sept jours.
# Roux ne peut lancer que les scripts presents dans ce dossier, et rien d'autre.

$avant = (Get-ChildItem $env:TEMP -Recurse -File -ErrorAction SilentlyContinue |
          Measure-Object Length -Sum).Sum
Get-ChildItem $env:TEMP -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force -ErrorAction SilentlyContinue
$apres = (Get-ChildItem $env:TEMP -Recurse -File -ErrorAction SilentlyContinue |
          Measure-Object Length -Sum).Sum
$gagne = [math]::Round((($avant - $apres) / 1MB), 1)
"Temp nettoye : $gagne Mo liberes."

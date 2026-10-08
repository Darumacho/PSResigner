# PSResigner — Journal de session

Ce fichier résume la conversation et les décisions prises, pour reprendre le travail dans ce dossier même si la session Claude est coupée.

## Objectif du projet

Créer un **resigner de sauvegardes PS3** maison, plus accessible et surtout **universel** (doit tourner sur Mac) que le "[PS3] Save Resigner" de KG actuellement utilisé (`E:\The Prince of Codes\[PS3] Save Resigner`, Windows-only).

## Décisions techniques (validées par l'utilisateur)

| Question | Choix |
|---|---|
| Interface | **CLI + GUI** (un cœur CLI réutilisable, une GUI légère par-dessus) |
| Langage | **Python** (portable Mac/Windows, packageable via PyInstaller) |
| Base cryptographique | **Porter la logique d'Apollo Save Tool** plutôt que réinventer |

## Recherche : comment fonctionne réellement le resigning PS3

En remontant le code d'Apollo Save Tool (`bucanero/apollo-ps3`, GPL), on arrive à sa source d'origine : **pfdtool** de flatz (2012, GPL), mirroré intégralement sur `bucanero/pfd_sfo_tools`. C'est la référence qu'on a portée en Python.

Fichiers sources C lus en détail durant la recherche (téléchargés temporairement, pas conservés dans le repo) :
- `pfdtool/src/pfd.c`, `pfd_internal.h`, `backend.c`, `util.c`, `main.c` (algorithme complet)
- `apollo-ps3/source/pfd_util.c` (contient les clés, obfusquées par XOR)

### Format PARAM.PFD

```
[header 16B: magic u64 "PFDB" + version u64 (3 ou 4)]
[header_key 16B, en clair]
[signature 64B, chiffrée AES-128-CBC(key=syscon_manager_key, iv=header_key)]
    -> une fois déchiffrée : bottom_hash(20) + top_hash(20) + hash_key(20) + 4 octets de padding
[hash_table: capacity/num_reserved/num_used (u64 x3) + capacity x u64 (table de buckets)]
[entry_table: num_reserved entrées de 272 octets chacune]
    -> additional_index(8) + file_name(65) + pad(7) + key(64, chiffré) + file_hashes[4](20 chacun) + pad(40) + file_size(8)
[entry_signature_table: capacity x 20 octets (HMAC-SHA1 par bucket)]
```

### Calcul des hash

- PFD v4 : `real_hash_key = HMAC-SHA1(keygen_key, hash_key)`. PFD v3 : `real_hash_key = hash_key` directement.
- `top_hash = HMAC-SHA1(real_hash_key, hash_table_bytes)`
- `bottom_hash = HMAC-SHA1(real_hash_key, entry_signature_table_bytes)`
- Pour chaque bucket occupé : `entry_signature_table[bucket] = HMAC-SHA1(real_hash_key, concat(file_name + key + file_hashes))` pour toute la chaîne d'entrées de ce bucket ; sinon `HMAC-SHA1(real_hash_key, b"")`.
- PARAM.SFO a 4 hash de fichier (`file_hashes[0..3]`) :
  - `FILE` : clé fixe `savegame_param_sfo_key`
  - `FILE_CID` : clé = **console_id cible (IDPS, 16 octets)** — c'est le lien console
  - `FILE_DHK_CID2` : `disc_hash_key` (généralement la valeur de fallback, sauf clé spécifique par jeu)
  - `FILE_AID_UID` : clé fixe `authentication_id` pour une sauvegarde classique (⚠️ ne dépend donc PAS du compte pour une savegame — seuls les trophées utilisent `user_id`, le slot utilisateur local PS3 "00000001" etc.)
- Les autres fichiers (hors PARAM.SFO) n'ont qu'un seul hash `FILE`, avec une clé fixe par nom de fichier (TROPSYS.DAT, TROPUSR.DAT, etc.) ou dérivée d'un "secure file ID" propre au jeu (non supporté pour l'instant — concerne surtout les saves avec fichiers de données supplémentaires signés).

### Resigner = exactement `pfdtool --update <dossier>`

1. `pfd_import` : déchiffrer le bloc signature avec `syscon_manager_key`/`header_key`
2. Remplacer `console_id` (et éventuellement `user_id`) dans la config par la cible désirée
3. Recalculer les `file_hashes` de PARAM.SFO (au moins `FILE_CID`) à partir du contenu réel du fichier sur disque
4. Reconstruire la chaîne HMAC par bucket → `entry_signature_table`
5. Recalculer `bottom_hash` / `top_hash`
6. Rechiffrer le bloc signature (même `header_key`, pas besoin de le changer) et réécrire `PARAM.PFD`

Aucune étape ne nécessite de déchiffrer le contenu réel des fichiers de sauvegarde (SAVEDATA.BIN etc.) — seulement du HMAC-SHA1 et une opération AES-CBC sur le bloc de signature de 64 octets.

### Origine et vérification des clés

Les clés "secrètes" (`syscon_manager_key`, `keygen_key`, `savegame_param_sfo_key`, `trophy_param_sfo_key`, les 4 clés `trop*`, `authentication_id`, `fallback_disc_hash_key`) sont embarquées dans `apollo-ps3/source/pfd_util.c`, obfusquées par un simple XOR répétitif avec la clé `D4 D1 6B 0C 5D B0 87 91`. Décodées en Python, la valeur obtenue pour `authentication_id` (`1010000001000003`) correspond **exactement** à la valeur par défaut, en clair, du fichier `global.conf` fourni avec pfdtool — ce qui confirme que le décodage est correct. Ces clés sont déjà publiques dans toute la scène homebrew PS3 (elles sont embarquées dans Apollo Save Tool, multiMAN, etc., tous open source).

## État du code

Dossier : `C:\Users\adri1\Documents\PSResigner`

- `ps3save/__init__.py` — version du package
- `ps3save/keys.py` — **fait**, constantes décodées et vérifiées (tailles correctes : 8/16/20 octets selon la clé)
- `ps3save/pfd.py` — **fait**, portage complet :
  - `PfdConfig` : console_id / user_id cible + toutes les clés (valeurs par défaut = celles de `keys.py`)
  - `PfdFile` : parsing du PARAM.PFD (header, signature, hash_table, entries), `check_file_hashes()` (vérifier une save existante), `recompute_file_hashes()`, `recompute_structure()`, `resign()` (combine les deux), `save()` (réécrit le fichier avec rechiffrement)
  - **Pas encore testé contre un vrai PARAM.PFD** — c'est la prochaine étape critique avant d'aller plus loin
- Pas encore fait : `ps3save/cli.py`, `ps3save/gui.py`, `ps3save/sfo.py` (lecteur PARAM.SFO pour affichage), `pyproject.toml`, `README.md`
- Pas de dépôt git initialisé pour l'instant

## Dépendances prévues

- `pycryptodome` (AES-CBC — seule dépendance externe non-stdlib nécessaire)
- `hashlib`/`hmac` (stdlib, pour tout le HMAC-SHA1)
- `tkinter` (stdlib, pour la GUI — déjà inclus avec Python sur Mac/Windows)

## Test contre de vraies sauvegardes — fait, succès

L'utilisateur a déposé 4 vraies sauvegardes PS3 dans `test/` (non versionnées, pas dans le repo) :
`BCES01741-PROFDATA-0` (v4, 33 fichiers, trophées non), `BCES01741-SYSDATA` (v4), `BLES01524SYSTEM_SAVE` (v3), `MRTC0000210` (v3).

Deux bugs trouvés et corrigés dans `pfd.py` :
1. `PfdConfig.user_id` : `b"00000000".encode("ascii")` plantait (`.encode` sur des `bytes`) → remplacé par `b"00000000"`.
2. `_entry_data_block()` ne hashait que `key(64) + file_hashes(80)` (144 octets) alors que l'algorithme réel hash `key(64) + file_hashes(80) + pad(40) + file_size(8)` = **192 octets**, c'est-à-dire tout ce qui suit `file_name`/son padding de 7 octets jusqu'à la fin de l'entrée (272 octets). Trouvé par recherche exhaustive des offsets de sous-tranche qui reproduisent le hash stocké sur une vraie entrée.

Après ces deux fix, validation complète :
- **Round-trip structurel** (`recompute_structure()` sans toucher aux fichiers) : `top_hash` et `bottom_hash` recalculés = ceux stockés, sur les 4 saves, y compris celle à 33 entrées (donc chaînage multi-entrées par bucket géré correctement) et les deux versions (v3 et v4, donc la dérivation `real_hash_key` via `keygen_key` est bonne aussi).
- **Hashes PARAM.SFO à clé fixe** (`FILE`, `FILE_AID_UID`) : `True` sur les 4 saves (non-trophées) — confirme `savegame_param_sfo_key` et `authentication_id`.
- **Resign complet bout en bout** (testé sur `BCES01741-PROFDATA-0`, copié dans un dossier temporaire puis supprimé) : `resign()` vers un nouveau `console_id` arbitraire → `FILE_CID` matche le nouvel IDPS et ne matche plus l'ancien ; la structure reste auto-cohérente après réécriture + relecture.
- Les fichiers hors PARAM.SFO (`DAT-*`, `SAVEDATA`, `SAVE_SYS.DAT`, `DATA0.DAT`) restent non vérifiables : limitation connue, nécessiteraient la base de "secure file ID" par jeu, pas portée.

**Conclusion : le cœur cryptographique (`ps3save/pfd.py`) est validé contre de vraies sauvegardes.** `pycryptodome` a été installé dans l'environnement Python utilisé.

## CLI — fait

`ps3save/cli.py` (argparse, 3 sous-commandes) + `pyproject.toml` (entry point `ps3save`, dépendance `pycryptodome`) :

- `ps3save info <dossier>` : version PFD, trophée ou non, liste fichiers + tailles.
- `ps3save check <dossier> [--console-id HEX] [--user-id N]` : affiche OK/MISMATCH/SKIPPED par fichier et par type de hash (`FILE`/`FILE_CID`/`FILE_DHK_CID2`/`FILE_AID_UID`), exit code 1 si un hash vérifiable ne matche pas.
- `ps3save resign <dossier> --console-id HEX [--user-id N] [--output DIR]` : rebind vers un nouvel IDPS ; sans `--output`, modifie en place avec backup `PARAM.PFD.bak` (désactivable via `--no-backup`) ; avec `--output`, copie le dossier source intact et resigne la copie.

Testé sur les vraies saves de `test/` : resign vers un IDPS arbitraire → check avec ce même IDPS = tout OK, check avec l'ancien (zéro) = `FILE_CID` passe en MISMATCH comme attendu. Testé aussi en mode in-place avec backup.

`--console-id` accepte les espaces dans la chaîne hex (ex. `"11223344 55667788 99AABBCC DDEEFF00"`). `--user-id` accepte un entier (converti en `%08d`) ou une chaîne de 8 caractères.

## Lecteur PARAM.SFO — fait

`ps3save/sfo.py` : parseur lecture seule du format PARAM.SFO (header 20 octets, table d'index 16 octets/entrée, table de clés, table de données). Format vérifié champ par champ contre les 4 vraies saves (dump brut des entrées avant d'écrire la classe) :
- `0x0404` = int32 LE (`ATTRIBUTE`, `PARENTAL_LEVEL`)
- `0x0204` = string UTF-8 NUL-terminée (`TITLE`, `SUB_TITLE`, `CATEGORY`, `SAVEDATA_DIRECTORY`, `SAVEDATA_LIST_PARAM`, `DETAIL`)
- `0x0004` = bytes bruts (`ACCOUNT_ID`, `PARAMS`, `PARAMS2`)

Titres décodés correctement sur les 4 saves (ex. "God of War: Ascension™ (EU)", le ™ UTF-8 est bien présent dans les octets — un éventuel affichage "�" n'est qu'un souci d'encodage de la console Windows, pas du parsing).

`cli.py info` affiche maintenant Title/Sub-title/Account ID issus du SFO en plus des infos PFD.

## GUI (tkinter) — fait

`ps3save/gui.py`, 100% stdlib (tkinter) + `pfd.py`/`sfo.py`, pas de dépendance GUI supplémentaire. Fenêtre unique :
- Choix du dossier de save (`Browse...`)
- Panneau infos (Title/Sub-title/version PFD/Trophée/Account ID + liste des fichiers avec taille)
- Champs "Target identity" (console_id hex 32 car. + user_id)
- Boutons `Check hashes`, `Resign (in place)` (avec backup `.bak` + confirmation), `Resign to new folder...`
- Zone de log

Point d'entrée `ps3save-gui` ajouté dans `pyproject.toml` (`[project.gui-scripts]`).

**Testé visuellement** : lancé le process, capturé son contenu via `PrintWindow` (nécessaire car une autre fenêtre plein écran de l'utilisateur occupait le premier plan — `SetForegroundWindow`/capture d'écran classique ne fonctionnaient pas, capturaient l'autre appli). Layout confirmé propre.

**Testé fonctionnellement** (script pilotant directement les méthodes de `App`, sans passer par de vrais clics) :
- Chargement d'un dossier réel → Title/Sub-title/version/trophée/liste fichiers tous corrects dans l'UI.
- `on_check()` → log affiche bien OK/MISMATCH/SKIPPED par fichier (mismatch attendu avec IDPS par défaut à zéro).
- `_do_resign()` (chemin utilisé par les deux boutons resign) → testé sur une copie de save, hash `FILE_CID` repasse à `True` après resign vers un nouvel IDPS/user_id.

Les boîtes de dialogue modales (`askyesno`, `askdirectory`) n'ont pas été testées par clic réel (bloquantes pour l'automatisation), seule la logique métier derrière a été exercée directement.

## Git — fait

Dépôt initialisé (`git init`), `.gitignore` (exclut `__pycache__/`, `build/`, `dist/`, et surtout `/test/` — les vraies saves de l'utilisateur sont des données personnelles, pas versionnées). Premier commit avec tout le code existant (`ps3save/`, `pyproject.toml`, `SESSION_LOG.md`).

**L'utilisateur va continuer sur son Mac en reprenant ce dépôt git** pour la distribution macOS — c'est la source de vérité à partir de maintenant, pas seulement ce journal.

## Distribution Windows (PyInstaller) — fait, testée

But : un premier build Windows autonome, avant que l'utilisateur fasse l'équivalent sur Mac.

- `pyinstaller` installé.
- Deux scripts bootstrap à la racine (nécessaires car `ps3save/gui.py` et `ps3save/cli.py` utilisent des imports relatifs, donc ne peuvent pas être pointés directement par PyInstaller) :
  - `run_gui.py` → `from ps3save.gui import main`
  - `run_cli.py` → `from ps3save.cli import main`
- Builds (`--onefile`) :
  - `pyinstaller --onefile --windowed --name PSResigner run_gui.py` → `dist/PSResigner.exe` (~13 Mo)
  - `pyinstaller --onefile --console --name PSResignerCLI run_cli.py` → `dist/PSResignerCLI.exe`
- Les fichiers `.spec` générés (`PSResigner.spec`, `PSResignerCLI.spec`) sont commités : pas de chemin absolu dedans, donc réutilisables tels quels sur Mac (`pyinstaller PSResigner.spec`) pour garder les mêmes options (`console=False`, etc.) — seules les options de signature (`codesign_identity`, `entitlements_file`, actuellement `None`) seront à renseigner côté Mac.

**Tests effectués sur les .exe buildés (pas juste le code source)** :
- `PSResigner.exe` lancé seul (sans environnement Python actif) → fenêtre identique à la version source (capturée via `PrintWindow`, cf. méthode de capture développée plus haut dans la session GUI).
- `PSResignerCLI.exe` exercé bout en bout sur une copie de vraie save (`info` / `check` / `resign --console-id ...` / `check` avec le nouvel ID) → tout correct, confirme que `pycryptodome` (AES-CBC) est bien embarqué et fonctionnel une fois figé par PyInstaller.

## Prochaine étape proposée

Côté Mac (utilisateur) : builder avec les mêmes `.spec`/scripts, s'occuper de la signature/notarisation si besoin (sans compte Apple Developer, Gatekeeper bloquera au premier lancement — contournable via clic droit → Ouvrir). Éventuellement : tester le CLI/la GUI sur une vraie console PS3 ou comparer avec le resigner de KG pour une validation finale "terrain".

## Références (à re-télécharger si besoin, non copiées dans ce repo)

- https://github.com/bucanero/pfd_sfo_tools (pfdtool original de flatz, GPL) — fichiers clés : `pfdtool/src/pfd.c`, `pfd_internal.h`, `backend.c`, `util.c`
- https://github.com/bucanero/apollo-ps3 (Apollo Save Tool, GPL) — fichier clé : `source/pfd_util.c` (contient les clés obfusquées par XOR)

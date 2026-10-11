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

## Note

À partir de maintenant, le dépôt git (recréé manuellement par l'utilisateur, cf. plus bas) est la source de vérité du code — ce journal garde le fil narratif/les décisions mais peut décrocher des détails exacts du code au fil des commits.

## Dépôt git recréé manuellement par l'utilisateur

L'utilisateur a défait le dépôt git qu'on avait initialisé (`rm -rf .git`, à sa demande, pour le recréer lui-même). Petit incident au passage : une tentative de `git remote add` a échoué avec `'https-//github.com/Darumacho/PSResigner.git' is not a valid remote name` — diagnostiqué : l'URL avait un tiret au lieu du `:` (`https-//` au lieu de `https://`), et/ou le nom du remote (`origin`) avait été omis donc l'URL s'est retrouvée interprétée comme le nom. Pas d'action corrective appliquée (demande explicite de l'utilisateur : diagnostiquer seulement, ne rien committer). Le repo existe maintenant (confirmé en lecture seule), sans remote configuré à ce stade.

## Resign de l'Account ID (comme KG's Save Resigner) — fait

L'utilisateur a montré 2 captures d'écran de l'interface de KG's Save Resigner : la fenêtre principale (Console ID / User ID / Account ID à éditer sous "Owner", plus Region/Copy Protection) et la fenêtre "Profiles" (liste de profils Nom+UserID+AccountID, éditables, chargeables depuis PARAM.SFO).

**Point de départ** : on savait déjà (session précédente) que le champ `ACCOUNT_ID` du PARAM.SFO (16 car. ASCII hex) n'intervient dans AUCUN hash PFD pour une save classique (seul `authentication_id`, clé fixe, compte pour `FILE_AID_UID`) — donc changer l'account ID ne touche que le contenu du PARAM.SFO (et son hash `FILE`, déjà recalculé automatiquement par `recompute_file_hashes()`). Mais le dump du champ `PARAMS` (1024 octets) fait lors de la session précédente montrait une copie de l'account ID noyée dans une structure binaire pas encore comprise — forcément ce que l'outil de KG affiche réellement (bien plus long que 16 car.).

**Recherche (avant d'écrire du code)** :
- Code source d'Apollo Save Tool (`bucanero/apollo-ps3/source/sfo.c`, `include/sfo.h`, via GitHub raw — pas de `gh` CLI dispo dans l'environnement) : trouvé `sfo_patch_account()` (patch `ACCOUNT_ID` + `PARAMS.account_id`) et `sfo_patch_user_id()` (patch `PARAMS.user_id_1`/`user_id_2`), et la struct exacte :
  ```c
  struct sfo_param_params_t {
      u8  unk1[12]; u32 unk2, unk3, unk4;
      u32 user_id_1;        // offset 24
      u8  psid[16];         // offset 28 — identifiant PS3 physique, pas édité par KG
      u32 user_id_2;        // offset 44
      u8  account_id[16];   // offset 48
  };
  ```
- **L'utilisateur a aussi pointé vers le dossier de KG's Save Resigner** (`E:\The Prince of Codes\[PS3] Save Resigner`), qui bundle littéralement `pfdtool.exe` original de flatz (confirme notre portage) + `global.conf`/`games.conf`. `pfdtool.exe --help` confirme qu'il ne gère QUE le PFD (console_id/user_id + `-g` pour `games.conf`), donc KG patche l'Account ID dans sa propre couche C#, comme Apollo.
- **Validation croisée décisive** : un profil exporté par KG (`Drattak.txt`, contenu `0000000B` + `545B59EAA36EC696D496DFAC284B60FC0B00000034383631663764313664303337333130`) décodé en Python correspond EXACTEMENT à la struct ci-dessus (`psid` + `user_id`(LE u32=11) + `account_id` ASCII="4861f7d16d037310") — et cet account_id est justement celui des saves `BCES01741-*` de notre dossier `test/`. Confirme la struct au bit près avant même de toucher au code.
- Découverte annexe notée pour plus tard : `games.conf` contient aussi `secure_file_id:*=...` par jeu — c'est exactement la pièce manquante pour lever notre limitation sur les fichiers hors PARAM.SFO (`DAT-*` etc., actuellement `SKIPPED`). Pas implémenté maintenant, juste noté.

**Implémentation** :
- `ps3save/sfo.py` réécrit : garde le buffer brut + les offsets de chaque entrée (au lieu de ne garder qu'un dict de valeurs décodées), pour pouvoir patcher en place. Ajout de `SfoFile.set_account_id(hex16)` et `SfoFile.set_user_id(int)` (mêmes deux emplacements qu'Apollo), et `SfoFile.save(path)`. API de lecture existante (`.values`, `.get()`, `.title`) inchangée.
- `ps3save/cli.py resign` : nouvelle option `--account-id` (16 car. hex). Patch le PARAM.SFO (account_id + user_id numérique si fourni) avant d'appeler `pf.resign()` — l'ordre compte : le PFD doit rehasher le PARAM.SFO *après* sa modification.
- `ps3save/gui.py` : champ "Account ID (16 hex chars)" ajouté sous Console ID/User ID dans "Target identity", câblé dans `_do_resign()` (utilisé par les deux boutons resign).

**Tests (sur de vraies saves, copies temporaires nettoyées)** :
- Patch seul (`set_account_id`+`set_user_id`) : relu à froid depuis le disque, les bons octets changent aux bons offsets (`ACCOUNT_ID` + `PARAMS[48:64]` + `PARAMS[24:28]`/`PARAMS[44:48]`), `psid` et la taille du fichier inchangés.
- Bout en bout CLI (`resign --console-id ... --user-id 42 --account-id DEADBEEF01234567` puis `check`) : tout `OK`.
- Bout en bout GUI (piloté directement via les méthodes de `App`, cf. méthode de test établie précédemment) : idem, `check_file_hashes` tout `True` après resign.

## Affichage de l'icône + checkbox de backup — fait

- **"Trophy save" expliqué à l'utilisateur** : indique si cette save est un set de trophées (fichiers `TROPSYS.DAT`/`TROPUSR.DAT`/etc.), détecté par `PfdFile.is_trophy`. Ça compte parce que l'algo de hash bifurque sur ce flag (clés fixes différentes, `console_id`/`user_id` mélangés via `_shuffle16` pour les trophées au lieu de clés fixes pour une save classique).
- **Icône du jeu dans la GUI** : `ICON0.PNG` chargé via `tk.PhotoImage` natif (Tk 8.6+ lit le PNG directement, pas besoin de Pillow) dans `refresh_info()`, réduit par paliers de 2 (`.subsample(2,2)`) jusqu'à ≤160px de large (icônes PS3 habituellement 320×176). Affiché à droite de la liste de fichiers. Référence gardée dans `self.icon_image` (sinon le GC de Tk la jette et l'image disparaît).
- **Checkbox "Backup save folder before resigning in place"** (cochée par défaut) : corrige au passage un vrai manque — `PARAM.SFO` est modifié en place depuis l'ajout de l'Account ID, et n'était couvert par aucun backup (seul `PARAM.PFD.bak` existait). Remplacé par une copie de **tout le dossier** vers `<nom>_backup_AAAAMMJJ_HHMMSS` avant toute modification, côté GUI (checkbox) et CLI (`--no-backup` désormais équivalent).

## Fenêtre "Profiles" — fait

Nouveau module `ps3save/profiles.py` : dataclass `Profile(name, user_id, account_id)` + `load_profiles()`/`save_profiles()` en JSON dans `~/.ps3save/profiles.json` (cross-platform, aucune dépendance).

`sfo.py` : ajout de `SfoFile.get_owner() -> (user_id, account_id)` pour relire ce que `set_user_id()`/`set_account_id()` écriraient — sert à préremplir un profil depuis la save chargée.

`gui.py` : bouton "Profiles..." dans "Target identity" → ouvre `ProfilesDialog` (Toplevel) : liste des profils (Listbox), champs Nom/UserID/AccountID, boutons `Load From PARAM.SFO` (lit la save actuellement chargée dans la fenêtre principale via `app.save_dir`), `Save Profile Changes`, `Add New Profile`, `Delete`, et `Use in main window` (ou double-clic sur une ligne) qui recopie User ID/Account ID dans la fenêtre principale.

**Bug trouvé et corrigé pendant les tests** : `_refresh_list()` vide puis repeuple la Listbox, ce qui réinitialise silencieusement sa sélection — un enchaînement "Save Profile Changes" puis "Delete" perdait la sélection et le delete échouait silencieusement (`messagebox.showwarning` "No profile selected"). Fix : `_refresh_list(select_index=...)` restaure la sélection après reconstruction ; `on_save_changes`/`on_add_new` repassent l'index pertinent.

**Testé** : scénario complet piloté directement sur `ProfilesDialog` (add → save changes → delete, persistance JSON vérifiée à chaque étape) + capture d'écran réelle (liste avec "Drattak"/"Tyranocif", sélection d'un profil préremplissant bien les champs). Capture a aussi révélé que la fenêtre (420px) coupait le bouton "Delete" — élargie à 520px, revérifié visuellement.

## Convention de travail à ne pas perdre

**Recompiler les `.exe` (PyInstaller) après chaque modification de code**, sans attendre que l'utilisateur le demande explicitement — il veut voir l'avancement du côté "résultat packagé", pas juste le comportement en source. Côté Mac, l'équivalent est de rebuilder avec les mêmes `.spec` (`PSResigner.spec`, `PSResignerCLI.spec`) via `pyinstaller PSResigner.spec` etc. (Noté aussi en mémoire persistante côté Windows, mais ça ne traverse pas vers le Mac — donc ça doit rester écrit ici.)

## Prochaine étape (session suivante, sur Mac)

1. **Tester sur une vraie console PS3** (ou comparer avec le resigner de KG) — validation "terrain" prioritaire, surtout pour l'Account ID/Profiles dont la struct `PARAMS` est inférée par rétro-ingénierie (croisée avec un vrai profil KG, mais jamais vérifiée sur hardware réel) et pour `FILE_DHK_CID2` qui ne marche que si le jeu utilise la clé de fallback.
2. **Embellir l'appli** (GUI tkinter actuelle reste fonctionnelle mais basique) — pas encore défini précisément ce que ça couvre (thème, icônes, mise en page, retours visuels...), à discuter.
3. **Réfléchir à la faisabilité d'un resigner PS4** — à explorer : le format de save PS4 est différent (pas de PARAM.PFD/pfdtool, système de sauvegarde PS4 avec son propre système de clés — overlay FAT, mount points, section fichiers signées différemment). Recherche à faire avant toute estimation (pas encore commencée).
4. Support de `games.conf` (`secure_file_id:*=...`) pour lever la limitation sur les fichiers hors PARAM.SFO (reste valable, pas urgent).

## État du dépôt à la fin de cette session

Modifié/ajouté depuis le dernier commit (pas encore commité — l'utilisateur commitera lui-même avant de continuer sur Mac) :
- `SESSION_LOG.md` (ce fichier)
- `ps3save/cli.py` (option `--account-id`, backup dossier complet)
- `ps3save/gui.py` (champ Account ID, icône, checkbox backup, fenêtre Profiles)
- `ps3save/sfo.py` (écriture : `set_account_id`/`set_user_id`/`get_owner`/`save`)
- `ps3save/profiles.py` (nouveau fichier)

## Icône de l'application — fait (début de l'embellissement de l'appli)

L'utilisateur a ajouté `resources/app_logo.png` et `resources/app_logo.ico`.

- `ps3save/gui.py` : helper `_resource_path()` qui résout un chemin de ressource à la fois en lançant depuis les sources (racine du projet, deux niveaux au-dessus de `gui.py`) et une fois figé par PyInstaller (`sys._MEIPASS`). `App.__init__` charge `resources/app_logo.png` via `tk.PhotoImage` et l'applique avec `master.iconphoto(True, ...)` (icône de fenêtre + barre des tâches ; `True` = aussi utilisée par défaut pour les Toplevel comme la fenêtre Profiles). Référence gardée sur `self._app_icon` pour éviter le garbage-collection.
- `PSResigner.spec` / `PSResignerCLI.spec` : `icon=['resources/app_logo.ico']` (icône de l'exe dans l'Explorateur/la barre des tâches) + `datas=[('resources/app_logo.png', 'resources')]` dans `PSResigner.spec` pour que le PNG soit bien extrait au runtime en mode onefile.

Point d'attention soulevé en cours de route (dimensions non carrées observées dans le fichier) puis confirmé réglé par l'utilisateur : le `.ico` est correctement redimensionné. Affichage vérifié correct dans la barre de titre de l'exe buildé.

## Thème clair/sombre — fait

- `ps3save/theme.py` (nouveau) : palettes `LIGHT`/`DARK`, construites sur le thème ttk `"clam"` — le seul thème intégré à la fois cross-platform (contrairement à `"vista"`/`"winnative"`, Windows-only) et entièrement recolorable via `Style.configure` (contrairement à `"aqua"` sur Mac, qui ignore en grande partie les surcharges de couleur). `apply_ttk_style()` configure tous les styles ttk utilisés (`TFrame`, `TLabel`, `TLabelframe`, `TCheckbutton`, `TButton`, `TEntry`, `Treeview`, `TScrollbar`...). Les widgets tk "bruts" (non ttk) — `Text` du log, `Listbox` des Profiles — ne sont pas couverts par `ttk.Style` et sont coloriés à part via `style_text_widget()`/`style_listbox()`.
- `ps3save/settings.py` (nouveau) : persistance JSON minimale (`~/.ps3save/settings.json`), même pattern que `profiles.py`. Stocke `dark_mode`.
- `ps3save/gui.py` : checkbox "Dark mode" dans la rangée du dossier de save. `App.apply_theme()` applique le style + colore le log ; appelé au démarrage (valeur restaurée depuis les settings) et à chaque bascule (`on_toggle_theme()`, qui persiste aussi). `ProfilesDialog` applique le thème courant à son fond (Toplevel brut) et à sa Listbox à la construction.

**Testé visuellement** (capture d'écran, fenêtre principale + Profiles, en clair et en sombre) : rendu cohérent et lisible dans les deux cas — fond, texte, champs, Treeview, boutons, Listbox tous recolorés correctement. Limite connue et acceptée : la barre de titre native (gérée par Windows, pas par Tk) reste claire même en mode sombre — nécessiterait un appel DWM (`DWMWA_USE_IMMERSIVE_DARK_MODE`) spécifique à Windows, pas fait pour l'instant (pas critique, et il faudrait une solution équivalente différente sur Mac). Testé aussi dans l'exe PyInstaller rebuildé (démarre bien en clair par défaut quand `settings.json` n'existe pas encore).

## Premier test sur vraie console — succès, et mémorisation du dernier Console ID

**L'utilisateur a confirmé que le resign fonctionne sur une vraie PS3.** Première validation "terrain" du projet.

Question soulevée : peut-on préremplir automatiquement le Console ID depuis un SFO ? **Non** — expliqué à l'utilisateur que `console_id` n'est stocké nulle part en clair dans la save (ni PARAM.SFO, ni PARAM.PFD) : il n'intervient que comme clé HMAC pour calculer `FILE_CID`, et un HMAC est à sens unique (on ne peut pas remonter à la clé depuis le hash). C'est la propriété de sécurité recherchée par le binding console. Pour connaître son propre IDPS il faut un outil tournant sur la console elle-même (CFW, comme Apollo) ou l'avoir déjà récupéré par un autre moyen.

Implémenté à la place : mémorisation automatique du **dernier Console ID utilisé** (choix simple retenu par l'utilisateur plutôt qu'une liste nommée "Mes consoles").
- `ps3save/gui.py` : `console_id_var` préremplie au démarrage depuis `self._settings.get("last_console_id", "")` (même fichier `settings.json` que le thème). Nouvelle méthode `_remember_console_id()`, appelée après chaque resign réussi (en place ou vers un nouveau dossier), qui persiste la valeur.
- Testé : scénario "session 1" (resign avec un ID) → "session 2" (nouvelle instance `App`) → le champ est bien prérempli avec la valeur de la session précédente.

## Ajustements de layout GUI — faits

Suite à un retour visuel de l'utilisateur (capture d'écran avec titre/sous-titre/account_id coupés dans le panneau "Save info") :
- Colonne des valeurs du panneau "Save info" élargie (`minsize=220`, poids plus important que la colonne du tableau de fichiers) ; fenêtre principale élargie 720→780px pour compenser.
- Colonnes du tableau de fichiers (`file_tree`) resserrées à 110px chacune (nom/taille) plutôt que leurs largeurs par défaut généreuses — testé que "Size (bytes)" reste lisible (ex. `2097152`) après resserrement.
- Label "Trophy save" renommé en **"Trophy set file"** (clé du dict `info_labels` renommée en cohérence).

## Console ID optionnel sur un Profil — fait

Question de l'utilisateur : peut-on associer un Console ID à un Profil (en plus de User ID/Account ID) ? Choix retenu : champ **optionnel**, pour ne pas casser le flux existant (mémorisation globale du dernier Console ID utilisé) quand un profil ne gère qu'un seul compte sur une seule console — utile surtout pour qui jongle avec plusieurs consoles cibles.

- `ps3save/profiles.py` : `Profile.console_id: str = ""` (32 car. hex ou vide), validé dans `validate()` comme les autres champs. Rétrocompatible : les anciens `profiles.json` sans ce champ se rechargent avec `console_id=""` par défaut (dataclass).
- `ps3save/gui.py` : champ "Console ID (optional):" dans `ProfilesDialog`. `on_apply()` ne touche à `app.console_id_var` que si `p.console_id` est non vide — sinon le Console ID actuellement dans la fenêtre principale (ou mémorisé) reste inchangé.

**Testé** : profil avec Console ID → appliquer écrase bien `console_id_var` ; profil sans Console ID → appliquer laisse `console_id_var` intact ; validation rejette correctement un Console ID de mauvaise longueur. Capture d'écran de la fenêtre Profiles avec le nouveau champ confirmée visuellement (layout propre).

## Icônes sur les boutons — fait

L'utilisateur a ajouté 6 PNG 64×64 dans `resources/` : `open-folder.png`, `profiles.png`, `load.png`, `save.png`, `add.png`, `delete.png`.

- `ps3save/gui.py` : helper `_load_icon(name, scale=3)` (charge via `_resource_path`, réduit avec `.subsample(3,3)` — 64×64 est trop grand pour un bouton, pas de redimensionnement lissé possible en Tk pur sans Pillow mais le facteur 3 passe bien pour ces icônes simples/aplats). Icônes chargées une fois dans `App._icons` (fenêtre principale : `Browse...`→`open-folder.png`, `Profiles...`→`profiles.png`) et dans `ProfilesDialog._icons` (`Load From PARAM.SFO`→`load.png`, `Save Profile Changes`→`save.png`, `Add New Profile`→`add.png`, `Delete`→`delete.png`). Boutons avec `image=...` + `compound="left"` pour garder le texte. `Use in main window` volontairement sans icône (demande explicite).
- `PSResigner.spec` : `datas` simplifié pour embarquer tout le dossier `resources/` (`[('resources', 'resources')]`) plutôt qu'un fichier nommé — plus pratique vu le nombre de ressources qui augmente.
- Ajustement de layout : la fenêtre Profiles (520px) ne laissait plus de place pour "Delete" une fois les icônes ajoutées (boutons plus larges) → élargie à 640px.

**Testé** : chargement des icônes confirmé par capture d'écran (fenêtre principale + Profiles, boutons avec icônes bien visibles, tailles cohérentes) ; revérifié aussi dans l'exe PyInstaller rebuildé (le dossier `resources/` complet est bien extrait au runtime en mode onefile).

### Ajustement de taille : 22px → 48px → 16px

L'utilisateur a demandé de passer les icônes à 48×48 pour réduire la taille des boutons (jugés "un peu grands" à l'implémentation initiale ~22px). `_load_icon()` généralisé pour viser une taille exacte via `zoom()`+`subsample()` (facteurs rationnels, pas seulement des diviseurs entiers de 64 — ex. 64→48 = zoom(3) puis subsample(4)).

**Capture à l'appui, 48×48 donnait l'effet inverse** : boutons nettement plus grands qu'avant, pas plus petits. Remonté à l'utilisateur avec la capture avant d'aller plus loin plutôt que de deviner — confirmé : il voulait en fait **16×16** (le malentendu venait du fait qu'il raisonnait par rapport à la taille source 64×64 fournie, pas par rapport au rendu déjà réduit à ~22px). Recompilé et revérifié par capture à 16×16 : boutons compacts, icônes nettes. C'est la taille actuelle.

## Étude de faisabilité : injection de cheats (patch de save)

L'utilisateur a demandé d'étudier la faisabilité d'un **resigner PS4** (Save Wizard for PS4 existe déjà, service payant) puis d'un **système d'injection de cheats**.

### PS4 — abandonné, verdict justifié par du code source réel

Vérifié sur le code source d'Apollo PS4 (`bucanero/apollo-ps4`, le portage PS4 du projet dont on s'est déjà beaucoup inspiré) : **pas de clés crypto en dur dans le code**, contrairement à pfdtool/PS3. Le (dé)chiffrement réel passe par des appels noyau PS4 (`mountSave()`/`umountSave()`, wrappers de `sceSaveData*`) — c'est la PS4 elle-même, avec ses clés matérielles, qui fait le travail. Le README confirme : *"Standalone: no computer required, everything happens on the PS4."* Aucune fuite de clés équivalente à celle de PS3 (~2012) n'a eu lieu pour la PS4. **Conclusion : infaisable comme outil PC pur Python** — c'est très probablement pour ça que Save Wizard facture un service (fait tourner une vraie PS4 côté serveur). Seule alternative réaliste : Apollo Save Tool PS4 tourne déjà gratuitement sur une PS4 jailbreakée, ou alors développement homebrew PS4 (projet complètement différent, hors scope).

### Cheats (patch de save) — faisable, confirmé par un exemple réel

Clarifié avec l'utilisateur via `AskUserQuestion` : patch de save façon Apollo (valeurs connues patchées dans les fichiers, via base de codes communautaire) — **pas** d'injection mémoire live (qui nécessiterait une PS3 jailbreakée en réseau + CCAPI, projet totalement différent).

Vérifié sur le code/les dépôts réels :
- Format `.savepatch` (bucanero/apollo-lib) : blocs `[Nom du cheat]` + lignes de code hexadécimal façon GameGenie/CodeBreaker (type+adresse / valeur), ou dialectes alternatifs BSD (commandes set/write/search/...) et MicroPython pour les cas complexes.
- Exemple réel récupéré pour **BCES01741** (God of War: Ascension, une de nos saves de test) :
  ```
  [Max Red Orbs]
  80010004 00000324
  28000030 49742400
  ```
- Base de données communautaire déjà existante et maintenue : [bucanero/apollo-patches](https://github.com/bucanero/apollo-patches) (fichiers `.savepatch` par ID de jeu, dont `BCES01741.savepatch` existe déjà) — pas besoin de construire notre propre base.
- Référence pour le décodage du format GameGenie/CodeBreaker : crate Rust [`codebreaker`](https://docs.rs/codebreaker) (évite de rétro-ingénierier le bit-layout à l'aveugle).

**Point d'architecture clé** : patcher un fichier de save invalide son hash `FILE` dans PARAM.PFD — un cheat appliqué doit donc être suivi du même rehash que `resign()` (identique au patch d'Account ID déjà en place). Pas de changement nécessaire dans `pfd.py` ; juste une étape de patch des octets *avant* `resign()`.

## Réorganisation GUI en onglets — fait (étape 1, squelette)

Suite à la discussion, l'utilisateur a proposé un onglet dédié aux cheats séparé du resign pur. Accord sur : `Save folder` + panneau `Save info` restent communs au-dessus (les deux onglets agissent sur la même save chargée) ; `Console ID`/`User ID`/`Account ID` restent des champs **partagés** (pas dupliqués par onglet) car appliquer un cheat nécessitera le même rehash que Resign, donc le même contexte d'identité.

- `ps3save/gui.py` : `App` utilise maintenant un `ttk.Notebook` (`_build_notebook()`) avec deux onglets : **Resign** (contenu de l'ancien `_build_target_row()`/`_build_buttons()`, inchangé fonctionnellement) et **Cheats** (`_build_cheats_tab()`, placeholder "coming soon" pour l'instant — le moteur de patch lui-même n'est pas encore implémenté).
- `ps3save/theme.py` : styles `TNotebook`/`TNotebook.Tab` ajoutés (onglet actif coloré avec `select_bg`), manquait pour le mode sombre.

**Testé** : scénario resign complet (console+account id) rejoué après la réorganisation → toujours `check_file_hashes` tout `True`, donc pas de régression. Capture d'écran en clair (onglet Resign) et en sombre (onglet Cheats sélectionné programmatiquement, `notebook.select(1)`, les clics OS simulés via `PostMessage` n'étant pas fiables sur ces fenêtres Tk) : rendu cohérent dans les deux cas.

## Base communautaire de cheats (bucanero/apollo-patches) — fait

But : implémenter la récupération + l'affichage des cheats disponibles pour la save chargée, depuis la vraie base communautaire. L'application effective des patches (décodage des codes Save Wizard/GameGenie) reste **pour une prochaine étape** — volontairement pas fait maintenant, pour garder ce morceau testable isolément.

### Recherche de la syntaxe réelle (avant d'écrire le parseur)

`curl` fonctionne directement depuis Bash (accès réseau direct, pas besoin de passer par WebFetch) — bien plus fiable pour récupérer du texte exact, le résumé automatique de WebFetch tronque/paraphrase le code source au-delà de 125 caractères et s'est avéré peu fiable sur les extraits longs (a carrément halluciné une partie du détail du type de code `'8'` à une étape).

- Récupéré le vrai fichier `PS3/BCES01741.savepatch` (God of War: Ascension, une de nos saves de test) et le code source de `bucanero/apollo-lib/source/patches.c` (fonction `apollo_apply_sw_code`) en entier via `curl` + lecture directe (pas de résumé IA) pour valider la syntaxe exacte.
- Format confirmé :
  - `; commentaire`
  - `:<motif-dossier>\<motif-fichier>` — directive de section, s'applique à tous les cheats suivants jusqu'à la prochaine. Les deux côtés acceptent des wildcards (`*`). Le dossier peut être omis entièrement (`:DATA0.DAT` tout seul = n'importe quel dossier).
  - `[Nom du cheat]` suivi de lignes de code `XXXXXXXX YYYYYYYY` (8 hex + espace + 8 hex).
  - Un seul fichier peut cibler plusieurs dossiers de save différents pour le même jeu (ex. `-PROFDATA-*` et `-SYSDATA` dans le même `.savepatch`).

### Robustesse testée sur 105 vrais fichiers (pas juste nos 2 exemples)

Échantillon aléatoire de 105 fichiers `.savepatch` réels téléchargés (listing via l'API GitHub, graine fixée pour reproductibilité) et passés dans le parseur. Deux bugs réels trouvés et corrigés grâce à cet échantillon large (pas visibles sur nos 2 cas de test initiaux) :
1. Directive sans dossier (`:DATA0.DAT`) mal interprétée comme *dossier* au lieu de *fichier* → corrigé (si pas de `\`, c'est le fichier, dossier = `*`).
2. En-têtes organisationnels sans code associé (`[group:ICO]`, `[Group:...]`, `[INFO:...]`, parfois avant toute directive `:`) remontaient comme de fausses "cheats" vides → corrigé en filtrant les entrées sans ligne de code au moment du parsing (elles ne représentent rien d'applicable de toute façon). Au passage, découverte d'un cas réel de nom de cheat sans crochets (`Max WP/Money`, fichier `BLES00951.savepatch`) — le parseur accepte maintenant ce cas en repli (une ligne qui n'a pas la forme d'un code hex et qu'on rencontre hors cheat actif = nom de cheat implicite).

Résultat final : **3755 cheats parsés sans erreur ni entrée invalide** sur les 105 fichiers testés.

### Implémentation

- `ps3save/cheats.py` (nouveau) : `extract_product_code()` (regex `[A-Z]{4}\d{5}` sur le nom du dossier de save), `fetch_savepatch()` (téléchargement HTTPS stdlib `urllib`, avec cache local sous `~/.ps3save/cheats_cache/`), `parse_savepatch()`, `CheatEntry` (avec `matches_folder()` et `matching_files()` via `fnmatch`), `list_cheats_for_save()` (combine tout : code produit → fetch → parse → filtre par dossier réellement chargé).
- `ps3save/gui.py` : l'onglet Cheats (placeholder de l'étape précédente) affiche maintenant une vraie liste (Treeview Fichier(s) cible / Nom du cheat), un statut, et un bouton "Reload". Appelé automatiquement via `refresh_info()` à chaque chargement de save. Les fichiers multiples (`DAT-*` → 31 fichiers) sont résumés (`DAT-* (31 files)`) plutôt que listés en toutes lettres.

**Testé** : bout en bout sur 3 vraies saves de test (God of War Ascension ×2 dossiers + Ninja Gaiden 3, ce dernier ayant lui aussi des cheats dans la base — découvert en testant) → listes correctes, capture d'écran confirmant le rendu. Vérifié aussi que `urllib`/HTTPS fonctionne bien une fois figé par PyInstaller (bundle de test séparé, pas de souci de certificats SSL comme ça peut arriver avec PyInstaller).

## Décodeur/applicateur de codes Save Wizard — fait

But : pouvoir réellement appliquer un cheat (pas juste le lister), en portant la logique de `apollo_apply_sw_code()` en Python.

### Recherche complémentaire (endianness — point critique trouvé avant de coder)

En creusant les fonctions utilitaires d'`apollo-lib` (`include/types.h`), découverte importante : Apollo utilise par défaut l'endianness **réelle de la machine sur laquelle il tourne** (`apollo_get_host_endianness()`), pas une valeur fixée. Comme Apollo tourne *sur* la PS3 elle-même (PowerPC, big-endian), ce défaut est **big-endian en pratique pour toute vraie save PS3** — un `.savepatch` peut le surcharger via des tags `[LE:]`/`[BE:]`, non rencontrés dans les 105 fichiers testés, donc pas gérés pour l'instant (documenté comme limitation connue plutôt que silencieusement ignorés).

Ça simplifie énormément le portage Python : avec big-endian comme convention, écrire/chercher un code hex revient à prendre `bytes.fromhex(...)` directement, sans aucune gymnastique d'endianness (le code C fait cette gymnastique uniquement pour être indépendant de la machine hôte — un problème qu'on n'a pas en Python).

### Implémentation

`ps3save/cheats.py` : `apply_sw_codes(data, codes) -> bytes`, types supportés :
- **0/1/2** : écriture 8/16/32-bit directe (`XTAAAAAA VVVVVVVV`, T=8 → relatif au pointeur).
- **3** : incrément/décrément (nibble encode taille + add/sub + relatif pointeur ou non).
- **8** : recherche d'un motif (N-ième occurrence, motif multi-lignes si > 4 octets) → positionne un pointeur interne.
- **9** : manipulation du pointeur (lecture BE/LE à une adresse, +/-, set direct).

Tout code d'un type non supporté (4/5/6/7/A-F, dialectes BSD/MicroPython) ou mal formé déclenche `UnsupportedCodeError` explicite plutôt que d'être ignoré ou mal appliqué silencieusement — pareil pour un pattern de recherche introuvable (ne touche jamais au fichier avant d'être sûr de pouvoir appliquer tout le code).

**Validation de couverture réelle** (échantillon de 60 jeux, 2789 cheats) : **93.2% des cheats réels n'utilisent QUE les types déjà supportés**. Type `4` (multi-write) est le plus gros morceau restant (148 occurrences sur l'échantillon) si on veut aller plus loin un jour ; `5`/`6`/`7`/`D`/`F` sont anecdotiques (<25 occurrences cumulées).

**Tests** :
- Synthétique, un cas par type (0/1/2/3/8/9) + écriture relative au pointeur + erreurs (type inconnu, ligne mal formée, hors limites) — tous corrects.
- Bout en bout réel : le cheat "Max Red Orbs" de `BCES01741` rejoué à la main (recherche `00 00 03 24` → écriture `49 74 24 00` à pointeur+0x30) sur une vraie save de test (motif injecté manuellement puisque cette save précise ne le contient pas naturellement) → octets corrects au bon offset, puis `check_file_hashes` repasse tout à `True` après le resign qui suit automatiquement.

### UI (onglet Cheats)

Sélectionner une ligne peuple un menu déroulant "Apply to file" (les fichiers réellement présents correspondant au motif, ex. `DAT-1` à `DAT-31`) ; bouton "Apply Cheat" qui patch le fichier choisi puis déclenche le même pipeline que `Resign` (réutilise `_do_resign`, `console_id`/`account_id`/`backup_var` déjà partagés — cf. décision prise avant de construire les onglets). Factorisé `_backup_save_dir()` pour éviter la 3ᵉ copie du même bloc de code (Resign en place + Apply Cheat).

## 🔴 BUG CRITIQUE trouvé sur console réelle : "Apply Cheat" corrompait la save — corrigé

**L'utilisateur a testé "Max Bolts" sur une vraie save (Ratchet & Clank 3, `NPEA00387_RC3_SAVEDATA_A`) → erreur PS3 80029207 au chargement ("save corrompue").**

### Diagnostic

Confirmé par recherche : `80029207` = erreur standard PS3 de donnée de save corrompue.

Cause root, reproduite et prouvée en local (sur une copie, avec un `console_id` de test arbitraire — la valeur exacte ne change rien au diagnostic) :
- `USR-DATA` (comme `DAT-*`, `SAVE_SYS.DAT`, etc.) n'a pas de clé de hash fixe connue — c'est la limitation "secure file ID par jeu" documentée depuis le tout début du projet, jusqu'ici jugée "pas urgente".
- `PfdFile.recompute_file_hashes()` : quand la clé est inconnue, elle `continue` silencieusement (ne met pas à jour `entry.file_hashes[HASH_FILE]`) **mais met quand même à jour `entry_signature_table`/`top_hash`/`bottom_hash`** à partir de ce hash resté périmé.
- Preuve directe : hash `USR-DATA` strictement identique avant et après patch+resign (`410e281d...` dans les deux cas), alors que le contenu réel du fichier avait changé. Le PFD reste "structurellement cohérent" (le hash périmé est bien chaîné partout) mais ne correspond plus au vrai contenu → la PS3 rejette la save au chargement.
- **Conséquence grave** : quasiment aucun fichier de save réel (seuls PARAM.SFO et les `TROP*.DAT` fixes sont couverts) ne pouvait être patché "Apply Cheat" en toute sécurité — la fonctionnalité, comme livrée, était un piège pour presque tous les vrais jeux.

### Correctif (livré immédiatement, avant toute autre chose)

- `ps3save/pfd.py` : nouvelle méthode publique `PfdFile.can_rehash_file(file_name) -> bool` (sonde `_entry_hash_key` pour chaque hash applicable, retourne `False` si une clé manque).
- `ps3save/gui.py` :
  - `on_apply_cheat()` **refuse désormais explicitement** de patcher un fichier dont `can_rehash_file()` est `False`, avec un message clair citant l'erreur 80029207, *avant* la moindre confirmation/écriture (vérifié : aucune boîte de dialogue de confirmation atteinte, fichier intact).
  - La liste de l'onglet Cheats marque désormais chaque cheat non-applicable avec `⚠` + un compteur dans le statut (`refresh_cheats()` appelé *après* le rechargement de `self.pf`, pas avant — réordonnancement nécessaire dans `refresh_info()` pour que `can_rehash_file()` dispose du bon `PfdFile`).

**Testé** : le cas réel (RC3/`USR-DATA`) et le cas déjà connu (BCES01741/`DAT-*`) sont bien tous deux marqués `⚠` dans la liste, et une tentative d'`on_apply_cheat()` sur `USR-DATA` est bloquée avant toute écriture (vérifié avec `unittest.mock` sur `messagebox.showerror`/`askyesno`).

**Mise à jour : pas de perte de données** — l'utilisateur avait fait son propre backup avant de tester.

## ✅ Correction de fond : base `secure_file_id` de `games.conf` — implémentée

L'utilisateur a demandé directement : "Est-ce qu'on a moyen d'avoir un fix pour le souci du hash ?" — donc, lever la limitation à la racine plutôt que de se contenter du garde-fou.

### Recherche : comment pfdtool (PC, hors-ligne) utilise `secure_file_id`

Contrairement à Apollo-PS4 (qui a besoin du noyau de la console), **pfdtool fait ça entièrement en pur logiciel sur PC** — exactement notre architecture. Récupéré `pfd.c`/`main.c`/`backend.c` du dépôt original (`bucanero/pfd_sfo_tools`) via `curl` :

- `games.conf` associe des codes produit (`[CODE1/CODE2/.../CODEn]`) à un `secure_file_id` (16 octets hex) par motif de nom de fichier (`secure_file_id:*=...` ou un nom précis), plus un `disc_hash_key` optionnel par jeu (souvent commenté = "utiliser le fallback").
- `pfd_generate_hash_key_for_secure_file_id()` (pfd.c:160) transforme ces 16 octets en la vraie clé HMAC de 20 octets via **exactement le même "byte-interleave"** qu'on utilise déjà pour les trophées (`_shuffle16` / `_SHUFFLE_OVERRIDES = {1:11, 2:15, 5:14, 8:10}`) — cette constante était déjà présente dans `pfd.py` depuis la toute première session, **jamais câblée**, en attente exacte de cette fonctionnalité.

### Validation manuelle avant d'écrire le code

KG's `games.conf` (dans `[PS3] Save Resigner`) contient les 4 jeux de test. Calcul à la main (`shuffle(secure_file_id, 20, overrides)` puis HMAC-SHA1) → **correspond exactement** au hash déjà stocké dans le vrai PARAM.PFD de chacune de nos 4 saves (`DAT-31`, `DATA0.DAT`, `SAVE_SYS.DAT`, et surtout `USR-DATA` : `410e281d...`, identique à la valeur périmée repérée dans le diagnostic du bug). Preuve complète avant d'intégrer quoi que ce soit au code.

### games.conf en ligne (pas juste la copie locale de KG)

Trouvé que `games.conf` est aussi hébergé dans le même dépôt communautaire que les `.savepatch` : [bucanero/apollo-patches/PS3/games.conf](https://github.com/bucanero/apollo-patches/blob/main/PS3/games.conf) (9208 lignes, cohérent avec la copie de KG, et à jour).

### 🐛 Bug trouvé en cours de route : doublement des lignes en cache sous Windows

En câblant le fetch+cache (même pattern que `cheats.py`), `parse_games_conf` retournait des `secure_file_id` vides pour `BCES01741` malgré une extraction manuelle correcte. Cause : `Path.write_text()` en mode texte **retraduit** les `\n` déjà présents dans le texte récupéré (CRLF du fichier GitHub) vers `os.linesep` — sur Windows, un `\r\n` existant devient `\r\r\n` sur disque. À la relecture, ça insère une ligne vide fantôme après chaque vraie ligne, ce qui faisait réinitialiser `current` à `None` juste après l'en-tête de section, avant que la ligne `secure_file_id:` ne soit lue. `cheats.py` avait exactement le même pattern de cache mais n'était pas symptomatique (les lignes vides y sont de toute façon ignorées) — corrigé par précaution aux deux endroits : normaliser `\r\n`/`\r` → `\n` juste après le décodage, avant d'écrire en cache.

### Implémentation

- `ps3save/games_conf.py` (nouveau) : `fetch_games_conf()` (HTTPS + cache `~/.ps3save/games_conf_cache/`), `parse_games_conf()`, `GameEntry.secure_file_id_for(file_name)` (wildcard via `fnmatch`), `lookup(product_code)`.
- `ps3save/pfd.py` : `_shuffle16` généralisé en `_shuffle(src, out_size, overrides)` (16 reste un cas particulier) ; nouveau `_secure_file_id_to_hash_key()` (sortie 20 octets) ; `PfdConfig.secure_file_id_lookup: Callable[[str], bytes | None] | None = None` (nouveau champ, rétrocompatible — `None` par défaut = comportement identique à avant) ; `_entry_hash_key()` l'essaie en dernier recours avant de lever `PfdError`.
- `ps3save/gui.py` : `App._games_conf_kwargs()` — recherche le jeu via `extract_product_code(self.save_dir.name)`, attache `secure_file_id_lookup` (+ `disc_hash_key` si présent dans `games.conf`) ; câblé dans `current_config()` et dans le chargement de `self.pf` (`refresh_info()`). Échec de lookup (jeu absent, réseau down) → repli silencieux sur l'ancien comportement, jamais une erreur bloquante.
- `ps3save/cli.py` : même câblage, `_config_from_args()` prend maintenant aussi le dossier de save pour en déduire le code produit.

**Testé, bout en bout, sur les 4 jeux de test** : `can_rehash_file()` passe de `False` à `True` pour `DAT-31`/`DATA0.DAT`/`SAVE_SYS.DAT`/`USR-DATA` ; `check_file_hashes()` retourne désormais `{0: True}` pour ces fichiers (au lieu de `{}` SKIPPED) ; **rejoué le scénario exact du bug signalé** (cheat "Max Bolts" sur `USR-DATA`) → patch + resign + `check_file_hashes` intégralement `True`, et la liste de l'onglet Cheats n'affiche plus de ⚠ pour ce cheat. Vérifié aussi en CLI (`check` affiche `USR-DATA FILE=OK` au lieu de SKIPPED) et dans l'exe PyInstaller figé (fetch HTTPS confirmé).

## Test terrain post-fix : crash console (pas une corruption) — pas un bug PSResigner

Rejoué sur console réelle : le cheat "Max Bolts" (Ratchet & Clank, `USR-DATA`) charge maintenant correctement (plus d'erreur 80029207 — le fix `secure_file_id` fonctionne), **mais fait planter la console après chargement**.

Diagnostic : vérifié par diff complet avant/après patch → **exactement 4 octets modifiés** (ceux visés par le code, `0x24`-`0x27`), taille de fichier inchangée, rien d'autre touché. Le patch de PSResigner est chirurgicalement exact — le crash n'est pas causé par notre pipeline.

Cause la plus probable, identifiée avec l'utilisateur : la save vient de **The Ratchet & Clank Trilogy** (remaster HD), mais `games.conf` catégorise `NPEA00387` sous *"Ratchet & Clank: Up Your Arsenal"* (le jeu PS2 d'origine en rachat digital). Le format crypto PFD a été conservé à l'identique entre les deux (d'où notre hash qui matche parfaitement), mais la disposition interne des données de save a pu changer dans le remaster HD. Le cheat communautaire a probablement été écrit pour la disposition mémoire du jeu PS2 d'origine, pas celle du remaster — un risque inhérent aux bases de cheats communautaires (identique avec Apollo/Save Wizard sur ce même cheat), pas quelque chose à corriger côté PSResigner.

Digital vs physique : confirmé au passage que les codes produit diffèrent par convention (`BCES`/`BLES`/`BCUS`/`BLUS` = physique, `NPEA`/`NPUA`/`NPJA`/etc. = digital PSN) — mais le code de cette save (`NPEA00387`) est généré par la console elle-même, donc fiable comme identifiant, le souci est seulement la correspondance version-du-jeu ↔ disposition-mémoire-du-cheat.

**L'utilisateur testera avec d'autres jeux quand il aura le temps.**

## Accent couleur sur les boutons d'action — fait

Demande : "un peu plus de couleurs" dans l'appli. Proposé un mockup ciblé (pas un relookage complet) avant d'implémenter quoi que ce soit — testé en clair/sombre, validé par l'utilisateur ("ça me plaît pour le moment") avant de recompiler.

- `ps3save/theme.py` : couleur `accent` (bleu, `#0070cc` clair / `#3b82f6` sombre, cohérent avec l'icône PlayStation de l'app) + `accent_active`/`accent_fg`, nouveau style ttk `Accent.TButton`.
- `ps3save/gui.py` : style appliqué aux 3 boutons d'action principaux — `Resign (in place)`, `Resign to new folder...`, `Apply Cheat` — le reste (Browse, Check hashes, Profiles, Reload) reste neutre pour garder la hiérarchie visuelle claire.

Les deux `.exe` sont reconstruits avec ce changement.

## Prochaine étape

1. Autres tests sur console réelle avec d'autres jeux (cheats, trophées, changement d'Account ID) — en attente du temps de l'utilisateur.
2. Éventuellement le type `4` (multi-write) pour les cheats, pour pousser la couverture au-delà de 93%.
3. Suite de l'embellissement si l'utilisateur en a envie (en-têtes de section colorés, barre de titre native sombre sous Windows, etc.).

## Image de fond (discrète) — fait

L'utilisateur a ajouté `resources/BackgroundMain.jpg` (2560×1440) et `resources/BackgroundMain.png` (en réalité un JPEG 1280×720, juste mal nommé — vérifié par les octets magiques, `FFD8FF...` = JPEG, pas `89504E47` = PNG).

**Point bloquant identifié avant de coder** : `tkinter.PhotoImage` ne sait pas charger de JPEG nativement (seulement GIF/PNG/PPM). Signalé à l'utilisateur, avec le compromis associé (image derrière des panneaux opaques = visible seulement dans les marges, pas un vrai habillage plein cadre) — **choix validé avec lui avant d'implémenter** : nouvelle dépendance **Pillow**, et un rendu "discret" (liseré dans la marge) plutôt qu'un relookage complet.

- `pyproject.toml` : ajout de `Pillow`.
- `ps3save/gui.py` : import `PIL.Image`/`PIL.ImageTk` dans un `try/except ImportError` (dégrade proprement si Pillow absent, pas de crash). `App._build_background()` charge `resources/BackgroundMain.jpg`, pose un `tk.Label` en `place(relwidth=1, relheight=1)` sur **`self.master` (la fenêtre racine)**, abaissé (`.lower()`) sous le frame principal. Le frame principal (`self`) garde son fond opaque (géré par `theme.py`) mais est maintenant griddé avec une marge (`padx=14, pady=14`) au lieu de remplir 100% de la fenêtre — c'est cette marge qui laisse voir l'image en dessous. Redimensionnement de l'image sur `<Configure>` (avec un cache `_bg_size` pour éviter de retraiter l'image à chaque micro-event).

**Testé** : capture d'écran (clair/sombre non re-testés séparément, le fond ne dépend pas du thème) confirmant le liseré bleu/sombre discret autour des panneaux ; validé par l'utilisateur ("ça me convient pour l'instant") avant recompilation. Vérifié aussi dans l'exe PyInstaller figé — Pillow se bundle correctement (taille de l'exe passée de ~13 Mo à ~33 Mo, attendu), le fond s'affiche bien.

## Espacement entre sections pour laisser voir le fond — fait

Demande : alléger l'interface en espaçant les 4 sections principales (`Save folder`, `Save info`, le volet Resign/Cheats, `Log`) pour que le fond apparaisse aussi *entre* elles, pas juste sur le pourtour extérieur.

**Restructuration nécessaire** : jusqu'ici, les 4 sections étaient griddées à l'intérieur d'un seul grand `ttk.Frame` (`App` elle-même, opaque) qui remplissait toute la fenêtre avec juste une marge extérieure de 14px — un espacement entre sections resterait donc coloré par le fond opaque du frame, pas par l'image. Pour que le fond soit visible entre les sections, chacune doit être sa propre cellule de grille **directement sur la fenêtre racine (`self.master`)**, pas imbriquée dans un conteneur commun.

- `ps3save/gui.py` : `App` n'est plus griddée du tout (reste une instance `ttk.Frame` inutilisée comme conteneur, inoffensif). Les 4 méthodes `_build_folder_row`/`_build_info_panel`/`_build_notebook`/`_build_log` prennent maintenant `self.master` comme parent et se griddent chacune sur une ligne de la fenêtre racine (`row=0..3`), avec `padx=14` partout et un `pady` calculé pour un espacement uniforme de 14px (marge extérieure ET entre sections) : `(14,7)` / `(7,7)` / `(7,7)` / `(7,14)`. `master.rowconfigure(3, weight=1)` (la ligne du Log) fait le lien avec l'extensibilité verticale, à la place de l'ancien `self.rowconfigure`/`columnconfigure`.

**Testé** : scénario resign complet rejoué après la restructuration (`check_file_hashes` correct, pas de régression) ; capture d'écran confirmant le fond bien visible entre chaque section désormais, pas seulement autour. Les deux `.exe` sont reconstruits.

## Bande vide dans l'onglet Notebook + coins de fenêtre arrondis — faits

L'utilisateur a repéré (capture à l'appui) une bande vide encadrée à droite des onglets "Resign"/"Cheats", et a demandé des coins de fenêtre arrondis pour adoucir le rendu.

- **Bande vide** : `ttk.Notebook` en thème `clam` dessine une bordure autour de toute la largeur de la bande d'onglets, y compris l'espace inutilisé à droite des onglets eux-mêmes (plus étroits que le panneau de contenu en dessous) — ça donnait l'impression d'une boîte vide. Fix dans `theme.py` : `style.configure("TNotebook", ..., borderwidth=0)`. Testé par capture : la bande disparaît, ne restent que les onglets.
- **Coins arrondis** : Windows 11 arrondit nativement les fenêtres composées par DWM, mais une fenêtre Tk brute n'active pas cet opt-in. Ajout de `_round_window_corners(window)` dans `gui.py` (nouvelle fonction module-level) : appelle `DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE=33, DWMWCP_ROUND=2)` via `ctypes`, en remontant au vrai HWND top-level via `GetAncestor(hwnd, GA_ROOT)` (celui que `winfo_id()` renvoie n'est pas garanti être le top-level). No-op silencieux hors Windows (Mac/Linux arrondissent déjà nativement) ou sur Windows <11 (attribut inconnu, échec silencieux). Appliqué à la fenêtre principale et à la fenêtre Profiles (`ProfilesDialog`).

**Testé** : capture d'écran zoomée sur le coin supérieur gauche de la fenêtre réelle (pas juste le contenu, la fenêtre OS complète cette fois) → le fond du bureau apparaît bien au coin, confirmant l'arrondi. Les deux `.exe` sont reconstruits et le lancement de l'exe figé a été revérifié (pas de crash lié à `ctypes`/`dwmapi`).

## Suppression des cadres "boîte dans la boîte" (Save info + Target identity) — fait

Nouvelle demande de l'utilisateur suite au fix des coins arrondis : il désignait en réalité un autre souci — certains panneaux ont un cadre extérieur (`ttk.LabelFrame`, bordure + titre) qui enveloppe des widgets ayant déjà leur propre bordure naturelle (le tableau de fichiers, les champs Entry), créant un effet de double-boîte redondant ("fenêtre mère" + "sous-fenêtre" dans ses mots).

**Fausse piste initiale, abandonnée avant d'aller trop loin** : j'ai d'abord commencé à construire un composant `RoundedPanel` (Canvas dessiné à la main avec coins arrondis, dans un nouveau `ps3save/widgets.py`) en pensant que la demande portait sur l'arrondi des panneaux internes. Après une capture de clarification avec l'utilisateur, il s'est avéré que la vraie demande était plus simple : juste **retirer le cadre extérieur**, pas le redessiner en rond. Fichier `widgets.py` supprimé avant intégration — rien de perdu, juste du temps de recherche, mais bon réflexe d'avoir clarifié par capture avant de pousser plus loin une mauvaise direction.

**Fix réel** : `ttk.LabelFrame(parent, text="...")` → `ttk.Frame(parent)` (sans texte, sans bordure) pour :
- `_build_info_panel` ("Save info") — les champs Title/Sub-title/etc. flottent maintenant librement, seul le tableau de fichiers garde sa bordure propre.
- `_build_resign_tab` ("Target identity") — même traitement, les champs Console ID/User ID/Account ID flottent directement sous les onglets.

`_build_log` ("Log") a le même motif mais n'a pas été demandé cette fois — laissé tel quel, à voir si l'utilisateur veut le même traitement plus tard.

**Testé** : capture d'écran après chaque changement (effet confirmé, pas de double-boîte) + scénario resign complet rejoué (`check_file_hashes` correct, pas de régression). Les deux `.exe` sont reconstruits.

## Bordure résiduelle du panneau de contenu du Notebook — tentative annulée

L'utilisateur a signalé une "longue bande à côté des onglets". J'ai diagnostiqué et corrigé la bordure de `Notebook.client` (élément utilisant `bordercolor`/`lightcolor`/`darkcolor`, pas `borderwidth` — d'où l'inefficacité du fix précédent) en l'alignant sur la couleur de fond. **L'utilisateur a indiqué que ça ne correspondait pas à ce qu'il voulait** et a demandé à revenir en arrière pour réexpliquer — `theme.py` restauré à l'état d'avant cette tentative (juste `borderwidth=0`/`bordercolor=c["border"]`, sans les lignes `lightcolor`/`darkcolor`). Le diagnostic technique sur `Notebook.client` reste valable et pourra être réutilisé une fois la vraie demande clarifiée.

## Remplacement du ttk.Notebook par des onglets faits main — fait

L'utilisateur a réexpliqué avec une capture annotée : ce qu'il voulait vraiment, c'était que **seuls les boutons d'onglets dépassent**, rien d'opaque à leur droite.

**Diagnostic clé** : un `ttk.Notebook` est un seul rectangle — sa largeur est dictée par son contenu le plus large (ici, le panneau "Target identity" + boutons, plus large que les deux onglets "Resign"/"Cheats"). La bande d'espace à droite des onglets, à hauteur de la bande d'onglets, appartient donc structurellement au widget et reste forcément remplie d'une couleur opaque (le fond du panneau) — **aucun réglage de couleur/bordure ne peut la rendre transparente**, contrairement aux deux tentatives précédentes qui essayaient de la maquiller plutôt que de s'en débarrasser structurellement.

**Avant d'implémenter, vérification du risque de régression** (demandée explicitement par l'utilisateur) : `self.notebook` n'était référencé nulle part ailleurs dans le code (ni événement de changement d'onglet, ni logique métier dépendante) ; `_build_resign_tab()`/`_build_cheats_tab()` prennent déjà un `parent` générique en paramètre, agnostique de son type. Risque confirmé faible et bien délimité avant de coder.

**Implémentation** : `ttk.Notebook` remplacé par deux `ttk.Button` ordinaires ("Resign"/"Cheats", dans une petite `ttk.Frame` qui ne prend que leur largeur naturelle) + deux `ttk.Frame` séparées griddées directement sur `self.master`, dont une seule visible à la fois via `.grid()`/`.grid_remove()` (`_select_tab()`). Le bouton actif utilise le style `Accent.TButton` déjà existant comme indicateur visuel. Renumérotation des lignes de la grille (`Log` passe de la ligne 3 à 4, `master.rowconfigure` ajusté en conséquence). Styles `TNotebook`/`TNotebook.Tab` devenus morts retirés de `theme.py`.

**Testé** : bascule programmatique entre onglets (jamais les deux visibles en même temps, vérifié via `winfo_ismapped()`) + scénario resign complet rejoué (`check_file_hashes` correct). Capture d'écran confirmant l'effet exact demandé : les deux boutons ne prennent que leur largeur, le fond (dégradé bleu) est visible jusqu'au bord droit de la fenêtre. Les deux `.exe` sont reconstruits.

## Bordure blanche autour de la fenêtre — fait

L'utilisateur a redemandé les coins arrondis ("à nouveau ?") — revérifié et confirmé qu'ils fonctionnaient toujours (capture zoomée sur le coin réel), rien n'avait cassé ça. Il a ensuite précisé ce qu'il voulait vraiment en plus : une **bordure blanche** autour des fenêtres extérieures, en complément des coins déjà arrondis.

Fix dans `_round_window_corners()` (`gui.py`) : ajout de l'attribut DWM `DWMWA_BORDER_COLOR` (34, Windows 11+) juste après `DWMWA_WINDOW_CORNER_PREFERENCE`, valeur blanche (`COLORREF 0x00FFFFFF`). Le fin liseré système que Windows dessine déjà autour de chaque fenêtre (habituellement un gris neutre, peu visible) devient blanc — complète l'arrondi existant plutôt que de le remplacer. Appliqué aux deux endroits déjà gérés par cette fonction (fenêtre principale + `ProfilesDialog`), aucun changement structurel nécessaire.

**Testé** : capture d'écran de la vraie fenêtre (pas juste le contenu) confirmant le liseré blanc visible tout autour, coins toujours arrondis. Les deux `.exe` sont reconstruits.

## Liseré autour des 4 sections — tentative annulée, puis stash

Nouvelle demande suite à la bordure blanche de fenêtre : une capture annotée de 4 rectangles rouges entourant `Save folder`, `Save info`, le bloc onglets+contenu et `Log`, pour un contour fermé visible autour de chaque section (pas juste les bordures naturelles des widgets internes).

**Première implémentation, rejetée** : `App._make_bordered_frame()` — un `tk.Frame` (`highlightthickness`) enveloppant chaque section en `ttk.Frame`, couleur `c["border"]` du thème. Pour la section onglets, la barre d'onglets ET le contenu actif étaient mis dans le **même** cadre bordé. L'utilisateur : *"Ce n'est pas bon, reviens en arrière"* — tous les changements (`gui.py`, `theme.py`, `SESSION_LOG.md`) annulés à la main (ré-édition inverse exacte) et les deux `.exe` reconstruits pour refléter le retour en arrière.

**Stash puis comparaison** : l'utilisateur a demandé de stasher les changements en cours (`git stash push -u`), puis de reconstruire les `.exe` — ce qui a produit un exe reflétant l'état `main` commité (onglets Notebook à l'ancienne, avec la bande vide et un liseré blanc englobant tabs+contenu). Comparaison par capture entre cette version et la version stashée (onglets faits main, sans cadre) a permis de clarifier la vraie demande : un **hybride** des deux.

## Liseré blanc autour du seul panneau de contenu des onglets — fait

Demande clarifiée : garder les onglets faits main tels quels (rien ne les enveloppe, ils ne prennent que leur largeur propre) + un liseré **blanc** (pas la couleur de bordure du thème) autour du panneau de contenu actif seulement, pas autour de la ligne d'onglets elle-même — pour éviter de réintroduire la bande vide de l'ancien Notebook tout en retrouvant l'aspect "page" visible sur l'ancienne capture.

- `git stash pop` pour récupérer le travail de la session (coins arrondis, bordure blanche de fenêtre, onglets faits main) resté dans le stash.
- `_build_notebook()` (`gui.py`) : ajout d'un `tk.Frame` `content_border` (`highlightthickness=1`, `highlightbackground="white"`) entre la barre d'onglets et les deux frames de contenu (`resign`/`cheats`), qui deviennent désormais enfants de `content_border` plutôt que de `self.master` directement. `content_border.columnconfigure(0, weight=1)` pour que le contenu actif remplisse bien sa largeur. La barre d'onglets (`tab_bar`) reste à part, hors de ce cadre.
- `apply_theme()` : synchronise le fond de `content_border` (`self._content_border`) avec `c["bg"]` à chaque bascule clair/sombre — le liseré lui-même reste blanc fixe (pas dépendant du thème), comme la bordure DWM de la fenêtre.

**Testé** : capture d'écran de la fenêtre réelle confirmant le liseré blanc fermé autour du panneau de contenu, les deux boutons d'onglets qui dépassent librement au-dessus sans aucune bande vide. Validé par l'utilisateur ("C'est parfait comme ça"). Les deux `.exe` sont reconstruits.

## Hauteur fixe du panneau Log — fait

L'utilisateur a remarqué que le panneau `Log` devenait énorme par défaut (onglet Resign, contenu court) alors qu'il paraissait bien plus compact sur l'onglet Cheats (contenu plus grand) — et voulait ce format compact comme défaut, sans toucher au redimensionnement du bloc onglets lui-même.

**Diagnostic** : `master.rowconfigure(4, weight=1)` donnait toute la hauteur verticale restante de la fenêtre (taille fixe 780x720) à la ligne du `Log`. Comme la hauteur du bloc onglets (`content_border`) varie selon l'onglet actif (118px sur Resign, 361px sur Cheats, mesuré via un script d'inspection headless important `ps3save.gui.App` directement, sans clic réel), le `Log` se voyait attribuer tout le reste : 320px sur Resign, seulement 77px sur Cheats (compressé sous sa taille naturelle demandée).

**Fix** : suppression de `master.rowconfigure(4, weight=1)` (`gui.py`, `App.__init__`) -- le `Log` n'absorbe plus l'espace restant. `tk.Text(height=12, ...)` réduit à `height=5` pour une taille par défaut modeste. Résultat vérifié : `log_box` fait désormais une hauteur fixe de 107px, identique sur les deux onglets, immédiatement sous le bloc onglets (qui continue de varier sa propre hauteur comme avant, inchangé). L'espace libéré en bas de fenêtre (sur l'onglet Resign, plus court) laisse simplement voir le fond d'écran, cohérent avec le reste de l'interface.

**Incident lors du test** : une tentative d'automatiser un clic réel sur le bouton "Cheats" (via coordonnées écran + `mouse_event`) a cliqué par erreur sur la fenêtre du jeu **ContrabandPolice** de l'utilisateur (superposée à cet endroit de l'écran), potentiellement au niveau d'un slider de réglage ("Durée du jour", "Circulation", etc.) -- signalé explicitly à l'utilisateur pour qu'il vérifie les réglages du jeu. **Leçon retenue : ne plus jamais automatiser de clic réel à l'écran pour tester cette appli ; utiliser uniquement l'inspection headless (`app._select_tab(...)` appelé directement en import Python) ou des captures sans interaction simulée.**

**Testé** : script headless confirmant `log_box` fixe à 107px sur Resign et sur Cheats (plus de variation 320px/77px) ; capture d'écran de la fenêtre réelle sur l'onglet Resign confirmant le rendu compact attendu. Les deux `.exe` sont reconstruits.

## Icône du jeu centrée dans son cadre — fait

Demande : centrer verticalement l'image `ICON0.PNG` affichée à côté du tableau de fichiers, au lieu de la laisser ancrée en haut.

**Fix** : `_build_info_panel()` (`gui.py`) -- `self.icon_label.grid(row=0, column=3, rowspan=5, sticky="n", padx=(4, 4))` → suppression de `sticky="n"`. Sans ce `sticky`, Tk centre le widget sur les deux axes dans sa cellule (qui s'étend sur les 5 lignes du panneau, `rowspan=5`, la même hauteur que le tableau de fichiers juste à côté) au lieu de l'ancrer en haut.

**Testé** : chargement direct d'un dossier de sauvegarde (`MRTC0000210`, LOST PLANET 2) via script headless (`app.save_dir = ...; app.refresh_info()`, sans passer par le dialogue de sélection de fichier) puis capture de la fenêtre réelle, confirmant le logo du jeu centré verticalement entre les lignes `Title` et `Account ID`. Les deux `.exe` sont reconstruits.

**Deuxième incident (capture, pas de clic cette fois)** : la première tentative de capture a de nouveau montré la fenêtre du jeu ContrabandPolice au lieu de PSResigner -- cette fois pas un clic erroné, mais `CopyFromScreen` qui capture les pixels réellement affichés à l'écran, et la fenêtre du jeu se trouvait au-dessus de celle de PSResigner à cet endroit. Fix : `SetForegroundWindow`/`ShowWindow` sur le HWND de PSResigner avant la capture, pour la ramener au premier plan.

## Dossier test/ ajouté au repo

L'utilisateur a retiré la ligne `/test/` du `.gitignore` lui-même (le repo GitHub `Darumacho/PSResigner` est privé et ce sont ses propres sauvegardes) -- `git add test/` effectué pour suivre tout le contenu actuel. Pas de commit fait automatiquement, laissé à l'utilisateur.

## Quatre nouvelles features (Récents, Glisser-déposer, Historique, Détection auto) — faites

L'utilisateur a demandé "d'autres idées de features", une liste en a été proposée, et il a choisi ces quatre-là avec un ordre d'implémentation explicite. Idées non retenues pour l'instant notées dans la mémoire du projet (`project_next_steps.md`), pas ici.

**1. Liste "Récents"** : le simple `ttk.Entry` du `Save folder` devient un `ttk.Combobox` (`folder_combo`), avec `values` = les derniers dossiers ouverts. Nouvel helper central `App._load_save_folder(path) -> bool` (valide la présence de `PARAM.PFD`, charge, logue, retient le dossier) utilisé par `on_browse`, par la sélection dans la combobox (`<<ComboboxSelected>>` → `_on_recent_folder_selected`), et plus tard par le drag-and-drop et l'historique -- un seul point d'entrée au lieu de dupliquer la logique de chargement. `_remember_recent_folder()` déduplique (le plus récent en tête), plafonne à 8 entrées, persiste dans `settings.json` (`recent_folders`) comme `last_console_id`/`dark_mode`.

**2. Glisser-déposer** : tkinter seul n'a pas de drag-and-drop cross-platform -- ajout de la dépendance `tkinterdnd2` (confirmé avec l'utilisateur avant d'ajouter une dépendance tierce, vu que le docstring du module revendiquait jusqu'ici "pure stdlib"). `main()` utilise `TkinterDnD.Tk()` au lieu de `tk.Tk()` si le module est disponible (repli silencieux sinon, même pattern que l'import Pillow). La fenêtre entière (`self.master`) est enregistrée comme cible de drop (`DND_FILES`), `_on_drop()` parse `event.data` via `tk.splitlist` (gère les chemins avec espaces, entourés d'accolades Tcl) et appelle `_load_save_folder()` sur le premier élément déposé.
  - **PyInstaller** : `tkinterdnd2` embarque l'extension Tcl native `tkdnd` comme fichiers de données par plateforme/architecture (pas du code Python importable, donc invisible à l'analyse d'imports de PyInstaller). `PSResigner.spec` calcule maintenant dynamiquement le nom de sous-dossier (`win-x64`, `osx-arm64`, etc., même logique que `tkinterdnd2.TkinterDnD._require()`) et l'ajoute à `datas` -- chaque OS ne bundle que son propre sous-dossier, pas les 14 variantes du package. `pyproject.toml` : `tkinterdnd2` ajouté aux dépendances. Vérifié que l'exe figé démarre sans crash (tkdnd se charge correctement depuis `_MEIPASS`).
  - `PSResignerCLI.spec` inchangé (CLI n'importe jamais `gui.py`).

**3. Historique des resigns** : nouveau module `ps3save/history.py` (même pattern JSON que `profiles.py`), `HistoryEntry` (timestamp, action, folder, console_id, user_id, account_id), plafonné à 200 entrées. `App._do_resign()` prend maintenant un paramètre `action` et logue une entrée après chaque resign réussi -- un seul point d'instrumentation puisque les 3 flux (resign en place, resign vers nouveau dossier, apply cheat) passent tous par cette méthode. Nouveau bouton "History..." à côté de "Profiles...", ouvrant `HistoryDialog` (Treeview en lecture seule, plus récent en premier, double-clic pour recharger ce dossier via `_load_save_folder()`, bouton "Clear history").

**4. Détection auto du jeu** : nouveau champ "Hash profile" dans le panneau Save info (6ᵉ ligne, `rowspan` du tableau de fichiers et de l'icône passés de 5 à 6 en conséquence). Nouvelle méthode `App._check_game_profile()` factorise la logique déjà présente dans `_games_conf_kwargs()` (extraction du code produit + lookup dans `games.conf`) et retourne en plus un statut lisible : `"Recognized (N file key(s))"`, `"Not in database (PARAM.SFO/trophies only)"`, `"Lookup failed (...)"` (si pas de cache + pas de réseau), ou `"-"` si rien n'est chargé. `_games_conf_kwargs()` réutilise cette méthode au lieu de dupliquer le try/except.

**Testé** : chaque feature testée séparément via scripts headless (import direct de `ps3save.gui`, sans passer par un vrai clic écran) + capture de la fenêtre réelle ramenée au premier plan (`SetForegroundWindow`) pour confirmer visuellement -- combobox avec flèche déroulante, dialogue Historique avec deux entrées factices (nettoyées après coup, `history.json` supprimé puisqu'il n'existait pas avant ce test), ligne "Hash profile: Recognized (1 file key(s))" sur LOST PLANET 2. `tkinterdnd2.drop_target_register` confirmé présent sur la fenêtre racine. Resign réel non re-testé (aucune des 4 features ne touche `pfd.py`/`sfo.py`) ; l'exe figé lancé brièvement pour confirmer l'absence de crash au démarrage (chargement de `tkdnd`). `PSResigner.exe` reconstruit ; `PSResignerCLI.exe` non concerné, pas reconstruit.

## Code produit affiché dans Save info — fait

Demande : afficher aussi l'identifiant de jeu (BCES/NPEA/MRTC/etc.) dans le panneau Save info, pas seulement en interne pour le lookup `games.conf`.

**Fix** : nouvelle ligne "Product code" dans `_build_info_panel()` (entre "Account ID" et "Hash profile" -- juste avant, puisque le code produit est ce qui permet de trouver le profil de hash juste en dessous). `fields` passe de 6 à 7 entrées, `rowspan` du tableau de fichiers et de l'icône suit automatiquement (`len(fields)`, pas de valeur en dur à changer). `refresh_info()` : `extract_product_code(self.save_dir.name)` (déjà importé, déjà utilisé par `_check_game_profile()`) renseigne le champ, `"-"` si le nom du dossier ne correspond à aucun format reconnu.

**Testé** : script headless confirmant `Product code` = `MRTC00002` pour le dossier `MRTC0000210` (le code produit est les 4 lettres + 5 chiffres du préfixe, `PRODUCT_CODE_RE` dans `cheats.py` -- le reste du nom de dossier n'en fait pas partie). Capture de la fenêtre réelle confirmant la ligne bien positionnée, panneau toujours proportionné (icône centrée, tableau non décalé). `PSResigner.exe` reconstruit.

## Région du jeu affichée à côté du titre — fait

L'utilisateur a demandé en amont si on pouvait déduire la région (EU/NA/JP/Asia/Digital) à partir du code produit avant de coder quoi que ce soit -- recherche faite (voir réponse précédente, sources psdevwiki/RPCS3 forums) : Sony encode la région dans le préfixe à 4 lettres (1ʳᵉ lettre `B`=disque/`N`=PSN digital, 3ᵉ lettre = lettre de région `E`/`U`/`J`/`A`/`H`/`K`), convention communautaire reverse-engineered mais très stable. Confirmé faisable, puis "Dans ce cas, c'est parti" de l'utilisateur pour l'implémenter.

**Implémentation** : nouveau module `ps3save/region.py`, fonction `region_label(product_code) -> str | None` avec une table statique (pas de source externe à requêter, contrairement à `games.conf`) ; renvoie `None` si le préfixe ne correspond à aucun format connu (ex: le code de test `MRTC00002`, qui ne suit pas le schéma Sony du tout) plutôt que de deviner à tort. `refresh_info()` (`gui.py`) ajoute ce label entre parenthèses à côté du `Title` affiché, ex. `"Ratchet & Clank 3 (EU, Digital)"`.

**Bug trouvé puis corrigé pendant le test** : une des sauvegardes de test réelles (`BCES01741-PROFDATA-0`, un save `PROFDATA` système) a un titre SFO qui contient déjà littéralement `"(EU)"` dans la chaîne stockée -- l'ajout naïf donnait `"... (EU) (EU)"`. Fix : `if region and region not in title` avant de l'ajouter, pour ne pas dupliquer un suffixe de région déjà présent dans le titre d'origine.

**Incident de capture d'écran** : la vérification visuelle suivante a affiché du contenu explicite sans rapport (une fenêtre vidéo superposée à l'écran à ce moment, capturée par `CopyFromScreen` comme les incidents précédents avec ContrabandPolice/l'Explorateur de fichiers) -- fichier supprimé immédiatement, signalé à l'utilisateur. **Décision prise : abandonner complètement la technique de capture d'écran réelle (`CopyFromScreen`) pour les vérifications visuelles de ce projet** -- trop d'incidents sur ce bureau multi-fenêtres (ContrabandPolice, rename Explorateur, maintenant ceci). Les tests headless (import direct de `ps3save.gui`, lecture des `StringVar`/widgets en mémoire) restent le moyen de vérification principal.

**Testé** : script headless confirmant `region_label()` sur tous les préfixes connus (`BCES`→EU, `BLUS`→NA, `BLJM`→JP, `BCAS`/`BLAS`→Asia, `NP**`→variante Digital, codes inconnus→`None`) et sur les 4 sauvegardes de test réelles (dédoublonnage du cas `(EU)` confirmé). `PSResigner.exe` reconstruit.

## Détection d'une PS3 montée en USB — fait

Nouvelle session (2026-10-11). L'utilisateur a branché une vraie clé USB (lecteur `F:/`) contenant un dossier `PS3/SAVEDATA/` (layout standard utilisé par la sauvegarde "Copier vers périphérique USB" de la PS3 elle-même, et par multiMAN) avec 7 vraies sauvegardes dedans, pour tester cette feature du backlog.

**Implémentation** : nouveau module `ps3save/usb_scan.py` :
- `_candidate_roots()` énumère les lecteurs/volumes montés selon la plateforme : lettres `A:`-`Z:` sur Windows (`Path(f"{letter}:\\").exists()`, aucune dépendance type `pywin32` nécessaire), `/Volumes/*` sur Mac, `/media/*/*` et `/mnt/*` sur Linux. Chaque lecteur qui lève une `OSError` à l'interrogation (ex: lecteur optique vide) est simplement ignoré plutôt que de faire échouer tout le scan.
- `find_ps3_save_folders()` : pour chaque racine, vérifie `<root>/PS3/SAVEDATA/`, puis ne retient que les sous-dossiers contenant effectivement un `PARAM.PFD`.
- `gui.py` : nouveau bouton "Scan USB..." dans la ligne `Save folder` (entre "Browse..." et "Dark mode"). Ouvre `UsbScanDialog` (même famille que `ProfilesDialog`/`HistoryDialog`) : Treeview (Lecteur / Dossier / Titre -- le titre est lu depuis `PARAM.SFO` pour chaque résultat, au mieux, "-" si illisible), bouton "Load" (ou double-clic) qui appelle `_load_save_folder()` et ferme la fenêtre, bouton "Rescan".

**Testé** (headless, conformément à [[feedback-no-screenshots]]) : scan réel sur la clé USB connectée -- 7 dossiers trouvés en 0.05s (scan de 26 lettres de lecteur inclus), titres lus correctement pour chacun (`"God of War: Ascension (EU)"`, `"Ratchet & Clank 3"`, etc.). Sélection + `on_load()` confirmée : charge bien le bon dossier dans la fenêtre principale et ferme le dialogue. Scénario `check hashes` rejoué après chargement depuis la clé (lecture seule, pas de régression). Exe figé lancé 5s sans crash. `PSResigner.exe` reconstruit.

## Prochaine étape

1. Backlog de features discuté mais pas encore planifié (voir `project_next_steps.md` dans la mémoire Claude -- ne vit pas dans ce repo) : type `4` (multi-write) pour les cheats, re-vérification automatique des hashs après resign, bouton "Annuler le dernier resign", export/import des profils, validation d'intégrité pré-resign, mode "dry run".
2. Autres tests sur console réelle avec d'autres jeux (cheats, trophées, changement d'Account ID) — en attente du temps de l'utilisateur.
3. Suite de l'embellissement si l'utilisateur en a envie (ex: même traitement "Log" sans cadre, ou liseré blanc façon onglets).

## Références (à re-télécharger si besoin, non copiées dans ce repo)

- https://github.com/bucanero/pfd_sfo_tools (pfdtool original de flatz, GPL) — fichiers clés : `pfdtool/src/pfd.c`, `pfd_internal.h`, `backend.c`, `util.c`
- https://github.com/bucanero/apollo-ps3 (Apollo Save Tool, GPL) — fichier clé : `source/pfd_util.c` (contient les clés obfusquées par XOR)

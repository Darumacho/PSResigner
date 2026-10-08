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

## Références (à re-télécharger si besoin, non copiées dans ce repo)

- https://github.com/bucanero/pfd_sfo_tools (pfdtool original de flatz, GPL) — fichiers clés : `pfdtool/src/pfd.c`, `pfd_internal.h`, `backend.c`, `util.c`
- https://github.com/bucanero/apollo-ps3 (Apollo Save Tool, GPL) — fichier clé : `source/pfd_util.c` (contient les clés obfusquées par XOR)

# PSResigner

A cross-platform PS3 save resigner: rebinds a `PARAM.PFD` save (or trophy
set) to a different console ID and/or PSN account, so a save copied from
one PS3 (or one account) can be loaded on another. Comes as both a GUI
and a command-line tool, and runs the same way on Windows and macOS.

This started as a from-scratch, cross-platform replacement for KG's
Windows-only "[PS3] Save Resigner", built on a pure-Python port of
flatz's `pfdtool`.

## What it does

A PS3 save folder's `PARAM.PFD` file binds every other file in that
folder to a specific console (via its `console_id`/"IDPS") and local
user slot (`user_id`), using per-file HMAC-SHA1 hashes wrapped in an
AES-encrypted signature blob. Resigning means decrypting that blob,
recomputing every hash against a *new* console_id/user_id, and writing
it back — PSResigner does this directly in Python, without needing a
PS3, an external hash-fixing tool, or admin rights.

It can also optionally patch the PSN account a save is tied to (its
`PARAM.SFO`'s `ACCOUNT_ID` field and the matching entry inside the
SFO's `PARAMS` blob), the same two fields KG's tool patches.

## Features

- **Resign in place or to a new folder**, with an optional automatic
  backup of the original files beforehand.
- **Account/user rebinding** alongside the console resign (Account ID,
  local User ID).
- **Named profiles** — save a user/account/console combination under a
  name and reapply it later, or load one straight from a save's
  `PARAM.SFO`.
- **Hash verification** (`Check hashes`) against a target console/user
  before committing to a resign.
- **Community cheat codes** (bucanero/apollo-patches `.savepatch`
  database): browse and apply published Save Wizard-style codes for the
  loaded save: the target file is decrypted, patched, re-encrypted and
  the save re-signed (codes address the file's plain text). Code
  types 0/1/2 (fixed-width write), 3 (increment/decrement), 8 (search)
  and 9 (pointer set/move) are supported; anything else is rejected
  rather than silently mis-applied.
- **Per-game key lookup** (`games.conf`, from the same community
  database) for games whose non-`PARAM.SFO` files need a per-title
  "secure file ID" to be decrypted and hashed correctly — the GUI shows whether a loaded
  save's game was recognized.
- **Product code and region detection** — the save's Sony product code
  (e.g. `BCES01741`) is shown, together with a best-effort region/
  distribution guess (EU/NA/JP/Asia, disc or digital) derived from
  Sony's own content-ID prefix convention.
- **Recent folders, drag-and-drop**, and a persistent **resign
  history** log, so repeated testing on the same few saves doesn't
  mean re-browsing for the folder every time.
- Light/dark theme, with Windows 11 rounded window corners.

## Installing

Requires Python 3.10+.

```
pip install -e .
```

This pulls in the runtime dependencies: `pycryptodome` (AES for the PFD
signature), `Pillow` (loading the JPEG background image), `tkinterdnd2`
(drag-and-drop support for the GUI), and `certifi` (a trusted CA bundle
for HTTPS requests to the cheats/games.conf database — needed because
python.org's own macOS builds don't ship a working one out of the box;
harmless elsewhere).

## Running

```
ps3save-gui          # GUI
ps3save info <path>      # CLI: show a save's PARAM.PFD structure
ps3save check <path> --console-id <32 hex chars> --user-id <id>
ps3save resign <path> --console-id <32 hex chars> --user-id <id> [--account-id <16 hex chars>] [--output <dir>]
```

`path` is a save folder containing a `PARAM.PFD` file. `--user-id`
accepts either a plain integer (the local user slot, e.g. `1`) or an
8-character string. Run `ps3save <command> --help` for the full option
list.

## Building a standalone executable

Both the GUI and the CLI can be frozen with PyInstaller:

```
pyinstaller PSResigner.spec       # PSResigner.exe / PSResigner.app — GUI
pyinstaller PSResignerCLI.spec    # PSResignerCLI.exe / PSResignerCLI — CLI
```

`PSResigner.spec` bundles `tkinterdnd2`'s native `tkdnd` extension for
whichever platform it's built on, so it has to be run separately on
each target OS — there's no cross-compiling a Windows build from macOS
or vice versa. On macOS it produces a proper `PSResigner.app` bundle
(onedir + `BUNDLE`, its own `.icns` icon, no UPX — UPX corrupts signed
Mach-O binaries) instead of a single onefile executable.

### Auto-update releases

The frozen GUI checks the repo's latest GitHub release at startup and,
if its tag is newer than `ps3save.__version__`, offers to download and
install it. The release must carry the asset for each platform:

- Windows: `PSResigner.exe`
- macOS: `PSResigner-macOS-<arch>.zip` (`arm64` / `x86_64`), built with
  `./build_mac.sh` — it runs PyInstaller and zips the `.app` with
  `ditto` (a plain `zip` drops the symlinks inside the bundle).

On macOS the app must live in a writable folder (e.g. `/Applications`),
not be run straight from Downloads or a disk image.

## How it works

The cryptography and file layout are a direct port of the PS3 homebrew
scene's own tooling, not a reverse-engineering effort from scratch:

- `ps3save/pfd.py` ports flatz's `pfdtool` (GPL) — the `PARAM.PFD`
  reader, hash verifier and resigner — plus the protected files'
  decryption/encryption from Apollo's `pfd.c` (AES with a per-block
  big-endian counter, keyed by the file's secure file ID).
- `ps3save/sfo.py` implements the long-documented `PARAM.SFO` container
  format, plus the account/user fields inside its `PARAMS` blob
  (reverse-engineered from `bucanero/apollo-ps3`'s `sfo.c` and
  cross-checked against a profile exported by KG's tool).
- `ps3save/cheats.py` and `ps3save/games_conf.py` pull cheat codes and
  per-game hash keys from the community-maintained
  `bucanero/apollo-patches` database on first use, caching them locally
  under `~/.ps3save/`.
- The cryptographic constants in `ps3save/keys.py` are the same
  publicly known PFD keys every PS3 save tool (Apollo Save Tool,
  multiMAN, KG's Save Resigner, ...) has shipped with since flatz first
  published them in 2012 — not a secret extracted by this project.

Settings, profiles, and resign history are stored as JSON under
`~/.ps3save/` and persist across runs and updates.

## Known limitations

- Save Wizard cheat code types 4 (multi-write), 5, 6, 7 and A-F aren't
  implemented yet.
- Region detection is a heuristic based on Sony's own product-code
  convention, not an official list — codes that don't follow the usual
  `[BN][A-Z][EUJAHK][A-Z]\d{5}` shape (test/dev saves, some obscure
  prefixes) are left unlabeled rather than guessed.
- Not yet verified end-to-end on a real PS3 console — tested against
  real save files and cross-checked against KG's tool's own output, but
  without hardware to confirm the resigned saves actually load.

## Credits

- flatz — original `pfdtool`.
- bucanero — `apollo-ps3`, `pfd_sfo_tools`, and the `apollo-patches`
  cheat/games-conf database that this project fetches from at runtime.
- KG — "[PS3] Save Resigner", whose feature set this project set out
  to match on platforms it doesn't support.

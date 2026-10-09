"""Per-game key database (games.conf), needed to hash/resign any save
file besides PARAM.SFO and the fixed TROP*.DAT names.

Format (sections keyed by one or more slash-separated product codes,
each followed by an optional disc_hash_key and one or more
secure_file_id:<pattern>=<hex> lines -- both are 16-byte values as hex):

    ; "Some Game"
    [BCES00001/BLES00001/NPEA00001]
    ;disc_hash_key=                        <- commented out = use the fallback
    secure_file_id:*=E1197F68ACA23B459DEC8062FF46195A

Hosted alongside the .savepatch database in bucanero/apollo-patches.
Verified against 4 real saves this session: the secure_file_id-derived
key correctly reproduces the exact FILE hash already stored in each
save's PARAM.PFD for a non-PARAM.SFO file (DAT-31, DATA0.DAT,
SAVE_SYS.DAT, USR-DATA) -- see SESSION_LOG.md.
"""

from __future__ import annotations

import fnmatch
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

GAMES_CONF_URL = "https://raw.githubusercontent.com/bucanero/apollo-patches/main/PS3/games.conf"
CACHE_PATH = Path.home() / ".ps3save" / "games_conf_cache" / "games.conf"

SECTION_RE = re.compile(r"^\[(.+)\]$")


class GamesConfError(Exception):
    pass


@dataclass
class GameEntry:
    product_codes: list[str]
    disc_hash_key: bytes | None = None
    secure_file_ids: list[tuple[str, bytes]] = field(default_factory=list)  # (pattern, 16-byte id)

    def secure_file_id_for(self, file_name: str) -> bytes | None:
        for pattern, sfid in self.secure_file_ids:
            if fnmatch.fnmatchcase(file_name.upper(), pattern.upper()):
                return sfid
        return None


def fetch_games_conf(use_cache: bool = True) -> str:
    if use_cache and CACHE_PATH.exists():
        return CACHE_PATH.read_text(encoding="utf-8", errors="replace")

    try:
        with urllib.request.urlopen(GAMES_CONF_URL, timeout=15) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise GamesConfError(f"Couldn't fetch games.conf: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise GamesConfError(f"Couldn't reach the games database: {e.reason}")

    # Normalize to \n before caching: the file is CRLF, and Path.write_text()
    # (text mode) re-translates each \n to os.linesep on Windows, turning
    # "\r\n" into "\r\r\n" -- which reads back as a spurious extra blank
    # line after every real one, corrupting section tracking.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(text, encoding="utf-8")
    return text


def parse_games_conf(text: str) -> dict[str, GameEntry]:
    """Returns {product_code: GameEntry}; a code shared by several games
    (shouldn't happen, but the file is community-maintained) keeps
    whichever section is parsed last."""
    by_code: dict[str, GameEntry] = {}
    current: GameEntry | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            current = None
            continue
        if line.startswith(";"):
            continue

        m = SECTION_RE.match(line)
        if m:
            codes = [c.strip().upper() for c in m.group(1).split("/") if c.strip()]
            current = GameEntry(product_codes=codes)
            for code in codes:
                by_code[code] = current
            continue

        if current is None:
            continue

        if line.startswith("disc_hash_key="):
            value = line.split("=", 1)[1].strip()
            if len(value) == 32:
                current.disc_hash_key = bytes.fromhex(value)
        elif line.startswith("secure_file_id:"):
            key_part, _, value = line.partition("=")
            pattern = key_part[len("secure_file_id:"):].strip()
            value = value.strip()
            if len(value) == 32:
                current.secure_file_ids.append((pattern, bytes.fromhex(value)))

    return by_code


_parsed_cache: dict[str, GameEntry] | None = None


def lookup(product_code: str) -> GameEntry | None:
    """Fetch+parse games.conf (once per process; relies on fetch_games_conf's
    on-disk cache across runs) and look up one product code."""
    global _parsed_cache
    if _parsed_cache is None:
        _parsed_cache = parse_games_conf(fetch_games_conf())
    return _parsed_cache.get(product_code.upper())

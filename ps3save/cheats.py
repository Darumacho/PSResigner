"""Community cheat-code database integration (bucanero/apollo-patches).

Fetches and parses .savepatch files -- a per-game list of named cheat
codes targeting specific files inside a save, written in Apollo Save
Tool's "Save Wizard" hex-code dialect. Real .savepatch syntax, reverse
engineered from bucanero/apollo-lib's patches.c and a real file
(PS3/BCES01741.savepatch -- God of War: Ascension, one of our own test
saves):

    ; comment line
    :<save-folder-pattern>\\<file-pattern>     <- section header, sets the
                                                  target for every cheat
                                                  below it until the next
                                                  one (both sides may use
                                                  '*' wildcards)
    [Cheat name]
    80010004 00000324      <- one or more "XXXXXXXX YYYYYYYY" code lines
    28000030 49742400

A single file can target several different save folders for the same
game (e.g. both its "-PROFDATA-*" and "-SYSDATA" variants), so cheats
are filtered against the actually-loaded save's folder name before
being shown.

Applying a code (apply_sw_codes()) supports types 0/1/2 (8/16/32-bit
write), 3 (increment/decrement), 8 (search -> set pointer) and 9
(pointer set/move; ops 0/1/2/3/5). Other types (4/5/6/7/A-F, and the
BSD-script/MicroPython dialects) aren't implemented -- apply_sw_codes()
raises UnsupportedCodeError rather than guessing at an opcode it
doesn't fully understand.

All multi-byte values are treated as big-endian: Apollo's own default
is "whatever CPU it's running on" (apollo_get_host_endianness(), from
bucanero/apollo-lib's types.h), and since Apollo actually runs on the
PS3 itself (PowerPC, big-endian), that default is big-endian in
practice for every real PS3 .savepatch. A .savepatch can override this
per-code with "[LE:]"/"[BE:]" tags; those aren't parsed yet, so a cheat
relying on one would be applied with the wrong byte order -- not
silently either, since apply_sw_codes doesn't special-case anything
based on tags it doesn't recognize (there simply aren't any in the 105
real files this was tested against).
"""

from __future__ import annotations

import fnmatch
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PATCHES_BASE_URL = "https://raw.githubusercontent.com/bucanero/apollo-patches/main"
CACHE_DIR = Path.home() / ".ps3save" / "cheats_cache"

PRODUCT_CODE_RE = re.compile(r"^[A-Z]{4}\d{5}")
SW_CODE_LINE_RE = re.compile(r"^[0-9A-Fa-f]{8} [0-9A-Fa-f]{8}$")


class CheatsError(Exception):
    pass


@dataclass
class CheatEntry:
    name: str
    codes: str          # raw "XXXXXXXX YYYYYYYY" lines, one per line, undecoded
    folder_pattern: str  # e.g. "BCES01741-PROFDATA-*" (fnmatch-style)
    file_pattern: str    # e.g. "DAT-*"

    def matches_folder(self, folder_name: str) -> bool:
        return fnmatch.fnmatchcase(folder_name.upper(), self.folder_pattern.upper())

    def matching_files(self, save_dir: Path) -> list[str]:
        return sorted(
            p.name for p in save_dir.iterdir()
            if p.is_file() and fnmatch.fnmatchcase(p.name.upper(), self.file_pattern.upper())
        )


def extract_product_code(name: str) -> str | None:
    """Pull a Sony content-ID-style product code (e.g. BCES01741) off the
    front of a save's directory name."""
    match = PRODUCT_CODE_RE.match(name.upper())
    return match.group(0) if match else None


def fetch_savepatch(product_code: str, platform: str = "PS3", use_cache: bool = True) -> str:
    """Download (or reuse a cached copy of) <platform>/<product_code>.savepatch
    from the community apollo-patches database. Raises CheatsError if the
    game isn't in the database or the request fails."""
    cache_path = CACHE_DIR / platform / f"{product_code}.savepatch"
    if use_cache and cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace")

    url = f"{PATCHES_BASE_URL}/{platform}/{product_code}.savepatch"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise CheatsError(f"No cheat codes found for {product_code} in the community database")
        raise CheatsError(f"Couldn't fetch cheat codes for {product_code}: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise CheatsError(f"Couldn't reach the cheat database: {e.reason}")

    # Normalize to \n before caching: the file is CRLF, and Path.write_text()
    # (text mode) re-translates each \n to os.linesep on Windows, turning
    # "\r\n" into "\r\r\n" on disk (harmless here since blank lines are
    # skipped either way, but games_conf.py hit a real bug from the same
    # pattern -- keeping both consistent).
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return text


def parse_savepatch(text: str) -> list[CheatEntry]:
    entries: list[CheatEntry] = []
    folder_pattern = file_pattern = "*"  # some files never have a ':' directive at all
    name: str | None = None
    code_lines: list[str] = []

    def flush():
        # Headers like "[Group:...]"/"[INFO:...]" are organizational, not
        # real cheats -- they carry no code lines, so there's nothing to
        # ever apply. Drop them rather than listing them as fake cheats.
        if name is not None and code_lines:
            entries.append(CheatEntry(name, "\n".join(code_lines), folder_pattern, file_pattern))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue

        if line.startswith(":"):
            flush()
            name = None
            code_lines = []
            spec = line[1:]
            if "\\" in spec:
                folder_pattern, _, file_pattern = spec.partition("\\")
            else:
                # No folder part given (e.g. ":DATA0.DAT") -- matches any save folder.
                folder_pattern, file_pattern = "*", spec
            continue

        if line.startswith("[") and line.endswith("]"):
            flush()
            name = line[1:-1]
            code_lines = []
            continue

        if name is None and not SW_CODE_LINE_RE.match(line):
            # Not inside a cheat yet, and this doesn't look like a code line
            # either: some real .savepatch files (e.g. BLES00951) name a
            # cheat without the usual "[...]" brackets.
            flush()
            name = line
            code_lines = []
            continue

        if name is not None:
            code_lines.append(line)

    flush()
    return entries


class UnsupportedCodeError(CheatsError):
    pass


def _check_bounds(size: int, off: int, length: int) -> None:
    if off < 0 or off + length > size:
        raise UnsupportedCodeError(f"Code would write out of bounds (offset 0x{off:X}, {length} bytes)")


def apply_sw_codes(data: bytes, codes: str) -> bytes:
    """Apply a block of Save Wizard-style "XXXXXXXX YYYYYYYY" code lines
    (one cheat's `CheatEntry.codes`) to a file's bytes and return the
    patched copy. See the module docstring for which code types are
    supported and the big-endian assumption. Raises UnsupportedCodeError
    on a malformed or unrecognized code rather than silently skipping or
    misapplying it.
    """
    buf = bytearray(data)
    pointer = 0
    lines = [line.strip() for line in codes.splitlines() if line.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]
        if not SW_CODE_LINE_RE.match(line):
            raise UnsupportedCodeError(f"Malformed code line: {line!r}")

        code_type = line[0].upper()
        addr_type = line[1].upper()
        addr = int(line[2:8], 16)
        value_hex = line[9:17]

        if code_type in "012":
            # 0/1/2 = 8/16/32-bit write: "0TXXXXXX 000000YY" etc.
            size = 1 << int(code_type)
            off = addr + (pointer if addr_type == "8" else 0)
            _check_bounds(len(buf), off, size)
            buf[off:off + size] = bytes.fromhex(value_hex)[4 - size:]
            i += 1
            continue

        if code_type == "3":
            # Increment/decrement: "3BYYYYYY XXXXXXXX" -- B encodes both the
            # byte width (low 2 bits) and add-vs-subtract/pointer-relative
            # (bits 2/3): 0-3 add 1/2/4/8 bytes, 4-7 subtract, 8-B
            # pointer-relative add, C-F pointer-relative subtract.
            n = int(addr_type, 16)
            size = 1 << (n & 3)
            is_sub = bool(n & 4)
            off = addr + (pointer if n & 8 else 0)
            _check_bounds(len(buf), off, size)
            current = int.from_bytes(buf[off:off + size], "big")
            delta = int(value_hex, 16) & ((1 << (size * 8)) - 1)
            new_value = (current - delta) if is_sub else (current + delta)
            buf[off:off + size] = (new_value & ((1 << (size * 8)) - 1)).to_bytes(size, "big")
            i += 1
            continue

        if code_type == "8":
            # Search: "8TZZXXXX YYYYYYYY" -- find the ZZ'th occurrence of an
            # XXXX-byte pattern (first 4 bytes = YYYYYYYY, more bytes from
            # as many extra lines as needed) and set `pointer` to its
            # offset. T=8 searches from the current pointer, else from 0.
            count = int(line[2:4], 16) or 1
            pattern_len = int(line[4:8], 16)
            if pattern_len == 0:
                raise UnsupportedCodeError("Search code with an empty pattern")

            pattern = bytearray(bytes.fromhex(value_hex))
            i += 1
            while len(pattern) < pattern_len:
                if i >= len(lines) or not SW_CODE_LINE_RE.match(lines[i]):
                    raise UnsupportedCodeError("Search pattern continuation line missing/malformed")
                pattern += bytes.fromhex(lines[i][0:8])
                if len(pattern) < pattern_len:
                    pattern += bytes.fromhex(lines[i][9:17])
                i += 1
            pattern = bytes(pattern[:pattern_len])

            start = pointer if addr_type == "8" else 0
            found = buf.find(pattern, start)
            occurrence = 1
            while found != -1 and occurrence < count:
                found = buf.find(pattern, found + 1)
                occurrence += 1
            if found == -1:
                raise UnsupportedCodeError(
                    f"Search pattern not found (occurrence {count} of {pattern.hex()})"
                )
            pointer = found
            continue

        if code_type == "9":
            # Pointer set/move: "9Y000000 XXXXXXXX" (the middle 6 hex digits
            # are unused/zero in every real example seen; X is the operand).
            operand = int(value_hex, 16)
            op = line[1].upper()
            if op == "0":
                _check_bounds(len(buf), operand, 4)
                pointer = int.from_bytes(buf[operand:operand + 4], "big")
            elif op == "1":
                _check_bounds(len(buf), operand, 4)
                pointer = int.from_bytes(buf[operand:operand + 4], "little")
            elif op == "2":
                pointer += operand
            elif op == "3":
                pointer -= operand
            elif op == "5":
                pointer = operand
            else:
                raise UnsupportedCodeError(f"Unsupported pointer operator '9{op}'")
            i += 1
            continue

        raise UnsupportedCodeError(f"Unsupported code type '{code_type}'")

    return bytes(buf)


def list_cheats_for_save(save_dir: Path) -> list[CheatEntry]:
    """Fetch this game's .savepatch (by product code, guessed from the save
    folder's name) and return only the entries whose folder pattern matches
    this specific save folder."""
    product_code = extract_product_code(save_dir.name)
    if product_code is None:
        raise CheatsError(f"Couldn't guess a product code from folder name '{save_dir.name}'")

    text = fetch_savepatch(product_code)
    entries = parse_savepatch(text)
    return [e for e in entries if e.matches_folder(save_dir.name)]

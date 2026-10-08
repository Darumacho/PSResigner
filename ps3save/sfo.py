"""Reader/patcher for PARAM.SFO (PS1/PS2/PSP/PS3 metadata container).

Layout (little-endian), unchanged since the PS1 era and documented across
the homebrew scene (psdevwiki):

    header (20 bytes): magic "\\0PSF", version, key_table_start,
                        data_table_start, tables_entries
    index_table: tables_entries x 16-byte entries
        key_offset(u16) data_fmt(u16) data_len(u32) data_max_len(u32)
        data_offset(u32)
    key_table: NUL-terminated ASCII key names, back to back
    data_table: one data_max_len-sized slot per entry (data_len actually used)

Rebinding a save to a different PSN account/local user means patching two
more things beyond PARAM.PFD's hashes: the SFO's own ACCOUNT_ID field, and
a copy of the same id (plus the local user slot) embedded inside the
"PARAMS" binary blob. That inner layout isn't part of any public PARAM.SFO
spec -- it's reverse-engineered from bucanero/apollo-ps3's sfo_param_params_t
(source/sfo.c) and verified byte-for-byte against real saves and against a
profile exported by KG's "[PS3] Save Resigner":

    struct sfo_param_params_t {
        u8  unk1[12];
        u32 unk2, unk3, unk4;
        u32 user_id_1;
        u8  psid[16];
        u32 user_id_2;
        u8  account_id[16];
        ...
    };

All patched fields keep their exact original size, so (like Apollo does)
we overwrite them in place -- the file's overall layout never changes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

SFO_MAGIC = b"\x00PSF"
HEADER_SIZE = 20
INDEX_ENTRY_SIZE = 16

FMT_BINARY = 0x0004  # raw bytes, no NUL terminator guaranteed
FMT_UTF8 = 0x0204    # NUL-terminated UTF-8 string
FMT_INT32 = 0x0404   # uint32, little-endian

PARAMS_USER_ID_1_OFFSET = 24
PARAMS_PSID_OFFSET = 28
PARAMS_PSID_SIZE = 16
PARAMS_USER_ID_2_OFFSET = 44
PARAMS_ACCOUNT_ID_OFFSET = 48
PARAMS_MIN_SIZE = 64

ACCOUNT_ID_SIZE = 16  # 16 ASCII hex-digit characters (not 16 raw bytes)


class SfoError(Exception):
    pass


@dataclass
class SfoEntry:
    data_fmt: int
    data_len: int
    data_max_len: int
    value_offset: int  # absolute offset into SfoFile.raw


class SfoFile:
    def __init__(self, raw: bytearray, entries: dict[str, SfoEntry]):
        self.raw = raw
        self.entries = entries

    def __getitem__(self, key: str) -> int | str | bytes:
        return self.values[key]

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    @property
    def values(self) -> dict[str, int | str | bytes]:
        return {key: self._decode(entry) for key, entry in self.entries.items()}

    def _decode(self, entry: SfoEntry) -> int | str | bytes:
        raw = bytes(self.raw[entry.value_offset:entry.value_offset + entry.data_len])
        if entry.data_fmt == FMT_INT32:
            return struct.unpack("<I", raw.ljust(4, b"\x00")[:4])[0]
        if entry.data_fmt == FMT_UTF8:
            return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return raw

    @property
    def title(self) -> str | None:
        return self.get("TITLE")

    @property
    def sub_title(self) -> str | None:
        return self.get("SUB_TITLE")

    # ---- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "SfoFile":
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> "SfoFile":
        if len(data) < HEADER_SIZE or data[0:4] != SFO_MAGIC:
            raise SfoError("Not a PARAM.SFO file (bad magic)")
        _, _, key_table_start, data_table_start, tables_entries = struct.unpack_from(
            "<4sIIII", data, 0
        )

        entries: dict[str, SfoEntry] = {}
        for i in range(tables_entries):
            eoff = HEADER_SIZE + i * INDEX_ENTRY_SIZE
            key_offset, data_fmt, data_len, data_max_len, data_offset = struct.unpack_from(
                "<HHIII", data, eoff
            )

            key_start = key_table_start + key_offset
            key_end = data.find(b"\x00", key_start)
            if key_end == -1:
                raise SfoError(f"Unterminated key name at offset {key_start}")
            key = data[key_start:key_end].decode("ascii")

            value_offset = data_table_start + data_offset
            entries[key] = SfoEntry(data_fmt, data_len, data_max_len, value_offset)

        return cls(bytearray(data), entries)

    # ---- patching (account / user rebind) ---------------------------------

    def set_account_id(self, account_id_hex: str) -> None:
        """Rebind to a PSN account: same two spots Apollo's
        sfo_patch_account() touches -- the top-level ACCOUNT_ID field, and
        its copy embedded in PARAMS."""
        account_id_hex = account_id_hex.strip().lower()
        if len(account_id_hex) != ACCOUNT_ID_SIZE or not all(
            c in "0123456789abcdef" for c in account_id_hex
        ):
            raise SfoError(f"account_id must be {ACCOUNT_ID_SIZE} hex characters")
        value = account_id_hex.encode("ascii")

        entry = self.entries.get("ACCOUNT_ID")
        if entry is not None and entry.data_len == ACCOUNT_ID_SIZE:
            self.raw[entry.value_offset:entry.value_offset + ACCOUNT_ID_SIZE] = value

        params = self.entries.get("PARAMS")
        if params is not None and params.data_len >= PARAMS_MIN_SIZE:
            off = params.value_offset + PARAMS_ACCOUNT_ID_OFFSET
            self.raw[off:off + ACCOUNT_ID_SIZE] = value

    def set_user_id(self, user_id: int) -> None:
        """Rebind to a local PS3 user slot, mirroring Apollo's
        sfo_patch_user_id() (writes the same value to both user_id copies
        found in PARAMS)."""
        if not 0 <= user_id <= 0xFFFFFFFF:
            raise SfoError("user_id must fit in 32 bits")
        packed = struct.pack("<I", user_id)
        params = self.entries.get("PARAMS")
        if params is not None and params.data_len >= PARAMS_MIN_SIZE:
            off1 = params.value_offset + PARAMS_USER_ID_1_OFFSET
            off2 = params.value_offset + PARAMS_USER_ID_2_OFFSET
            self.raw[off1:off1 + 4] = packed
            self.raw[off2:off2 + 4] = packed

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(bytes(self.raw))

    # ---- reading the owner back out --------------------------------------

    def get_owner(self) -> tuple[int | None, str | None]:
        """Read back the (user_id, account_id) pair that set_user_id()/
        set_account_id() would overwrite -- used to pre-fill a Profile from
        a loaded save."""
        user_id = None
        params = self.entries.get("PARAMS")
        if params is not None and params.data_len >= PARAMS_MIN_SIZE:
            off = params.value_offset + PARAMS_USER_ID_1_OFFSET
            user_id = struct.unpack("<I", bytes(self.raw[off:off + 4]))[0]

        account_id = None
        entry = self.entries.get("ACCOUNT_ID")
        if entry is not None and entry.data_len == ACCOUNT_ID_SIZE:
            raw = bytes(self.raw[entry.value_offset:entry.value_offset + ACCOUNT_ID_SIZE])
            if raw.strip(b"\x00"):
                account_id = raw.decode("ascii", errors="replace")

        return user_id, account_id

"""Read-only parser for PARAM.SFO (PS1/PS2/PSP/PS3 metadata container).

Layout (little-endian), unchanged since the PS1 era and documented across
the homebrew scene (psdevwiki):

    header (20 bytes): magic "\\0PSF", version, key_table_start,
                        data_table_start, tables_entries
    index_table: tables_entries x 16-byte entries
        key_offset(u16) data_fmt(u16) data_len(u32) data_max_len(u32)
        data_offset(u32)
    key_table: NUL-terminated ASCII key names, back to back
    data_table: one data_max_len-sized slot per entry (data_len actually used)
"""

from __future__ import annotations

import struct
from pathlib import Path

SFO_MAGIC = b"\x00PSF"
HEADER_SIZE = 20
INDEX_ENTRY_SIZE = 16

FMT_BINARY = 0x0004  # raw bytes, no NUL terminator guaranteed
FMT_UTF8 = 0x0204    # NUL-terminated UTF-8 string
FMT_INT32 = 0x0404   # uint32, little-endian


class SfoError(Exception):
    pass


class SfoFile:
    def __init__(self, values: dict[str, int | str | bytes]):
        self.values = values

    def __getitem__(self, key: str) -> int | str | bytes:
        return self.values[key]

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    @property
    def title(self) -> str | None:
        return self.values.get("TITLE")

    @property
    def sub_title(self) -> str | None:
        return self.values.get("SUB_TITLE")

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

        values: dict[str, int | str | bytes] = {}
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

            value_start = data_table_start + data_offset
            raw = data[value_start:value_start + data_len]

            if data_fmt == FMT_INT32:
                value: int | str | bytes = struct.unpack("<I", raw.ljust(4, b"\x00")[:4])[0]
            elif data_fmt == FMT_UTF8:
                value = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            else:
                value = raw

            values[key] = value

        return cls(values)

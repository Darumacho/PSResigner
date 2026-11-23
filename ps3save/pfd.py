"""
Pure-Python port of flatz's pfdtool (PARAM.PFD reader/validator/resigner).

PARAM.PFD binds a PS3 save (or trophy set) to a console (console_id /
"IDPS") and to a local user slot (user_id), by storing per-file HMAC-SHA1
hashes keyed off those identifiers, plus a tamper-evident Merkle-ish
structure (a hash table of per-file-name HMAC chains, wrapped in a
top/bottom HMAC, wrapped in an AES-CBC "signature" blob).

Resigning a save for a different console/account means: decrypt that
signature blob, recompute every hash using the *new* console_id /
user_id, then re-encrypt and write it back. That's exactly what
`PfdFile.resign()` below does; it mirrors pfdtool's
`backend_cmd_update()` (import -> update -> export) call for call.

Layout/algorithm reference: bucanero/pfd_sfo_tools (pfdtool, GPL,
originally by flatz, 2012) and bucanero/apollo-ps3 (GPL).
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from Crypto.Cipher import AES

from . import keys

PFD_MAGIC = 0x50464442  # "PFDB"
PFD_VERSION_V3 = 3
PFD_VERSION_V4 = 4

HEADER_SIZE = 16
HEADER_KEY_SIZE = 16
SIGNATURE_SIZE = 64  # bottom_hash(20) + top_hash(20) + hash_key(20) + 4 pad
HASH_SIZE = 20
HASH_KEY_SIZE = 20
HASH_TABLE_HEADER_SIZE = 24  # capacity, num_reserved, num_used (u64 each)
ENTRY_INDEX_SIZE = 8
ENTRY_NAME_SIZE = 65
ENTRY_KEY_SIZE = 64
ENTRY_SIZE = 272
FILE_SIZE_ALIGNMENT = 16

CONSOLE_ID_SIZE = 16
USER_ID_SIZE = 8
AUTHENTICATION_ID_SIZE = 8
DISC_HASH_KEY_SIZE = 16
SECURE_FILE_ID_SIZE = 16

# entry->file_hashes[] indices
HASH_FILE = 0           # file content, keyed by a fixed per-filename key
HASH_FILE_CID = 1        # PARAM.SFO only (non-trophy): keyed by console_id
HASH_FILE_DHK_CID2 = 2    # PARAM.SFO only: disc_hash_key (non-trophy) or shuffled console_id (trophy)
HASH_FILE_AID_UID = 3     # PARAM.SFO only: authentication_id (non-trophy) or shuffled user_id (trophy)


def _shuffle(src: bytes, out_size: int, overrides: dict[int, int]) -> bytes:
    """Reproduce pfdtool's byte-interleave used to build hash keys / IVs.

    `overrides` maps output index -> fixed byte value; every other output
    byte is taken from `src` in order.
    """
    out = bytearray(out_size)
    j = 0
    for i in range(out_size):
        if i in overrides:
            out[i] = overrides[i]
        else:
            out[i] = src[j % len(src)]
            j += 1
    return bytes(out)


def _shuffle16(src: bytes, overrides: dict[int, int]) -> bytes:
    return _shuffle(src, 16, overrides)


_SHUFFLE_OVERRIDES = {1: 11, 2: 15, 5: 14, 8: 10}

# Turns a games.conf secure_file_id (16 bytes) into the 20-byte HMAC key for
# a save file pfdtool/Apollo don't have a fixed name for (anything besides
# PARAM.SFO and the TROP*.DAT names) -- pfdtool's
# pfd_generate_hash_key_for_secure_file_id(). Same byte-interleave as
# _SHUFFLE_OVERRIDES, just widened to 20 bytes since a HASH_KEY is 20 bytes
# while a secure_file_id is only 16.
SECURE_FILE_ID_KEY_SIZE = 20


def _secure_file_id_to_hash_key(secure_file_id: bytes) -> bytes:
    return _shuffle(secure_file_id, SECURE_FILE_ID_KEY_SIZE, _SHUFFLE_OVERRIDES)


def hmac_sha1(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha1).digest()


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).decrypt(data)


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).encrypt(data)


def _xor(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big")


def _counter_keystream(cipher, num_blocks: int) -> bytes:
    """AES-ECB of the per-block counters [u64 block index, big-endian][u64 0].
    Big-endian because Apollo/pfdtool write the counter with a native store on
    the PS3 (PowerPC); verified against real saves (little-endian gives noise)."""
    counters = b"".join(struct.pack(">QQ", i, 0) for i in range(num_blocks))
    return cipher.encrypt(counters)


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


class PfdError(Exception):
    pass


@dataclass
class PfdEntry:
    offset: int                 # byte offset of this entry within entry_table
    additional_index: int
    file_name: str
    key: bytes                  # 64 bytes, still AES-wrapped
    file_hashes: list[bytes]    # 4 x 20 bytes
    file_size: int


@dataclass
class PfdConfig:
    console_id: bytes = b"\x00" * CONSOLE_ID_SIZE
    user_id: bytes = b"00000000"
    authentication_id: bytes = keys.AUTHENTICATION_ID
    syscon_manager_key: bytes = keys.SYSCON_MANAGER_KEY
    keygen_key: bytes = keys.KEYGEN_KEY
    savegame_param_sfo_key: bytes = keys.SAVEGAME_PARAM_SFO_KEY
    trophy_param_sfo_key: bytes = keys.TROPHY_PARAM_SFO_KEY
    tropsys_dat_key: bytes = keys.TROPSYS_DAT_KEY
    tropusr_dat_key: bytes = keys.TROPUSR_DAT_KEY
    troptrns_dat_key: bytes = keys.TROPTRNS_DAT_KEY
    tropconf_sfm_key: bytes = keys.TROPCONF_SFM_KEY
    disc_hash_key: bytes = keys.FALLBACK_DISC_HASH_KEY
    # Per-game override, from games.conf (ps3save/games_conf.py): given a
    # file name, returns its 16-byte secure_file_id, or None if this game
    # isn't in that database / doesn't have an entry for that file. Left
    # unset, every non-PARAM.SFO/non-TROP*.DAT file stays unhashable, same
    # as before games.conf support existed.
    secure_file_id_lookup: Callable[[str], bytes | None] | None = None

    @staticmethod
    def user_id_from_int(n: int) -> bytes:
        return f"{n:08d}".encode("ascii")

    @staticmethod
    def console_id_from_hex(h: str) -> bytes:
        raw = bytes.fromhex(h.strip().replace(" ", ""))
        if len(raw) != CONSOLE_ID_SIZE:
            raise PfdError(f"console_id must be {CONSOLE_ID_SIZE} bytes ({CONSOLE_ID_SIZE * 2} hex chars)")
        return raw


class PfdFile:
    """Parsed PARAM.PFD, able to recompute and re-export its signatures."""

    def __init__(self, data: bytes, config: PfdConfig):
        self.config = config
        self.raw = bytearray(data)
        self._parse_header_and_signature()
        self._parse_hash_table()
        self._parse_entries()
        self.is_trophy = any(
            e.file_name.upper() in ("TROPSYS.DAT", "TROPCONF.SFM", "TROPUSR.DAT", "TROPTRNS.DAT")
            for e in self.entries[: self.num_used]
        )

    # ---- loading -----------------------------------------------------

    @classmethod
    def load(cls, path: str | Path, config: PfdConfig) -> "PfdFile":
        data = Path(path).read_bytes()
        return cls(data, config)

    def _parse_header_and_signature(self) -> None:
        magic, version = struct.unpack_from(">QQ", self.raw, 0)
        if magic != PFD_MAGIC:
            raise PfdError(f"Not a PARAM.PFD file (bad magic 0x{magic:016X})")
        if version not in (PFD_VERSION_V3, PFD_VERSION_V4):
            raise PfdError(f"Unsupported PARAM.PFD version {version}")
        self.version = version

        self.header_key = bytes(self.raw[HEADER_SIZE:HEADER_SIZE + HEADER_KEY_SIZE])

        sig_off = HEADER_SIZE + HEADER_KEY_SIZE
        sig_enc = bytes(self.raw[sig_off:sig_off + SIGNATURE_SIZE])
        sig_plain = aes_cbc_decrypt(self.config.syscon_manager_key, self.header_key, sig_enc)

        self.bottom_hash = sig_plain[0:20]
        self.top_hash = sig_plain[20:40]
        self.hash_key = sig_plain[40:60]
        self._sig_padding = sig_plain[60:64]

        if version == PFD_VERSION_V4:
            self.real_hash_key = hmac_sha1(self.config.keygen_key, self.hash_key)
        else:
            self.real_hash_key = self.hash_key

        self._sig_offset = sig_off

    def _parse_hash_table(self) -> None:
        off = self._sig_offset + SIGNATURE_SIZE
        self._hash_table_offset = off
        capacity, num_reserved, num_used = struct.unpack_from(">QQQ", self.raw, off)
        self.capacity = capacity
        self.num_reserved = num_reserved
        self.num_used = num_used

        entries_off = off + HASH_TABLE_HEADER_SIZE
        self._hash_table_entries_offset = entries_off
        self.hash_table_entries = list(
            struct.unpack_from(f">{capacity}Q", self.raw, entries_off)
        )
        self._hash_table_size = HASH_TABLE_HEADER_SIZE + capacity * ENTRY_INDEX_SIZE

    def _parse_entries(self) -> None:
        self._entry_table_offset = self._hash_table_offset + self._hash_table_size
        self.entries: list[PfdEntry] = []
        for i in range(self.num_reserved):
            eoff = self._entry_table_offset + i * ENTRY_SIZE
            additional_index = struct.unpack_from(">Q", self.raw, eoff)[0]
            name_raw = bytes(self.raw[eoff + 8:eoff + 8 + ENTRY_NAME_SIZE])
            file_name = name_raw.split(b"\x00", 1)[0].decode("latin-1")
            key_off = eoff + 8 + ENTRY_NAME_SIZE + 7
            entry_key = bytes(self.raw[key_off:key_off + ENTRY_KEY_SIZE])
            fh_off = key_off + ENTRY_KEY_SIZE
            file_hashes = [
                bytes(self.raw[fh_off + j * HASH_SIZE: fh_off + (j + 1) * HASH_SIZE])
                for j in range(4)
            ]
            size_off = eoff + ENTRY_SIZE - 8
            file_size = struct.unpack_from(">Q", self.raw, size_off)[0]
            self.entries.append(
                PfdEntry(eoff, additional_index, file_name, entry_key, file_hashes, file_size)
            )

        esig_off = self._entry_table_offset + self.num_reserved * ENTRY_SIZE
        self._entry_sig_offset = esig_off
        self._entry_sig_size = self.capacity * HASH_SIZE

    # ---- hash-table bucket helpers ------------------------------------

    def _bucket_index(self, file_name: str) -> int:
        h = 0
        for ch in file_name.encode("latin-1"):
            h = (h * 31 + ch) & 0xFFFFFFFFFFFFFFFF
        return h % self.capacity

    def find_entry(self, file_name: str) -> PfdEntry | None:
        for e in self.entries[: self.num_used]:
            if e.file_name.lower() == file_name.lower():
                return e
        return None

    # ---- per-file hash keys --------------------------------------------

    def _param_sfo_hash_key(self, which: int) -> bytes:
        cfg = self.config
        if not self.is_trophy:
            if which == HASH_FILE:
                return cfg.savegame_param_sfo_key
            if which == HASH_FILE_CID:
                return cfg.console_id
            if which == HASH_FILE_DHK_CID2:
                return cfg.disc_hash_key
            if which == HASH_FILE_AID_UID:
                return cfg.authentication_id
        else:
            if which == HASH_FILE:
                return cfg.trophy_param_sfo_key
            if which == HASH_FILE_DHK_CID2:
                return _shuffle16(cfg.console_id, {4: 11, 8: 10, 9: 14, 10: 15})
            if which == HASH_FILE_AID_UID:
                uid = cfg.user_id
                out = bytearray(16)
                for i in range(16):
                    if i == 3:
                        out[i] = 11
                    elif i == 7:
                        out[i] = 14
                    else:
                        out[i] = uid[i % 8]
                return bytes(out)
        raise PfdError(f"hash type {which} not valid for this entry")

    def _entry_hash_key(self, entry: PfdEntry, which: int) -> bytes:
        name = entry.file_name.upper()
        cfg = self.config
        if name == "PARAM.SFO":
            return self._param_sfo_hash_key(which)
        if which != HASH_FILE:
            raise PfdError("only HASH_FILE applies to non-PARAM.SFO entries")
        if name == "TROPSYS.DAT":
            return cfg.tropsys_dat_key
        if name == "TROPUSR.DAT":
            return cfg.tropusr_dat_key
        if name == "TROPTRNS.DAT":
            return cfg.troptrns_dat_key
        if name == "TROPCONF.SFM":
            return cfg.tropconf_sfm_key
        if cfg.secure_file_id_lookup is not None:
            secure_file_id = cfg.secure_file_id_lookup(entry.file_name)
            if secure_file_id is not None:
                return _secure_file_id_to_hash_key(secure_file_id)
        raise PfdError(
            f"no fixed hash key known for '{entry.file_name}' "
            "(needs a per-game 'secure file ID' this tool doesn't carry)"
        )

    def can_rehash_file(self, file_name: str) -> bool:
        """Whether recompute_file_hashes() can actually recompute this
        file's stored hash(es) to match new content. False for files that
        need a per-game "secure file ID" key this tool doesn't have (every
        real save file besides PARAM.SFO and the fixed TROP*.DAT names) --
        patching such a file leaves its stored hash pointing at the OLD
        content, which the PS3 then rejects as corrupted (error 80029207,
        confirmed against a real save when this was still unguarded)."""
        entry = self.find_entry(file_name)
        if entry is None:
            return False
        for which in self._applicable_hash_types(entry):
            try:
                self._entry_hash_key(entry, which)
            except PfdError:
                return False
        return True

    # ---- protected file contents ------------------------------------------
    #
    # In a save, every PARAM.PFD entry besides PARAM.SFO is a "secure" file whose
    # content is encrypted (trophy sets' TROP* files are stored in the clear) (apollo-ps3 source/pfd.c, pfd_decrypt_file/pfd_encrypt_file):
    #   file key = first 16 bytes of AES-128-CBC-decrypt(syscon_manager_key,
    #              iv = first 16 bytes of the entry's hash key, entry.key)
    #   block i  : plain = AES_dec(K, block) XOR AES_enc(K, counter_i)
    #              block = AES_enc(K, plain XOR AES_enc(K, counter_i))
    # on 16-byte blocks of the file zero-padded to FILE_SIZE_ALIGNMENT; the
    # plain text is entry.file_size bytes long. Cheat codes (.savepatch) address
    # the plain text, so a cheat must be applied between decrypt and encrypt.

    _NOT_ENCRYPTED = ("PARAM.SFO", "TROPSYS.DAT", "TROPUSR.DAT", "TROPTRNS.DAT", "TROPCONF.SFM")

    def is_encrypted(self, entry: PfdEntry) -> bool:
        return entry.file_name.upper() not in self._NOT_ENCRYPTED

    def can_decrypt_file(self, file_name: str) -> bool:
        """Whether this file's content can be decrypted (and re-encrypted):
        it has a PARAM.PFD entry, is encrypted, and its key is known."""
        entry = self.find_entry(file_name)
        if entry is None or not self.is_encrypted(entry):
            return False
        try:
            self._file_key(entry)
        except PfdError:
            return False
        return True

    def _file_key(self, entry: PfdEntry) -> bytes:
        if not self.is_encrypted(entry):
            raise PfdError(f"'{entry.file_name}' is not encrypted")
        hash_key = self._entry_hash_key(entry, HASH_FILE)   # PfdError if the secure file ID is unknown
        return aes_cbc_decrypt(self.config.syscon_manager_key, hash_key[:16], entry.key)[:16]

    def decrypt_file_data(self, entry: PfdEntry, data: bytes) -> bytes:
        """On-disk (encrypted) content -> plain text, entry.file_size bytes long."""
        cipher = AES.new(self._file_key(entry), AES.MODE_ECB)
        padded = data + b"\x00" * (-len(data) % FILE_SIZE_ALIGNMENT)
        plain = _xor(cipher.decrypt(padded), _counter_keystream(cipher, len(padded) // 16))
        return plain[: entry.file_size]

    def encrypt_file_data(self, entry: PfdEntry, plain: bytes) -> bytes:
        """Plain text -> on-disk content (zero-padded to FILE_SIZE_ALIGNMENT).
        Sets entry.file_size to the plain text's length."""
        cipher = AES.new(self._file_key(entry), AES.MODE_ECB)
        padded = plain + b"\x00" * (-len(plain) % FILE_SIZE_ALIGNMENT)
        entry.file_size = len(plain)
        return cipher.encrypt(_xor(padded, _counter_keystream(cipher, len(padded) // 16)))

    def _applicable_hash_types(self, entry: PfdEntry) -> list[int]:
        if entry.file_name.upper() != "PARAM.SFO":
            return [HASH_FILE]
        if self.is_trophy:
            return [HASH_FILE, HASH_FILE_DHK_CID2, HASH_FILE_AID_UID]
        return [HASH_FILE, HASH_FILE_CID, HASH_FILE_DHK_CID2, HASH_FILE_AID_UID]

    # ---- validation / recompute -----------------------------------------

    def file_path_for(self, directory: Path, entry: PfdEntry) -> Path:
        return directory / entry.file_name

    def check_file_hashes(self, directory: Path) -> dict[str, dict[int, bool]]:
        """Compare on-disk file contents against the hashes stored in PARAM.PFD."""
        results: dict[str, dict[int, bool]] = {}
        for entry in self.entries[: self.num_used]:
            per_entry: dict[int, bool] = {}
            for which in self._applicable_hash_types(entry):
                try:
                    hkey = self._entry_hash_key(entry, which)
                except PfdError:
                    continue
                fp = self.file_path_for(directory, entry)
                if not fp.exists():
                    per_entry[which] = False
                    continue
                computed = hmac_sha1(hkey, fp.read_bytes())
                per_entry[which] = hmac.compare_digest(computed, entry.file_hashes[which])
            results[entry.file_name] = per_entry
        return results

    def recompute_file_hashes(self, directory: Path, only_param_sfo: bool = False) -> None:
        """Recompute entry.file_hashes[...] from the files actually on disk."""
        for entry in self.entries[: self.num_used]:
            if only_param_sfo and entry.file_name.upper() != "PARAM.SFO":
                continue
            fp = self.file_path_for(directory, entry)
            if not fp.exists():
                continue
            data = fp.read_bytes()
            for which in self._applicable_hash_types(entry):
                try:
                    hkey = self._entry_hash_key(entry, which)
                except PfdError:
                    continue
                entry.file_hashes[which] = hmac_sha1(hkey, data)
            # An encrypted file is stored zero-padded to FILE_SIZE_ALIGNMENT while
            # entry.file_size is its plain-text length: keep that length when the
            # on-disk size is just its padded form (otherwise the game would read
            # the padding as data).
            if not (self.is_encrypted(entry)
                    and align_up(entry.file_size, FILE_SIZE_ALIGNMENT) == len(data)):
                entry.file_size = len(data)

    def _entry_bucket_chain_hash(self, bucket: int) -> bytes:
        idx = self.hash_table_entries[bucket]
        if idx >= self.num_reserved:
            return hmac_sha1(self.real_hash_key, b"")
        mac = hmac.new(self.real_hash_key, digestmod=hashlib.sha1)
        while idx < self.num_reserved:
            entry = self.entries[idx]
            mac.update(entry.file_name.encode("latin-1").ljust(ENTRY_NAME_SIZE, b"\x00"))
            mac.update(self._entry_data_block(entry))
            idx = entry.additional_index
        return mac.digest()

    def _entry_data_block(self, entry: PfdEntry) -> bytes:
        """The 192-byte 'data' portion hashed per entry: key + file_hashes +
        the trailing 40-byte padding + file_size (everything from the key
        onward, i.e. the entry minus additional_index/file_name/pad7)."""
        return (
            entry.key
            + b"".join(entry.file_hashes)
            + b"\x00" * 40
            + struct.pack(">Q", entry.file_size)
        )

    def recompute_structure(self) -> None:
        """Rebuild entry_signature_table + bottom_hash + top_hash."""
        default_hash = hmac_sha1(self.real_hash_key, b"")
        esig = bytearray(self._entry_sig_size)
        for bucket in range(self.capacity):
            idx = self.hash_table_entries[bucket]
            h = default_hash if idx >= self.num_reserved else self._entry_bucket_chain_hash(bucket)
            esig[bucket * HASH_SIZE:(bucket + 1) * HASH_SIZE] = h
        self._entry_sig_bytes = bytes(esig)

        hash_table_bytes = self._hash_table_bytes()
        self.top_hash = hmac_sha1(self.real_hash_key, hash_table_bytes)
        self.bottom_hash = hmac_sha1(self.real_hash_key, self._entry_sig_bytes)

    def _hash_table_bytes(self) -> bytes:
        header = struct.pack(">QQQ", self.capacity, self.num_reserved, self.num_used)
        body = struct.pack(f">{self.capacity}Q", *self.hash_table_entries)
        return header + body

    # ---- resigning --------------------------------------------------------

    def resign(self, directory: str | Path, update_file_hashes: bool = True) -> None:
        """Rebind this PARAM.PFD to self.config's console_id/user_id.

        Equivalent to `pfdtool --update <directory>`: recompute every file
        hash (PARAM.SFO's console/account bindings included) then rebuild
        the structural signature.
        """
        directory = Path(directory)
        if update_file_hashes:
            self.recompute_file_hashes(directory)
        self.recompute_structure()

    def save(self, path: str | Path) -> None:
        self._write_entries()
        self._write_hash_table()
        self._write_entry_signatures()
        self._write_signature_block()
        Path(path).write_bytes(bytes(self.raw))

    # ---- serialization helpers ---------------------------------------

    def _write_entries(self) -> None:
        for entry in self.entries[: self.num_reserved]:
            fh_off = entry.offset + 8 + ENTRY_NAME_SIZE + 7 + ENTRY_KEY_SIZE
            for j, h in enumerate(entry.file_hashes):
                self.raw[fh_off + j * HASH_SIZE: fh_off + (j + 1) * HASH_SIZE] = h
            size_off = entry.offset + ENTRY_SIZE - 8
            struct.pack_into(">Q", self.raw, size_off, entry.file_size)

    def _write_hash_table(self) -> None:
        struct.pack_into(
            ">QQQ", self.raw, self._hash_table_offset,
            self.capacity, self.num_reserved, self.num_used,
        )
        struct.pack_into(
            f">{self.capacity}Q", self.raw, self._hash_table_entries_offset,
            *self.hash_table_entries,
        )

    def _write_entry_signatures(self) -> None:
        self.raw[self._entry_sig_offset:self._entry_sig_offset + self._entry_sig_size] = (
            self._entry_sig_bytes
        )

    def _write_signature_block(self) -> None:
        sig_plain = self.bottom_hash + self.top_hash + self.hash_key + self._sig_padding
        sig_enc = aes_cbc_encrypt(self.config.syscon_manager_key, self.header_key, sig_plain)
        self.raw[self._sig_offset:self._sig_offset + SIGNATURE_SIZE] = sig_enc

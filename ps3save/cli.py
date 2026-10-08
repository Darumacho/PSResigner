"""Command-line interface for ps3save: info / check / resign."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .pfd import PfdConfig, PfdError, PfdFile
from .sfo import SfoError, SfoFile

HASH_NAMES = {0: "FILE", 1: "FILE_CID", 2: "FILE_DHK_CID2", 3: "FILE_AID_UID"}


def _parse_user_id(value: str) -> bytes:
    if value.isdigit():
        return PfdConfig.user_id_from_int(int(value))
    raw = value.encode("ascii")
    if len(raw) != 8:
        raise argparse.ArgumentTypeError("--user-id must be an integer or an 8-character string")
    return raw


def _config_from_args(args: argparse.Namespace) -> PfdConfig:
    kwargs = {}
    if args.console_id is not None:
        kwargs["console_id"] = PfdConfig.console_id_from_hex(args.console_id)
    if args.user_id is not None:
        kwargs["user_id"] = _parse_user_id(args.user_id)
    return PfdConfig(**kwargs)


def _user_id_int(args: argparse.Namespace) -> int | None:
    """The numeric local user slot, when --user-id was given as a plain integer
    (PARAM.SFO's PARAMS blob stores it as a raw u32, unlike PfdConfig's 8-char
    ASCII form, so an arbitrary 8-char --user-id has no SFO equivalent)."""
    if args.user_id is not None and args.user_id.isdigit():
        return int(args.user_id)
    return None


def _load(save_dir: Path, config: PfdConfig) -> PfdFile:
    pfd_path = save_dir / "PARAM.PFD"
    if not pfd_path.exists():
        raise PfdError(f"No PARAM.PFD in {save_dir}")
    return PfdFile.load(pfd_path, config)


def cmd_info(args: argparse.Namespace) -> int:
    save_dir = Path(args.path)
    pf = _load(save_dir, _config_from_args(args))

    sfo_path = save_dir / "PARAM.SFO"
    if sfo_path.exists():
        try:
            sfo = SfoFile.load(sfo_path)
            if sfo.title:
                print(f"Title             : {sfo.title}")
            if sfo.sub_title:
                print(f"Sub-title         : {sfo.sub_title}")
            account_id = sfo.get("ACCOUNT_ID")
            if isinstance(account_id, bytes) and account_id.strip(b"\x00"):
                print(f"Account ID        : {account_id.decode('ascii', errors='replace')}")
        except SfoError as e:
            print(f"(couldn't read PARAM.SFO: {e})")

    print(f"PARAM.PFD version : {pf.version}")
    print(f"Trophy save       : {pf.is_trophy}")
    print(f"Entries           : {pf.num_used} used / {pf.num_reserved} reserved "
          f"(hash table capacity {pf.capacity})")
    print()
    for entry in pf.entries[: pf.num_used]:
        print(f"  {entry.file_name:<24s} {entry.file_size:>10d} bytes")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    save_dir = Path(args.path)
    config = _config_from_args(args)
    pf = _load(save_dir, config)
    results = pf.check_file_hashes(save_dir)

    print(f"Checking against console_id={config.console_id.hex()} user_id={config.user_id.decode()}")
    print()

    all_ok = True
    for file_name, per_hash in results.items():
        if not per_hash:
            print(f"  {file_name:<24s} SKIPPED (no fixed hash key known for this file)")
            continue
        parts = []
        for which, ok in per_hash.items():
            parts.append(f"{HASH_NAMES[which]}={'OK' if ok else 'MISMATCH'}")
            if not ok:
                all_ok = False
        print(f"  {file_name:<24s} " + " ".join(parts))

    print()
    print("Result: all checkable hashes OK" if all_ok else "Result: one or more hash mismatches")
    return 0 if all_ok else 1


def cmd_resign(args: argparse.Namespace) -> int:
    if args.console_id is None:
        print("error: --console-id is required for resign", file=sys.stderr)
        return 2

    source_dir = Path(args.path)
    config = _config_from_args(args)

    if args.output:
        target_dir = Path(args.output)
        if target_dir.exists():
            print(f"error: output directory already exists: {target_dir}", file=sys.stderr)
            return 2
        shutil.copytree(source_dir, target_dir)
    else:
        target_dir = source_dir
        if not args.no_backup:
            backup_dir = target_dir.parent / f"{target_dir.name}_backup_{datetime.now():%Y%m%d_%H%M%S}"
            shutil.copytree(target_dir, backup_dir)
            print(f"Backed up original folder to {backup_dir}")

    sfo_path = target_dir / "PARAM.SFO"
    if (args.account_id or args.user_id is not None) and sfo_path.exists():
        sfo = SfoFile.load(sfo_path)
        if args.account_id:
            sfo.set_account_id(args.account_id)
        uid_int = _user_id_int(args)
        if uid_int is not None:
            sfo.set_user_id(uid_int)
        sfo.save(sfo_path)

    pf = _load(target_dir, config)
    pf.resign(target_dir)
    pf.save(target_dir / "PARAM.PFD")

    message = f"Resigned {target_dir} to console_id={config.console_id.hex()} user_id={config.user_id.decode()}"
    if args.account_id:
        message += f" account_id={args.account_id.lower()}"
    print(message)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ps3save", description="PS3 PARAM.PFD save resigner")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("path", help="Path to the save directory (containing PARAM.PFD)")
        p.add_argument("--console-id", help="Target console_id / IDPS, 32 hex chars")
        p.add_argument("--user-id", help="Target user_id, as an integer (e.g. 1) or 8-char string")

    p_info = sub.add_parser("info", help="Show PARAM.PFD structure")
    add_common(p_info)
    p_info.set_defaults(func=cmd_info)

    p_check = sub.add_parser("check", help="Verify file hashes against a console_id/user_id")
    add_common(p_check)
    p_check.set_defaults(func=cmd_check)

    p_resign = sub.add_parser("resign", help="Rebind the save to a new console_id/user_id")
    add_common(p_resign)
    p_resign.add_argument("--account-id", help="Target PSN account_id, 16 hex chars "
                                                "(patches PARAM.SFO's ACCOUNT_ID + PARAMS)")
    p_resign.add_argument("--output", help="Write the resigned save to this new directory "
                                            "instead of modifying it in place")
    p_resign.add_argument("--no-backup", action="store_true",
                           help="Don't keep a backup copy of the folder when resigning in place")
    p_resign.set_defaults(func=cmd_resign)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PfdError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

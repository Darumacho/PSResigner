"""Scan removable/mounted drives for a PS3 "copy to USB" SAVEDATA
folder -- <drive>/PS3/SAVEDATA/<save-folder>/PARAM.PFD is the layout
the PS3's own storage-device backup and tools like multiMAN use, so a
save can be picked straight off a USB stick instead of hunting down
wherever it got copied to.
"""

from __future__ import annotations

import string
import sys
from pathlib import Path


def _candidate_roots() -> list[Path]:
    """Every mounted drive/volume worth checking -- best-effort per
    platform, never raises (a drive that errors out when queried, like
    an empty optical drive, is just skipped)."""
    if sys.platform == "win32":
        roots = []
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            try:
                if drive.exists():
                    roots.append(drive)
            except OSError:
                continue
        return roots

    if sys.platform == "darwin":
        try:
            return [p for p in Path("/Volumes").iterdir() if p.is_dir()]
        except OSError:
            return []

    # Linux: common removable-media mount points (/media/<user>/<label>,
    # /mnt/<label>).
    roots: list[Path] = []
    for base in (Path("/media"), Path("/mnt")):
        try:
            for entry in base.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    sub = [p for p in entry.iterdir() if p.is_dir()]
                except OSError:
                    sub = []
                roots.extend(sub or [entry])
        except OSError:
            continue
    return roots


def find_ps3_save_folders() -> list[Path]:
    """Every <root>/PS3/SAVEDATA/<name> folder, on any detected drive,
    that actually contains a PARAM.PFD."""
    found: list[Path] = []
    for root in _candidate_roots():
        savedata_dir = root / "PS3" / "SAVEDATA"
        try:
            if not savedata_dir.is_dir():
                continue
            for entry in sorted(savedata_dir.iterdir()):
                if entry.is_dir() and (entry / "PARAM.PFD").exists():
                    found.append(entry)
        except OSError:
            continue
    return found

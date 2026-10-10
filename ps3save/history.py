"""Persisted log of past resign operations, for traceability -- same
home-directory JSON pattern as profiles.py/settings.py."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

HISTORY_PATH = Path.home() / ".ps3save" / "history.json"

# Keep the file from growing forever; plenty for "what did I do recently".
MAX_ENTRIES = 200


@dataclass
class HistoryEntry:
    timestamp: str  # datetime.now().isoformat(timespec="seconds")
    action: str  # e.g. "resign_in_place", "resign_to_new_folder", "apply_cheat:<name>"
    folder: str
    console_id: str
    user_id: str
    account_id: str = ""


def load_history(path: Path = HISTORY_PATH) -> list[HistoryEntry]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [HistoryEntry(**entry) for entry in raw]


def append_history(entry: HistoryEntry, path: Path = HISTORY_PATH) -> None:
    entries = load_history(path)[-(MAX_ENTRIES - 1):]
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(e) for e in entries], indent=2), encoding="utf-8")


def clear_history(path: Path = HISTORY_PATH) -> None:
    path.write_text("[]", encoding="utf-8")

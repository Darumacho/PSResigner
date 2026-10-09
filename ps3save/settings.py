"""Tiny persisted app settings (currently just the dark/light theme
choice), stored next to profiles.json."""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".ps3save" / "settings.json"


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_settings(data: dict, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

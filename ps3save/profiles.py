"""Named Owner profiles (name -> user_id/account_id), stored as JSON in the
user's home directory so they persist across runs/updates -- same idea as
KG's "[PS3] Save Resigner" Profiles window, minus the game-specific stuff.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

PROFILES_DIR = Path.home() / ".ps3save"
PROFILES_PATH = PROFILES_DIR / "profiles.json"


class ProfileError(Exception):
    pass


@dataclass
class Profile:
    name: str
    user_id: int = 0
    account_id: str = ""  # 16 hex chars, or "" if unset
    console_id: str = ""  # 32 hex chars, or "" to leave the main window's Console ID untouched

    def validate(self) -> None:
        if not self.name.strip():
            raise ProfileError("Profile name can't be empty")
        if not 0 <= self.user_id <= 0xFFFFFFFF:
            raise ProfileError("User ID must fit in 32 bits")
        if self.account_id and (
            len(self.account_id) != 16
            or not all(c in "0123456789abcdefABCDEF" for c in self.account_id)
        ):
            raise ProfileError("Account ID must be 16 hex characters (or left empty)")
        if self.console_id and (
            len(self.console_id) != 32
            or not all(c in "0123456789abcdefABCDEF" for c in self.console_id)
        ):
            raise ProfileError("Console ID must be 32 hex characters (or left empty)")


def load_profiles(path: Path = PROFILES_PATH) -> list[Profile]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ProfileError(f"Couldn't read {path}: {e}")
    return [Profile(**entry) for entry in raw]


def save_profiles(profiles: list[Profile], path: Path = PROFILES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [asdict(p) for p in profiles]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

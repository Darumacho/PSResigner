"""Checks GitHub for newer PSResigner releases and, on Windows, downloads
and installs them.

Same shape as the Cardex-Companion .NET updater (its UpdateService.cs):
query the repo's latest release, compare its tag to our own __version__,
and if newer, grab the matching asset and hand off to a tiny detached
script that waits for this process to exit before overwriting it -- a
running Windows exe can't replace its own file while it's executing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

GITHUB_REPO = "Darumacho/PSResigner"
ASSET_NAME = "PSResigner.exe"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"

_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_USER_AGENT = "PSResigner-Updater"


@dataclass
class UpdateInfo:
    version: str
    download_url: str


def _parse_version(text: str) -> tuple[int, ...]:
    """"v1.2.3" -> (1, 2, 3); non-numeric/missing segments become 0 so a
    malformed tag never raises, just compares as low as possible."""
    out = []
    for part in text.strip().lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def check_for_update(timeout: float = 10) -> UpdateInfo | None:
    """Best-effort, like the games.conf lookup: any network/parsing
    problem (offline, rate-limited, no releases yet) just means no update
    is reported, never a user-facing error."""
    if sys.platform != "win32":
        return None
    try:
        request = urllib.request.Request(
            _API_URL,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))

        tag = data.get("tag_name", "")
        remote = _parse_version(tag)
        current = _parse_version(__version__)
        if not remote or remote <= current:
            return None

        download_url = next(
            (asset.get("browser_download_url") for asset in data.get("assets", [])
             if asset.get("name") == ASSET_NAME),
            None,
        )
        if not download_url:
            return None

        return UpdateInfo(version=tag.lstrip("vV"), download_url=download_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError, OSError):
        return None


def download_and_install(info: UpdateInfo, progress: Callable[[int], None] | None = None) -> None:
    """Downloads the new exe, then launches a detached PowerShell script
    that waits for this process to exit, copies the download over the
    current exe, relaunches it and deletes itself. Only valid for a
    frozen (PyInstaller) build -- sys.executable is the interpreter, not
    PSResigner.exe, when running from source."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("download_and_install() only applies to a frozen PSResigner.exe build")

    current_exe = Path(sys.executable).resolve()
    temp_exe = Path(tempfile.gettempdir()) / "PSResigner_update.exe"

    request = urllib.request.Request(info.download_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        total = int(response.headers.get("Content-Length", 0))
        read = 0
        last_reported = -1
        with open(temp_exe, "wb") as out:
            while chunk := response.read(81920):
                out.write(chunk)
                read += len(chunk)
                if progress and total:
                    percent = int(read * 100 / total)
                    if percent != last_reported:
                        progress(percent)
                        last_reported = percent

    script = Path(tempfile.gettempdir()) / "psresigner_update.ps1"
    script.write_text(
        f"""
$proc = Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue
if ($proc) {{ $proc.WaitForExit(30000) }}
Start-Sleep -Milliseconds 1500
Copy-Item -LiteralPath '{temp_exe}' -Destination '{current_exe}' -Force
Start-Sleep -Milliseconds 500

# The freshly-copied onefile exe occasionally fails its own self-extraction
# on the very first launch right after being overwritten (observed as a
# "Failed to load Python DLL" bootloader error -- a transient filesystem
# race, not a corrupt download: the file's hash is fine and a second
# launch always works). One silent retry covers it instead of leaving the
# user stuck looking at that dialog after an update they didn't ask to
# debug.
$started = Start-Process -FilePath '{current_exe}' -PassThru
Start-Sleep -Milliseconds 1500
if ($started.HasExited) {{
    Start-Process -FilePath '{current_exe}'
}}

Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
""".strip(),
        encoding="utf-8",
    )

    subprocess.Popen(
        [
            "powershell.exe", "-NonInteractive", "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass", "-File", str(script),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

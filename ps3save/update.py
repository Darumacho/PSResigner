"""Checks GitHub for newer PSResigner releases and downloads/installs them
(Windows: PSResigner.exe; macOS: a zipped PSResigner.app).

Same shape as the Cardex-Companion .NET updater (its UpdateService.cs):
query the repo's latest release, compare its tag to our own __version__,
and if newer, grab the matching asset and hand off to a tiny detached
script that waits for this process to exit before overwriting it -- a
running Windows exe can't replace its own file while it's executing, and
replacing a .app bundle under a running process isn't safe either.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
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
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"

_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_USER_AGENT = "PSResigner-Updater"


def asset_name() -> str | None:
    """Release asset matching this build, or None where auto-update isn't
    supported. On macOS, one zip per architecture (platform.machine() is
    the running binary's arch, so an x86_64 build under Rosetta still asks
    for the x86_64 zip). The zip must be made with
    `ditto -c -k --keepParent PSResigner.app <zip>` (see build_mac.sh):
    plain `zip` drops the symlinks inside the bundle."""
    if sys.platform == "win32":
        return "PSResigner.exe"
    if sys.platform == "darwin":
        return f"PSResigner-macOS-{platform.machine()}.zip"
    return None


ASSET_NAME = asset_name()


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
    problem (offline, rate-limited, no releases yet, no asset for this
    platform) just means no update is reported, never a user-facing error."""
    if ASSET_NAME is None:
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


def _download(url: str, dest: Path, progress: Callable[[int], None] | None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        total = int(response.headers.get("Content-Length", 0))
        read = 0
        last_reported = -1
        with open(dest, "wb") as out:
            while chunk := response.read(81920):
                out.write(chunk)
                read += len(chunk)
                if progress and total:
                    percent = int(read * 100 / total)
                    if percent != last_reported:
                        progress(percent)
                        last_reported = percent


def download_and_install(info: UpdateInfo, progress: Callable[[int], None] | None = None) -> None:
    """Downloads the new build, then launches a detached script that waits
    for this process to exit, replaces the current exe/.app, relaunches it
    and deletes itself. The caller must then quit. Only valid for a frozen
    (PyInstaller) build -- sys.executable is the interpreter when running
    from source. Failures raise OSError (the GUI then offers the releases
    page instead)."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("download_and_install() only applies to a frozen PSResigner build")
    if sys.platform == "win32":
        _install_windows(info, progress)
    elif sys.platform == "darwin":
        _install_macos(info, progress)
    else:
        raise OSError(f"Auto-update isn't supported on {sys.platform}")


def _install_windows(info: UpdateInfo, progress: Callable[[int], None] | None) -> None:
    current_exe = Path(sys.executable).resolve()
    temp_exe = Path(tempfile.gettempdir()) / "PSResigner_update.exe"
    _download(info.download_url, temp_exe, progress)

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


def _current_app_bundle() -> Path:
    """.../PSResigner.app/Contents/MacOS/PSResigner -> .../PSResigner.app"""
    bundle = Path(sys.executable).resolve().parents[2]
    if bundle.suffix != ".app":
        raise OSError(f"Not running from a .app bundle ({sys.executable})")
    if "/AppTranslocation/" in str(bundle):
        # Gatekeeper ran the app from a read-only randomized copy (app
        # launched straight from Downloads/a disk image): the real bundle's
        # location is unknown from here.
        raise OSError("PSResigner is running from a temporary location: move PSResigner.app "
                      "to the Applications folder, relaunch it and try again")
    if not os.access(bundle.parent, os.W_OK):
        raise OSError(f"No write permission on {bundle.parent}")
    return bundle


def _install_macos(info: UpdateInfo, progress: Callable[[int], None] | None) -> None:
    current_app = _current_app_bundle()
    staging = Path(tempfile.mkdtemp(prefix="psresigner_update_"))
    try:
        archive = staging / "update.zip"
        _download(info.download_url, archive, progress)
        extracted = staging / "extracted"
        # ditto (not zipfile) restores the bundle's symlinks and permissions.
        result = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(extracted)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise OSError(f"Couldn't extract the update: {result.stderr.strip()}")
        new_app = next(extracted.glob("*.app"), None)
        if new_app is None or not (new_app / "Contents" / "MacOS").is_dir():
            raise OSError("The downloaded update doesn't contain a .app bundle")
        archive.unlink()
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Copy the new bundle next to the old one first (same volume), then swap
    # with two renames, restoring the old bundle if the second one fails.
    q = shlex.quote
    incoming = current_app.with_name(current_app.name + ".update-new")
    backup = current_app.with_name(current_app.name + ".update-old")
    script = staging / "install.sh"
    script.write_text(
        f"""#!/bin/bash
for _ in $(seq 1 60); do kill -0 {os.getpid()} 2>/dev/null || break; sleep 0.5; done
sleep 1
rm -rf {q(str(incoming))} {q(str(backup))}
if /usr/bin/ditto {q(str(new_app))} {q(str(incoming))}; then
    /usr/bin/xattr -dr com.apple.quarantine {q(str(incoming))} 2>/dev/null
    if mv {q(str(current_app))} {q(str(backup))}; then
        if mv {q(str(incoming))} {q(str(current_app))}; then
            rm -rf {q(str(backup))}
        else
            mv {q(str(backup))} {q(str(current_app))}
        fi
    fi
fi
rm -rf {q(str(incoming))}
/usr/bin/open {q(str(current_app))}
rm -rf {q(str(staging))}
""",
        encoding="utf-8",
    )
    subprocess.Popen(
        ["/bin/bash", str(script)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,   # survives this process quitting
    )

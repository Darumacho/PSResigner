# -*- mode: python ; coding: utf-8 -*-

import os
import platform

import tkinterdnd2

# tkinterdnd2 ships the native tkdnd Tcl extension as per-platform data
# files (not importable Python), so PyInstaller's own import analysis
# never sees them -- they have to be added to `datas` by hand, picking
# the same platform/arch subfolder name tkinterdnd2.TkinterDnD._require()
# resolves at runtime (relative to wherever the package ends up extracted
# under _MEIPASS). Each OS's PyInstaller run only needs its own subfolder.
_machine = os.environ.get("PROCESSOR_ARCHITECTURE", platform.machine()) if platform.system() == "Windows" else platform.machine()
_TKDND_PLATFORMS = {
    ("Darwin", "arm64"): "osx-arm64",
    ("Darwin", "x86_64"): "osx-x64",
    ("Linux", "aarch64"): "linux-arm64",
    ("Linux", "x86_64"): "linux-x64",
    ("Windows", "ARM64"): "win-arm64",
    ("Windows", "AMD64"): "win-x64",
    ("Windows", "x86"): "win-x86",
}
_tkdnd_platform = _TKDND_PLATFORMS[(platform.system(), _machine)]
_tkdnd_root = os.path.join(os.path.dirname(tkinterdnd2.__file__), "tkdnd")
tkdnd_datas = [
    (os.path.join(_tkdnd_root, name), f"tkinterdnd2/tkdnd/{name}")
    for name in (_tkdnd_platform, _tkdnd_platform + "-tcl9")
    if os.path.isdir(os.path.join(_tkdnd_root, name))
]

a = Analysis(
    ['run_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('resources', 'resources')] + tkdnd_datas,
    hiddenimports=['tkinterdnd2', 'certifi'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['hooks/rth_certifi.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if platform.system() == "Darwin":
    # macOS: onedir + BUNDLE -> PSResigner.app (PyInstaller deprecates
    # onefile inside a .app bundle: slow startup, breaks codesigning). No
    # UPX on Mac either, it corrupts signed Mach-O binaries.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='PSResigner',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name='PSResigner',
    )
    app = BUNDLE(
        coll,
        name='PSResigner.app',
        icon='resources/app_logo.icns',
        bundle_identifier='fr.free.psresigner',
        info_plist={
            'CFBundleDisplayName': 'PSResigner',
            'CFBundleShortVersionString': '0.1.0',
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='PSResigner',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=['resources/app_logo.ico'],
    )

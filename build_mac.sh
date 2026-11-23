#!/bin/bash
# Builds dist/PSResigner.app and the zip the auto-updater looks for in the
# GitHub release (PSResigner-macOS-<arch>.zip). ditto, not zip: a plain zip
# drops the symlinks inside the .app bundle.
set -euo pipefail
cd "$(dirname "$0")"
pyinstaller --noconfirm --clean PSResigner.spec
zip_name="PSResigner-macOS-$(uname -m).zip"
rm -f "dist/$zip_name"
ditto -c -k --keepParent dist/PSResigner.app "dist/$zip_name"
echo "dist/$zip_name -> attach it to the GitHub release (tag = version in ps3save/__init__.py)"

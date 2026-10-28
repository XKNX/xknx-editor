#!/usr/bin/env bash
# Build the XKNX Editor as a standalone desktop app with PyInstaller.
#
#   apps/editor-gui/tools/build_desktop.sh
#
# Output: apps/editor-gui/dist/  ("XKNX Editor.app" on macOS, a folder elsewhere).
# PyInstaller is pulled in ephemerally (uv --with), so it is not a project dependency.
set -euo pipefail

cd "$(dirname "$0")/.."  # -> apps/editor-gui

# Freeze the app from the spec (the icons are committed, see tools/make_icon.py).
uv run --package xknxeditor-gui --with pyinstaller \
    pyinstaller --noconfirm --clean xknx-editor.spec

echo
echo "Done. Result in: $(pwd)/dist/"

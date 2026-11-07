"""The commit the running editor was built from, to tell builds of the same version apart: About
shows it in brackets after the version.

A build stores it in the package (:data:`COMMIT_FILE`: written by the wheel's build hook,
``apps/editor-gui/hatch_build.py``, and by the frozen app's spec); an editor run from a checkout
asks git."""

from __future__ import annotations

import re
import subprocess
import sys
from functools import cache
from pathlib import Path

from editor_gui import __version__

COMMIT_FILE = "_commit.txt"

_HASH = re.compile(r"[0-9a-f]{4,40}")


@cache
def commit() -> str:
    """The short hash of the commit the editor was built from, "" when unknown."""
    return read_commit(Path(__file__).parent)


def read_commit(package: Path) -> str:
    """The commit stored in ``package``, else that of the checkout it lies in, else ""."""
    try:
        stored = (package / COMMIT_FILE).read_text(encoding="ascii").strip()
    except OSError:
        return git_commit(package)
    return stored if _HASH.fullmatch(stored) else ""


def git_commit(folder: Path) -> str:
    """The short hash of the checkout ``folder`` lies in, "" outside one or without git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            # No console window flashing up for a windowed app on Windows.
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    found = result.stdout.strip()
    return found if result.returncode == 0 and _HASH.fullmatch(found) else ""


def version_text() -> str:
    """The version, with the commit in brackets when known: ``0.10.0 (1a2b3c4d)``."""
    found = commit()
    return f"{__version__} ({found})" if found else __version__

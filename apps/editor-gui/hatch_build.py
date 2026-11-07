"""Hatch build hook: stores the commit being built in the wheel (``editor_gui/_commit.txt``), so an
installed editor shows it in About next to the version (``editor_gui.build_info``). Without git, or
outside a checkout, the wheel is built without it."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CommitHook(BuildHookInterface):  # type: ignore[type-arg]
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # An editable install runs from the sources, which ask git themselves.
        if version == "editable":
            return
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return
        found = result.stdout.strip()
        if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{4,40}", found):
            return
        self._folder = Path(tempfile.mkdtemp(prefix="xknx-editor-commit-"))
        stamp = self._folder / "_commit.txt"
        stamp.write_text(found, encoding="ascii")
        build_data["force_include"][str(stamp)] = "editor_gui/_commit.txt"

    def finalize(
        self, version: str, build_data: dict[str, Any], artifact_path: str
    ) -> None:
        folder: Path | None = getattr(self, "_folder", None)
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)

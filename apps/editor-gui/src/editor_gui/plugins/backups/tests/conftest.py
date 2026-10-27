"""The backup tests keep their settings and backups in the test's folder, never the user's."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui import settings as app_settings
from editor_gui.plugins.backups import settings as backup_settings


@pytest.fixture(autouse=True)
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "config"
    folder.mkdir()
    monkeypatch.setattr(app_settings, "config_dir", lambda: folder)
    monkeypatch.setattr(backup_settings, "config_dir", lambda: folder)
    return folder

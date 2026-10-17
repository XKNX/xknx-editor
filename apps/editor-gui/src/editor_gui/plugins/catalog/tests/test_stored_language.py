"""The download language earlier versions stored is removed: the editor's language decides it."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui import settings
from editor_gui.plugins.catalog import service


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path)


def test_a_file_with_only_the_language_goes(tmp_path: Path) -> None:
    settings.save_settings("catalog", {"language": "de-DE"})
    service._forget_stored_language()  # pyright: ignore[reportPrivateUsage]
    assert not (tmp_path / "catalog.json").exists()


def test_other_settings_stay() -> None:
    settings.save_settings("catalog", {"language": "de-DE", "other": 1})
    service._forget_stored_language()  # pyright: ignore[reportPrivateUsage]
    assert settings.load_settings("catalog") == {"other": 1}

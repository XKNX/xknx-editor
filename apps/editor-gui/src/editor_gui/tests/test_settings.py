"""Tests for the JSON settings store (used by recent files, connection, MCP)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from editor_gui import settings


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path)


def test_round_trip() -> None:
    settings.save_settings("app", {"recent_files": ["/a.xknx", "/b.xknx"]})
    assert settings.load_settings("app")["recent_files"] == ["/a.xknx", "/b.xknx"]


def test_missing_returns_empty() -> None:
    assert settings.load_settings("does-not-exist") == {}


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert settings.load_settings("broken") == {}


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_written_owner_only(tmp_path: Path) -> None:
    """mcp.json holds the MCP bearer token; the default umask would leave it world-readable."""
    settings.save_settings("mcp", {"token": "s3cret"})
    assert (tmp_path / "mcp.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_tightens_a_previously_world_readable_file(tmp_path: Path) -> None:
    """Upgrades must not leave an existing token readable by other local users."""
    stale = tmp_path / "mcp.json"
    stale.write_text("{}", encoding="utf-8")
    stale.chmod(0o644)
    settings.save_settings("mcp", {"token": "s3cret"})
    assert stale.stat().st_mode & 0o777 == 0o600
    assert settings.load_settings("mcp")["token"] == "s3cret"


def test_non_dict_json_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert settings.load_settings("list") == {}

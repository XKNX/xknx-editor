"""About shows the commit the editor was built from next to the version: stored in the package by
a build, else asked from the checkout the sources lie in."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from editor_gui import __version__, build_info


def test_a_stored_commit_wins(tmp_path: Path) -> None:
    (tmp_path / build_info.COMMIT_FILE).write_text("1a2b3c4d\n", encoding="ascii")
    assert build_info.read_commit(tmp_path) == "1a2b3c4d"


def test_a_stored_file_without_a_hash_shows_none(tmp_path: Path) -> None:
    (tmp_path / build_info.COMMIT_FILE).write_text("not a commit", encoding="ascii")
    assert build_info.read_commit(tmp_path) == ""


def test_the_sources_ask_their_checkout() -> None:
    package = Path(build_info.__file__).parent
    expected = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=package,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not expected:
        pytest.skip("not run from a git checkout")
    assert build_info.read_commit(package) == expected


def test_outside_a_checkout_there_is_none(tmp_path: Path) -> None:
    assert build_info.git_commit(tmp_path) == ""


def test_the_version_text_shows_the_commit_in_brackets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build_info, "commit", lambda: "1a2b3c4d")
    assert build_info.version_text() == f"{__version__} (1a2b3c4d)"
    monkeypatch.setattr(build_info, "commit", lambda: "")
    assert build_info.version_text() == __version__

"""Comparing with a password-protected project asks for its password."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.project_snapshot import read_project_snapshot
from editor_gui.plugins.project.ui.project_diff import (
    DiffOutcome,
    DiffResult,
    ProjectSnapshot,
    diff_projects,
)
from xknxeditor.proj import ProjectService, export_knxproj


def _project(path: Path, password: str | None, *, secure: bool = False) -> Path:
    svc = ProjectService()
    pid = svc.create(path, "P-1234", name="Other")
    svc.create_group_address(pid, 0, 0x0801, "GA One")
    svc.close(pid)
    if secure:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "UPDATE group_addresses SET data_secure = 1, "
                "security_key = 'AAECAwQFBgcICQoLDA0ODw=='"
            )
    if password:
        pid = svc.open(path)
        svc.set_project_password(pid, password)
        svc.close(pid)
    return path


def _diff(path: Path, password: str | None) -> DiffOutcome:
    """Run the plugin's comparison on a stand-in that has only what it uses."""
    plugin = cast(Any, SimpleNamespace())

    def compare(other: str, pw: str | None) -> DiffResult:
        return ProjectPlugin._compare_with_file(plugin, other, pw)  # pyright: ignore[reportPrivateUsage]

    def compare_with(svc: ProjectService, pid: str, name: str) -> DiffResult:
        # The open project is empty here: everything of the other one is added.
        current = ProjectSnapshot("current", (), {})
        return diff_projects(current, read_project_snapshot(svc, pid, name))

    def warning(*args: object, **kwargs: object) -> None:
        pass

    plugin._compare_with_file = compare
    plugin._api = SimpleNamespace(
        log=SimpleNamespace(warning=warning),
        project=SimpleNamespace(compare_with=compare_with),
    )
    return ProjectPlugin.tools_diff_projects(plugin, str(path), password)  # pyright: ignore[reportPrivateUsage]


def _protected_archive(tmp_path: Path) -> Path:
    out = tmp_path / "other.knxproj"
    export_knxproj(_project(tmp_path / "p.xknx", "geheim"), out, password="geheim")
    return out


@pytest.mark.parametrize("kind", ["xknx", "knxproj"])
def test_a_protected_project_needs_its_password(tmp_path: Path, kind: str) -> None:
    path = (
        _project(tmp_path / "other.xknx", "geheim")
        if kind == "xknx"
        else _protected_archive(tmp_path)
    )
    first = _diff(path, None)
    assert (first.result, first.error, first.needs_password) == (None, "", True)
    wrong = _diff(path, "falsch")
    assert wrong.needs_password and wrong.error
    right = _diff(path, "geheim")
    assert right.result is not None and not right.needs_password
    assert [e.key for e in right.result.group_addresses] == ["1/0/1"]


def test_an_unprotected_archive_with_secure_data_needs_no_password(
    tmp_path: Path,
) -> None:
    out = tmp_path / "other.knxproj"
    export_knxproj(_project(tmp_path / "p.xknx", None, secure=True), out)
    outcome = _diff(out, None)
    assert outcome.result is not None and not outcome.needs_password

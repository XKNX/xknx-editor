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
    # The open project is an empty one: everything of the other one is added.
    opened = path.with_name("open.xknx")
    if not opened.exists():
        svc = ProjectService()
        svc.close(svc.create(opened, "P-0001", name="Open"))

    def compare(other: str, pw: str | None, job: Any, current: Any) -> DiffResult:
        return ProjectPlugin._compare_with_file(plugin, other, pw, job, current)  # pyright: ignore[reportPrivateUsage]

    def compare_projects(
        mine: ProjectService,
        mine_pid: str,
        svc: ProjectService,
        pid: str,
        name: str,
        **_options: Any,
    ) -> DiffResult:
        return diff_projects(
            read_project_snapshot(mine, mine_pid, "current"),
            read_project_snapshot(svc, pid, name),
        )

    def warning(*args: object, **kwargs: object) -> None:
        pass

    plugin._compare_with_file = compare
    plugin._resolve_on_ui = lambda *_args: None
    plugin._api = SimpleNamespace(
        log=SimpleNamespace(warning=warning),
        main_thread=None,
        project=SimpleNamespace(
            working_path=opened,
            project_password=None,
            compare_projects=compare_projects,
        ),
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


def test_the_worker_looks_applications_up_on_the_ui_thread() -> None:
    """The catalog serves another thread only while the UI thread does not use it, so the
    comparison's worker hands each lookup to the UI thread and waits for it."""
    import threading

    from editor_gui.concurrency import MainThreadExecutor

    executor = MainThreadExecutor()
    threads: list[threading.Thread] = []

    def resolve_application(program: str | None, application: str | None) -> str:
        threads.append(threading.current_thread())
        return f"{program}/{application}"

    plugin = cast(Any, SimpleNamespace())
    plugin._api = SimpleNamespace(
        main_thread=executor,
        project=SimpleNamespace(resolve_application=resolve_application),
    )
    found: list[object] = []
    worker = threading.Thread(
        target=lambda: found.append(
            ProjectPlugin._resolve_on_ui(plugin, "HP-1", "A-1")  # pyright: ignore[reportPrivateUsage]
        )
    )
    worker.start()
    while worker.is_alive():
        executor.drain()
        worker.join(timeout=0.01)
    assert found == ["HP-1/A-1"]
    assert threads == [threading.current_thread()]

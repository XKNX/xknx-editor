"""Running editors find each other: an editor's entry, requests to it, and entries of editors that
ended without removing theirs."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from editor_gui.instances import (
    BROWSER,
    DESKTOP,
    Instance,
    Registry,
    others,
    request,
    with_project,
)


def _registry(folder: Path, received: list[dict[str, Any]]) -> Registry:
    done = threading.Event()

    def on_request(command: dict[str, Any]) -> None:
        received.append(command)
        done.set()

    registry = Registry(BROWSER, "http://localhost:8081/", on_request, folder)
    registry.done = done  # type: ignore[attr-defined]
    return registry


def _as_other(folder: Path, registry: Registry) -> Instance:
    """The registry's entry as another running process's (an editor only lists the others): the
    one that started the tests."""
    other = replace(registry.entry, pid=os.getppid())
    (folder / f"{other.pid}.json").write_text(
        json.dumps(asdict(other)), encoding="utf-8"
    )
    return other


def test_an_editor_is_found_and_answers_requests(tmp_path: Path) -> None:
    received: list[dict[str, Any]] = []
    registry = _registry(tmp_path, received)
    try:
        assert (tmp_path / f"{os.getpid()}.json").exists()
        assert others(tmp_path) == []  # this editor itself is not another one
        other = _as_other(tmp_path, registry)
        assert others(tmp_path) == [other]

        answer = request(other, {"cmd": "activate", "file": "C:/p.xknx"})
        assert answer is not None and answer["ok"]
        assert registry.done.wait(5)  # type: ignore[attr-defined]
        assert received[0]["cmd"] == "activate"
        assert received[0]["file"] == "C:/p.xknx"
    finally:
        registry.close()
    assert not (tmp_path / f"{os.getpid()}.json").exists()


def test_a_request_without_the_token_is_refused(tmp_path: Path) -> None:
    received: list[dict[str, Any]] = []
    registry = _registry(tmp_path, received)
    try:
        forged = replace(registry.entry, token="0" * 32)
        answer = request(forged, {"cmd": "activate"})
        assert answer is not None and not answer["ok"]
        assert received == []
    finally:
        registry.close()


def test_an_editor_that_ended_is_dropped(tmp_path: Path) -> None:
    received: list[dict[str, Any]] = []
    registry = _registry(tmp_path, received)
    other = _as_other(tmp_path, registry)
    registry.close()
    assert others(tmp_path) == []
    assert not (tmp_path / f"{other.pid}.json").exists()


def test_the_open_project_is_shared(tmp_path: Path) -> None:
    project = tmp_path / "Haus.xknx"
    project.write_bytes(b"")
    received: list[dict[str, Any]] = []
    registry = _registry(tmp_path, received)
    try:
        registry.set_project(str(project))
        running = [_as_other(tmp_path, registry)]
        assert running[0].project == str(project)
        # Another spelling of the same file (case, relative) is the same project.
        spelled = os.path.relpath(project).upper() if os.name == "nt" else str(project)
        assert with_project(spelled, running) == running
        assert with_project(str(tmp_path / "Other.xknx"), running) == []
    finally:
        registry.close()


def test_the_entry_of_an_editor_no_longer_running_is_dropped_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[dict[str, Any]] = []
    registry = _registry(tmp_path, received)
    try:
        other = _as_other(tmp_path, registry)
        monkeypatch.setattr("editor_gui.instances.psutil.pid_exists", lambda pid: False)

        def ask(*_: object) -> None:
            raise AssertionError("asked an editor that no longer runs")

        monkeypatch.setattr("editor_gui.instances.request", ask)
        assert others(tmp_path) == []
        assert not (tmp_path / f"{other.pid}.json").exists()
    finally:
        registry.close()


def test_modes() -> None:
    assert {DESKTOP, BROWSER} == {"desktop", "browser"}

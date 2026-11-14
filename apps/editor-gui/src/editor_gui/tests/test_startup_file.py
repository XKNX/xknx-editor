"""The project passed on the command line, e.g. by the .xknx file association."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui import main
from editor_gui.main import (
    PROJECT_PASSWORD_ENV,
    KnxGuiApp,
    read_password_file,
    startup_file,
    startup_password_file,
)


def test_no_arguments_open_nothing():
    assert startup_file(["XKNX-Editor.exe"]) is None


def test_options_are_not_files():
    assert startup_file(["XKNX-Editor.exe", "--profile"]) is None


def test_the_first_file_argument_is_opened_as_an_absolute_path(tmp_path: Path):
    project = tmp_path / "Haus.xknx"
    assert startup_file(
        ["XKNX-Editor.exe", "--profile", str(project), "other.xknx"]
    ) == str(project)


def test_a_relative_path_is_made_absolute():
    assert startup_file(["XKNX-Editor.exe", "Haus.xknx"]) == os.path.abspath(
        "Haus.xknx"
    )


def test_the_password_file_is_an_absolute_path():
    assert startup_password_file(["XKNX-Editor.exe", "Haus.xknx"]) is None
    assert startup_password_file(
        ["XKNX-Editor.exe", "--password-file=pw.txt", "Haus.xknx"]
    ) == os.path.abspath("pw.txt")
    assert startup_password_file(["XKNX-Editor.exe", "--password-file="]) is None
    # The option is not taken for the project file.
    assert startup_file(
        ["XKNX-Editor.exe", "--password-file=pw.txt", "Haus.xknx"]
    ) == os.path.abspath("Haus.xknx")


def test_the_password_is_the_first_line_of_the_file(tmp_path: Path):
    file = tmp_path / "pw.txt"
    file.write_text("geheim\nsecond line\n", encoding="utf-8")
    assert read_password_file(str(file)) == "geheim"
    # As Windows editors write it: a byte order mark and CRLF.
    file.write_bytes("﻿geheim\r\n".encode())
    assert read_password_file(str(file)) == "geheim"
    file.write_text("", encoding="utf-8")
    assert read_password_file(str(file)) is None
    with pytest.raises(OSError):
        read_password_file(str(tmp_path / "missing.txt"))


class _Log:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def info(self, *_: Any, **__: Any) -> None:
        pass

    debug = info

    def error(self, event: str, **_: Any) -> None:
        self.errors.append(event)


def _opening(monkeypatch: pytest.MonkeyPatch) -> tuple[SimpleNamespace, list[Any]]:
    opened: list[Any] = []
    monkeypatch.setattr(main, "others", list)
    stub = SimpleNamespace(
        _log=_Log(),
        _project_service=SimpleNamespace(can_open=lambda _path: True),
        _open_xknx=lambda path, _report, password, *, consented: opened.append(
            (path, password, consented)
        ),
    )
    return stub, opened


def test_a_project_opens_with_the_password_of_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    file = tmp_path / "pw.txt"
    file.write_text("geheim\n", encoding="utf-8")
    stub, opened = _opening(monkeypatch)
    # The file wins over a password from the environment.
    KnxGuiApp._do_open_project(  # type: ignore[arg-type]
        stub, "Haus.xknx", password_file=str(file), password="other"
    )
    # Not asked for the password; a network location still asks before a local copy.
    assert opened == [("Haus.xknx", "geheim", False)]


def test_an_unreadable_password_file_is_reported_and_the_password_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    stub, opened = _opening(monkeypatch)
    KnxGuiApp._do_open_project(  # type: ignore[arg-type]
        stub, "Haus.xknx", password_file=str(tmp_path / "missing.txt")
    )
    assert stub._log.errors == ["cannot read the project password file"]
    assert opened == [("Haus.xknx", None, False)]


def test_the_start_opens_with_the_password_of_the_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv(PROJECT_PASSWORD_ENV, "geheim")
    monkeypatch.setattr(main, "load_settings", lambda _name: {})
    stub = SimpleNamespace(
        _connection_plugin=SimpleNamespace(autostart=lambda: None),
        no_connect=False,
        startup_file="Haus.xknx",
        startup_password_file=None,
        _do_open_project=lambda path, **options: calls.append(
            {"path": path, **options}
        ),
    )
    KnxGuiApp._start_session(stub)  # type: ignore[arg-type]
    assert calls == [{"path": "Haus.xknx", "password_file": None, "password": "geheim"}]


def test_a_running_editor_gets_the_password_file_not_the_password(
    monkeypatch: pytest.MonkeyPatch,
):
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(main, "allow_foreground", lambda _pid: None)
    monkeypatch.setattr(
        main, "request", lambda _other, command: sent.append(command) or {}
    )
    other = SimpleNamespace(pid=1, mode="desktop", url="")
    assert KnxGuiApp._switch_to(  # type: ignore[arg-type]
        SimpleNamespace(), other, "Haus.xknx", "pw.txt"
    )
    assert sent == [{"cmd": "activate", "file": "Haus.xknx", "password_file": "pw.txt"}]
    # It opens the project with it.
    opened: list[Any] = []
    monkeypatch.setattr(main.web, "active_hub", None)
    monkeypatch.setattr(main, "bring_to_front", lambda: None)
    import collections

    stub = SimpleNamespace(
        _registry=None,
        _editor_requests=collections.deque(sent),
        _project_service=SimpleNamespace(path=None),
        _bg_thread=None,
        _import_thread=None,
        _do_open_project=lambda path, **options: opened.append((path, options)),
    )
    KnxGuiApp._handle_editor_requests(stub)  # type: ignore[arg-type]
    assert opened == [("Haus.xknx", {"password_file": "pw.txt"})]


def _reopening(
    monkeypatch: pytest.MonkeyPatch, settings: dict[str, Any]
) -> tuple[SimpleNamespace, list[dict[str, Any]]]:
    """A start without a project given, with ``settings`` as the app settings."""
    calls: list[dict[str, Any]] = []
    monkeypatch.delenv(PROJECT_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(main, "load_settings", lambda _name: settings)
    stub = SimpleNamespace(
        _connection_plugin=SimpleNamespace(autostart=lambda: None),
        _log=_Log(),
        no_connect=False,
        startup_file=None,
        startup_password_file=None,
        _do_open_project=lambda path, **options: calls.append(
            {"path": path, **options}
        ),
    )
    stub._open_last_project_enabled = lambda: KnxGuiApp._open_last_project_enabled(
        stub  # type: ignore[arg-type]
    )
    stub._recent_files = lambda: KnxGuiApp._recent_files(stub)  # type: ignore[arg-type]
    stub._last_project_to_reopen = lambda: KnxGuiApp._last_project_to_reopen(
        stub  # type: ignore[arg-type]
    )
    return stub, calls


def test_the_start_shows_the_welcome_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "Haus.xknx"
    project.write_bytes(b"")
    stub, calls = _reopening(monkeypatch, {"recent_files": [str(project)]})
    KnxGuiApp._start_session(stub)  # type: ignore[arg-type]
    assert calls == []


def test_the_start_opens_the_last_project_when_asked_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    last, older = tmp_path / "Haus.xknx", tmp_path / "Büro.xknx"
    last.write_bytes(b"")
    older.write_bytes(b"")
    stub, calls = _reopening(
        monkeypatch,
        {"open_last_project": True, "recent_files": [str(last), str(older)]},
    )
    stub.startup_password_file = "pw.txt"
    KnxGuiApp._start_session(stub)  # type: ignore[arg-type]
    # Opened like a project given at the start: with --password-file, if given.
    assert calls == [{"path": str(last), "password_file": "pw.txt", "password": None}]


def test_a_project_given_at_the_start_wins_over_the_last_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    last = tmp_path / "Haus.xknx"
    last.write_bytes(b"")
    stub, calls = _reopening(
        monkeypatch, {"open_last_project": True, "recent_files": [str(last)]}
    )
    stub.startup_file = "Other.xknx"
    KnxGuiApp._start_session(stub)  # type: ignore[arg-type]
    assert [call["path"] for call in calls] == ["Other.xknx"]


def test_a_missing_last_project_is_not_opened_or_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings: dict[str, Any] = {
        "open_last_project": True,
        "recent_files": [str(tmp_path / "gone.xknx")],
    }
    stub, calls = _reopening(monkeypatch, settings)
    KnxGuiApp._start_session(stub)  # type: ignore[arg-type]
    assert calls == []
    assert stub._log.errors == []
    # It stays in Open Recent until it is picked there.
    assert settings["recent_files"] == [str(tmp_path / "gone.xknx")]
    # Nothing recent: nothing to open.
    stub, calls = _reopening(monkeypatch, {"open_last_project": True})
    KnxGuiApp._start_session(stub)  # type: ignore[arg-type]
    assert calls == []


def test_the_setting_is_kept_in_the_app_settings(monkeypatch: pytest.MonkeyPatch):
    stored: dict[str, dict[str, Any]] = {"app": {"recent_files": ["Haus.xknx"]}}
    monkeypatch.setattr(main, "load_settings", lambda name: dict(stored.get(name, {})))
    monkeypatch.setattr(
        main, "save_settings", lambda name, data: stored.__setitem__(name, data)
    )
    stub = SimpleNamespace()
    assert not KnxGuiApp._open_last_project_enabled(stub)  # type: ignore[arg-type]
    KnxGuiApp._set_open_last_project_enabled(stub, True)  # type: ignore[arg-type]
    assert KnxGuiApp._open_last_project_enabled(stub)  # type: ignore[arg-type]
    # The other app settings stay.
    assert stored["app"] == {"recent_files": ["Haus.xknx"], "open_last_project": True}

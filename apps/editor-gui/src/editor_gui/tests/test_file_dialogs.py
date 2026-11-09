from __future__ import annotations

import shutil
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui import web
from editor_gui.main import (
    KnxGuiApp,
    _find_linux_file_dialog_helper,
    _restore_subprocess_library_path,
)
from editor_gui.strings import S


class _StubLog:
    def __init__(self) -> None:
        self.debugs: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[tuple[str, dict[str, Any]]] = []
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **kwargs: Any) -> None:
        self.debugs.append((event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self.errors.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, kwargs))


def test_linux_file_dialog_helper_uses_first_available_on_path(monkeypatch) -> None:
    def fake_which(command: str, path: str | None = None) -> str | None:
        assert path == "/mock/bin"
        return f"{path}/{command}" if command == "kdialog" else None

    monkeypatch.setattr(shutil, "which", fake_which)

    assert _find_linux_file_dialog_helper("/mock/bin") == "kdialog"


def test_linux_file_dialog_helper_returns_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _command, path=None: None)

    assert _find_linux_file_dialog_helper("/mock/bin") is None


@pytest.mark.parametrize(
    ("helper", "which", "missing", "command"),
    [
        (
            None,
            "/usr/bin/which",
            ["zenity / kdialog / matedialog / qarma"],
            "sudo apt install zenity",
        ),
        ("zenity", None, ["which"], "sudo apt install gnu-which"),
        (
            None,
            None,
            ["zenity / kdialog / matedialog / qarma", "which"],
            "sudo apt install zenity gnu-which",
        ),
    ],
)
def test_missing_linux_programs_are_reported_on_each_attempt(
    monkeypatch, helper, which, missing, command
) -> None:
    log = _StubLog()
    stub = SimpleNamespace(
        _file_dialogs_available=None, _file_dialog_error_requested=False, _log=log
    )
    checks = 0

    def find_helper() -> str | None:
        nonlocal checks
        checks += 1
        return helper

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("editor_gui.main._find_linux_file_dialog_helper", find_helper)
    monkeypatch.setattr(shutil, "which", lambda _command: which)

    # Startup checks do not open a window; a file action does, even after it was dismissed.
    assert not KnxGuiApp._can_launch_file_dialog(stub, show_error=False)  # type: ignore[arg-type]
    assert not stub._file_dialog_error_requested
    for _ in range(2):
        assert not KnxGuiApp._can_launch_file_dialog(stub)  # type: ignore[arg-type]
        assert stub._file_dialog_error_requested
        stub._file_dialog_error_requested = False
    assert checks == 1
    assert stub._file_dialog_missing == missing
    assert stub._file_dialog_install_command == command
    assert log.errors == [
        (
            "file dialog unavailable",
            {
                "missing": ", ".join(missing),
                "error": "Install the missing programs, then restart the app.",
            },
        )
    ]


def test_linux_dialog_is_available_with_helper_and_which(monkeypatch) -> None:
    stub = SimpleNamespace(
        _file_dialogs_available=None,
        _file_dialog_error_requested=False,
        _log=_StubLog(),
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "editor_gui.main._find_linux_file_dialog_helper", lambda: "kdialog"
    )
    monkeypatch.setattr(shutil, "which", lambda _command: "/usr/bin/which")

    assert KnxGuiApp._can_launch_file_dialog(stub)  # type: ignore[arg-type]
    assert not stub._file_dialog_error_requested
    assert not stub._log.errors


def test_browser_view_needs_no_linux_file_dialog_helper(monkeypatch) -> None:
    # Regression: in the Docker image (Linux, no zenity/kdialog) every file action stopped here
    # although the browser view draws its own file dialogs.
    log = _StubLog()
    stub = SimpleNamespace(_file_dialogs_available=None, _log=log)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("editor_gui.main._find_linux_file_dialog_helper", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda _command: None)
    monkeypatch.setattr(web, "active_hub", SimpleNamespace())

    assert KnxGuiApp._can_launch_file_dialog(stub)  # type: ignore[arg-type]
    assert log.errors == []


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_other_desktops_need_no_linux_programs(monkeypatch, platform) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(shutil, "which", lambda _command: None)
    assert KnxGuiApp._can_launch_file_dialog(SimpleNamespace())  # type: ignore[arg-type]


def test_open_project_reports_missing_programs_without_launching_dialog(
    monkeypatch,
) -> None:
    stub = SimpleNamespace(
        _file_dialogs_available=False, _file_dialog_error_requested=False
    )
    stub._can_launch_file_dialog = lambda: KnxGuiApp._can_launch_file_dialog(stub)  # type: ignore[arg-type]
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "editor_gui.main.file_dialogs.open_file",
        lambda *_: pytest.fail("dialog launched"),
    )

    KnxGuiApp._open_project(stub)  # type: ignore[arg-type]

    assert stub._file_dialog_error_requested


def test_error_window_waits_for_other_dialog_and_can_be_copied_and_closed(
    monkeypatch,
) -> None:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    command = "sudo apt install zenity gnu-which"
    stub = SimpleNamespace(
        _file_dialog_error_requested=True,
        _file_dialog_missing=["zenity / kdialog / matedialog / qarma", "which"],
        _file_dialog_install_command=command,
    )
    title = f"{S.FILE_DIALOG_UNAVAILABLE_TITLE}###file-dialog-unavailable"
    copied: list[str] = []
    monkeypatch.setattr(imgui, "set_clipboard_text", copied.append)

    def frame() -> bool:
        imgui.new_frame()
        KnxGuiApp._render_file_dialog_error_modal(stub)  # type: ignore[arg-type]
        opened = imgui.is_popup_open(title)
        imgui.end_frame()
        return opened

    try:
        imgui.new_frame()
        imgui.open_popup("Other dialog")
        KnxGuiApp._render_file_dialog_error_modal(stub)  # type: ignore[arg-type]
        assert stub._file_dialog_error_requested
        assert not imgui.is_popup_open(title)
        assert imgui.begin_popup("Other dialog")
        imgui.close_current_popup()
        imgui.end_popup()
        imgui.end_frame()

        assert frame()
        assert not stub._file_dialog_error_requested
        assert frame()
        monkeypatch.setattr("editor_gui.main.dialog_buttons", lambda *_: 0)
        assert frame()
        assert copied == [command]
        monkeypatch.setattr("editor_gui.main.dialog_buttons", lambda *_: 1)
        assert not frame()
        assert not frame()
    finally:
        imgui.destroy_context(ctx)


def test_run_bg_logs_when_a_worker_is_busy() -> None:
    log = _StubLog()
    stub = SimpleNamespace(
        _bg_thread=threading.current_thread(),
        _import_thread=None,
        _log=log,
    )

    KnxGuiApp._run_bg(stub, "Open project", lambda: None)  # type: ignore[arg-type]

    assert log.warnings == [
        (
            "background task not started; worker busy",
            {
                "task": "Open project",
                "background_thread": "MainThread",
                "background_alive": True,
            },
        )
    ]


def test_restore_library_path_is_noop_off_frozen(monkeypatch) -> None:
    import os

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(os.environ, "LD_LIBRARY_PATH", "/bundle/lib")

    _restore_subprocess_library_path()

    assert os.environ.get("LD_LIBRARY_PATH") == "/bundle/lib"


def test_restore_library_path_restores_original_on_frozen_linux(monkeypatch) -> None:
    import os

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(os.environ, "LD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.setitem(os.environ, "LD_LIBRARY_PATH_ORIG", "/usr/lib")

    _restore_subprocess_library_path()

    assert os.environ.get("LD_LIBRARY_PATH") == "/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in os.environ


def test_restore_library_path_drops_bundle_when_no_original(monkeypatch) -> None:
    import os

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(os.environ, "LD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    _restore_subprocess_library_path()

    assert "LD_LIBRARY_PATH" not in os.environ

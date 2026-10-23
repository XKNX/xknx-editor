from __future__ import annotations

import shutil
import sys
import threading
from types import SimpleNamespace
from typing import Any

from editor_gui import web
from editor_gui.main import (
    KnxGuiApp,
    _find_linux_file_dialog_helper,
    _restore_subprocess_library_path,
)


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


def test_missing_linux_file_dialog_helper_is_reported_once(monkeypatch) -> None:
    log = _StubLog()
    stub = SimpleNamespace(_file_dialogs_available=None, _log=log)
    checks = 0

    def missing_helper() -> None:
        nonlocal checks
        checks += 1
        return None

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "editor_gui.main._find_linux_file_dialog_helper", missing_helper
    )

    assert not KnxGuiApp._can_launch_file_dialog(stub)  # type: ignore[arg-type]
    assert not KnxGuiApp._can_launch_file_dialog(stub)  # type: ignore[arg-type]
    assert checks == 1
    assert log.errors == [
        (
            "file dialog unavailable",
            {
                "error": "Install a file-dialog helper such as zenity or kdialog, then restart the app."
            },
        )
    ]


def test_browser_view_needs_no_linux_file_dialog_helper(monkeypatch) -> None:
    # Regression: in the Docker image (Linux, no zenity/kdialog) every file action stopped here
    # although the browser view draws its own file dialogs.
    log = _StubLog()
    stub = SimpleNamespace(_file_dialogs_available=None, _log=log)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("editor_gui.main._find_linux_file_dialog_helper", lambda: None)
    monkeypatch.setattr(web, "active_hub", SimpleNamespace())

    assert KnxGuiApp._can_launch_file_dialog(stub)  # type: ignore[arg-type]
    assert log.errors == []


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

"""A placeholder signing key defers the .knxproj export behind a one-time warning."""

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from editor_gui.main import KnxGuiApp


def _app(tmp_path: Path, *, acknowledged: bool) -> SimpleNamespace:
    service = SimpleNamespace(
        working_path=tmp_path / "project.xknx",
        program_refs=Mock(return_value={}),
        module_arguments=lambda: {},
        group_object_trees=lambda: {},
        project_password=None,
    )
    return SimpleNamespace(
        _project_service=service,
        _catalog_service=SimpleNamespace(io_lock=threading.RLock()),
        _log=Mock(),
        _show_export_failure=Mock(),
        _myknx_thread=None,
        _signing_warning_acknowledged=acknowledged,
        _signing_warning_requested=False,
        _signing_warning_pending=None,
    )


def test_placeholder_key_defers_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("editor_gui.main.signing_key_is_placeholder", lambda: True)
    app = _app(tmp_path, acknowledged=False)
    dest = str(tmp_path / "export.knxproj")

    KnxGuiApp._do_export_knxproj(app, dest)  # type: ignore[arg-type]

    # Deferred before any export work: a warning is requested and the call is remembered.
    assert app._signing_warning_requested is True
    assert app._signing_warning_pending == (dest, None)
    app._project_service.program_refs.assert_not_called()


def test_acknowledged_warning_lets_export_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("editor_gui.main.signing_key_is_placeholder", lambda: True)
    app = _app(tmp_path, acknowledged=True)
    # Stop right after the gate so the worker setup is not exercised here.
    app._project_service.program_refs = Mock(side_effect=RuntimeError("past gate"))

    dest = str(tmp_path / "export.knxproj")
    KnxGuiApp._do_export_knxproj(app, dest)  # type: ignore[arg-type]
    app._project_service.program_refs.assert_called_once()
    app._show_export_failure.assert_called_once_with(dest, "past gate")
    assert app._signing_warning_requested is False


def test_valid_key_does_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("editor_gui.main.signing_key_is_placeholder", lambda: False)
    app = _app(tmp_path, acknowledged=False)
    app._project_service.program_refs = Mock(side_effect=RuntimeError("past gate"))

    dest = str(tmp_path / "export.knxproj")
    KnxGuiApp._do_export_knxproj(app, dest)  # type: ignore[arg-type]
    app._project_service.program_refs.assert_called_once()
    app._show_export_failure.assert_called_once_with(dest, "past gate")
    assert app._signing_warning_requested is False

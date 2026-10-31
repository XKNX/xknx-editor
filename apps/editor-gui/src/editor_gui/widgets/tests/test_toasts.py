"""Toasts: a confirmation for a moment, a refusal in an orange box for longer, and a click on one
closes it at once; refusals reach the app through ``PluginAPI.tell_refused``, warnings and errors
of the log as toasts unless the user sees them elsewhere."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui
from xknx.exceptions import ManagementConnectionTimeout

from editor_gui.plugins.base import Logger
from editor_gui.plugins.base.api import PluginAPI
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.logger.service import LogService
from editor_gui.widgets import toasts as toasts_module
from editor_gui.widgets.toasts import Toasts, log_toast
from xknxeditor.download.errors import PartialDownloadError


def test_warnings_and_errors_of_the_log_get_a_toast() -> None:
    assert log_toast("error", "connection", "Restart failed", {"error": "timeout"}) == (
        "[connection] Restart failed: timeout"
    )
    assert log_toast("warning", "project", "Nothing to do", {}) == (
        "[project] Nothing to do"
    )
    assert log_toast("info", "project", "Programming complete", {}) is None
    # Shown in a dialog, or in the operations history: no toast.
    assert log_toast("error", "app", "export failed", {"shown": "dialog"}) is None
    assert log_toast("error", "connection", "x", {"shown": "operations"}) is None


def _failed(error: BaseException) -> Future[Any]:
    future: Future[Any] = Future()
    future.set_exception(error)
    return future


def test_a_failed_operation_raises_no_toast() -> None:
    """Programming, its test, a device info readout, a restart, a master reset and a DALI run
    end in the operations history, which shows the failure; their log records raise no
    toast."""
    logs = LogService()
    service = cast(
        Any,
        SimpleNamespace(
            _log=Logger(logs, "connection"),
            _clear_busy=lambda: None,
            _set_program_notice=lambda ok: None,
        ),
    )
    timeout = ManagementConnectionTimeout("No ACK received for repeated telegram.")
    partial = PartialDownloadError("failed after writing")
    partial.__cause__ = timeout
    for error in (timeout, partial):
        ConnectionService._log_program_result(service, _failed(error))  # pyright: ignore[reportPrivateUsage]
    ConnectionService._log_evaluate_result(service, _failed(timeout), "1.1.1")  # pyright: ignore[reportPrivateUsage]
    ConnectionService._end_device_info(service, _failed(timeout), "1.1.1")  # pyright: ignore[reportPrivateUsage]
    ConnectionService._log_restart_result(service, _failed(timeout), "1.1.1")  # pyright: ignore[reportPrivateUsage]
    ConnectionService._log_reset_result(service, _failed(timeout), "1.1.1")  # pyright: ignore[reportPrivateUsage]
    ConnectionService._log_dali_result(service, _failed(timeout), "1.1.1")  # pyright: ignore[reportPrivateUsage]
    records = [r for r in logs.get_records() if r.plugin == "connection"]
    assert len(records) == 11
    assert all(r.level == "error" for r in records)
    assert [log_toast(r.level, r.plugin, r.event, r.payload) for r in records] == [
        None
    ] * 11


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frame(toasts: Toasts) -> None:
    imgui.new_frame()
    toasts.render()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_toasts_expire_and_a_click_closes_one(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    toasts = Toasts(clock=lambda: now[0])
    toasts.add("Copied", "success", 2.0)
    toasts.add("The limit to add was reached.", "warning", 8.0)
    _frame(toasts)
    assert [t.text for t in toasts.items] == [
        "Copied",
        "The limit to add was reached.",
    ]
    # The confirmation is gone after its two seconds, the refusal still shows.
    now[0] = 103.0
    _frame(toasts)
    assert [t.level for t in toasts.items] == ["warning"]
    # A click on it closes it before its time.
    monkeypatch.setattr(imgui, "is_item_clicked", lambda *_a: True)
    _frame(toasts)
    assert toasts.items == []


def test_refusals_and_failures_stand_out() -> None:
    assert toasts_module._boxed("success") is None  # pyright: ignore[reportPrivateUsage]
    warning = toasts_module._boxed("warning")  # pyright: ignore[reportPrivateUsage]
    error = toasts_module._boxed("error")  # pyright: ignore[reportPrivateUsage]
    assert warning is not None and error is not None and warning != error


def _api(**kwargs: Any) -> PluginAPI:
    none = cast(Any, None)
    return PluginAPI(1, none, none, none, none, **kwargs)


def test_tell_refused_uses_warn_else_notify() -> None:
    warned: list[str] = []
    notified: list[str] = []
    _api(notify=notified.append, warn=warned.append).tell_refused("No room")
    assert warned == ["No room"] and notified == []
    _api(notify=notified.append).tell_refused("No room")
    assert notified == ["No room"]
    # Nothing refused: nothing told.
    _api(warn=warned.append).tell_refused(None)
    _api(warn=warned.append).tell_refused("")
    assert warned == ["No room"]

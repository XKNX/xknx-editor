"""The stdlib->LogService bridge that surfaces the ``xknxeditor.*`` packages' logging (programming,
download, myknx, export, import) in the in-app Logger panel."""

from __future__ import annotations

import io
import logging
import sys

import pytest

from editor_gui.log_file import session_log, set_session_log
from editor_gui.plugins.logger.service import LogService


def test_bridge_forwards_package_logs_with_level_and_plugin():
    svc = LogService()
    logging.getLogger("xknxeditor.download.procedure").debug(
        "execute load control: %s", "LdCtrlWriteMem"
    )
    recs = svc.get_records()
    hit = next((r for r in recs if r.plugin == "download"), None)
    assert hit is not None
    assert hit.level == "debug"
    assert hit.event == "execute load control: LdCtrlWriteMem"  # %-args formatted


def test_bridge_surfaces_extra_fields_as_payload():
    svc = LogService()
    logging.getLogger("xknxeditor.proj.core.myknx_cert").info(
        "cert obtained", extra={"product_id": "X-1"}
    )
    hit = next((r for r in svc.get_records() if r.plugin == "project"), None)
    assert hit is not None
    assert hit.payload.get("product_id") == "X-1"


def test_an_exception_shows_on_its_line_and_its_traceback_in_the_copy():
    """Test an error logged with its exception (as xknx's failing background tasks are)
    names it on its line and keeps the traceback for the copied log."""
    from editor_gui.plugins.logger.ui import LogPanel

    svc = LogService()
    try:
        raise TimeoutError("L_DATA_CON timed out")
    except TimeoutError as err:
        logging.getLogger("xknx.core").error("Unexpected error in task", exc_info=err)
    hit = next(r for r in svc.get_records() if r.event == "Unexpected error in task")
    assert hit.payload["exception"] == "TimeoutError: L_DATA_CON timed out"
    assert hit.traceback.startswith("Traceback (most recent call last):")
    assert "test_bridge.py" in hit.traceback
    panel = LogPanel(lambda: [hit], lambda: 1, lambda: None)
    text = panel._record_as_text(hit)
    assert text.splitlines()[0].endswith("exception=TimeoutError: L_DATA_CON timed out")
    assert hit.traceback in text
    logging.getLogger("xknx.core").error("Plain error")
    plain = next(r for r in svc.get_records() if r.event == "Plain error")
    assert not plain.traceback and "exception" not in plain.payload
    assert len(panel._record_as_text(plain).splitlines()) == 1


def test_bridge_is_idempotent_across_instances():
    # Re-creating the service must not stack duplicate handlers on the shared package logger.
    LogService()
    LogService()
    pkg = logging.getLogger("xknxeditor")
    from editor_gui.plugins.logger.service import _StdlibBridgeHandler

    for name in ("xknxeditor", "xknx.management", "xknx.log", "xknx"):
        bridges = [
            h
            for h in logging.getLogger(name).handlers
            if isinstance(h, _StdlibBridgeHandler)
        ]
        assert len(bridges) == 1, name
    assert pkg.propagate is False


def test_xknx_point_to_point_messages_are_logged_from_debug_on():
    # A telegram the device did not acknowledge, and its repetitions, show in a reported log.
    svc = LogService()
    logging.getLogger("xknx.management").info(
        "%s: timeout while waiting for ACK. Resending Telegram (%s/%s).", "1.1.16", 1, 3
    )
    logging.getLogger("xknx.management").debug("Received repeated sequence number 2")
    events = [(r.plugin, r.level, r.event) for r in svc.get_records()]
    assert (
        "xknx",
        "info",
        "1.1.16: timeout while waiting for ACK. Resending Telegram (1/3).",
    ) in events
    assert ("xknx", "debug", "Received repeated sequence number 2") in events


def test_other_xknx_messages_are_logged_from_warnings_on():
    svc = LogService()
    logging.getLogger("xknx.cemi").debug("Outgoing CEMI: every frame")
    logging.getLogger("xknx.log").debug("Sending: every frame")
    logging.getLogger("xknx.raw_socket").debug("Sending to USB: 0113")
    logging.getLogger("xknx.cemi").warning("Could not send CEMI frame")
    events = [r.event for r in svc.get_records() if r.plugin == "xknx"]
    assert events == ["Could not send CEMI frame"]


def test_the_connection_steps_are_logged_from_info_on():
    # e.g. how a KNX USB interface takes reports, or the address it turned out to have
    svc = LogService()
    logging.getLogger("xknx.log").info("Sending them over the control pipe")
    events = [(r.plugin, r.level, r.event) for r in svc.get_records()]
    assert ("xknx", "info", "Sending them over the control pipe") in events


def test_frames_are_logged_with_log_frames() -> None:
    frames = logging.getLogger("xknx.raw_socket")
    level, propagate = frames.level, frames.propagate
    try:
        svc = LogService()
        svc.log_frames()
        frames.debug("Sending to USB: %s", "0113")
        events = [(r.plugin, r.level, r.event) for r in svc.get_records()]
        assert ("xknx", "debug", "Sending to USB: 0113") in events
    finally:
        frames.handlers.clear()
        frames.setLevel(level)
        frames.propagate = propagate


def test_structlog_is_written_to_console_and_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", console)
    disk = io.StringIO()
    previous = session_log()
    set_session_log(disk)
    try:
        svc = LogService()
        svc.info("tee test", "app", value=42)
    finally:
        set_session_log(previous)

    assert "tee test" in console.getvalue()
    assert "tee test" in disk.getvalue()


def test_the_frozen_app_console_is_the_log_file_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk = io.StringIO()
    monkeypatch.setattr(sys, "stdout", disk)
    previous = session_log()
    set_session_log(disk)
    try:
        LogService().info("once", "app")
    finally:
        set_session_log(previous)

    assert disk.getvalue().count("once") == 1


def test_package_logs_reach_the_log_file() -> None:
    disk = io.StringIO()
    previous = session_log()
    set_session_log(disk)
    try:
        LogService()
        logging.getLogger("xknxeditor.proj.core.myknx_cert").error(
            "add product to workset failed: HTTP %s", 422
        )
    finally:
        set_session_log(previous)

    text = disk.getvalue()
    assert "add product to workset failed: HTTP 422" in text
    assert "plugin=project" in text

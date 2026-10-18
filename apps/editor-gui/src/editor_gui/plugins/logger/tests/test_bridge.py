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


def test_bridge_is_idempotent_across_instances():
    # Re-creating the service must not stack duplicate handlers on the shared package logger.
    LogService()
    LogService()
    pkg = logging.getLogger("xknxeditor")
    from editor_gui.plugins.logger.service import _StdlibBridgeHandler

    bridges = [h for h in pkg.handlers if isinstance(h, _StdlibBridgeHandler)]
    assert len(bridges) == 1


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

"""Received cEMI failures carry enough context in the copied and persisted log."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from xknx import XKNX
from xknx.cemi.cemi_handler import CEMIHandler

from editor_gui.cemi_diagnostics import DiagnosticCEMIHandler, cemi_error_details
from editor_gui.log_file import session_log, set_session_log
from editor_gui.plugins.connection.interface import ObservableKNXIPInterfaceThreaded
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.logger.ui import LogPanel

_BODY = "0100101f1233098281"
_VALID = bytes.fromhex("2900bce0101f0801010081")


def _xknx(target: str = "192.0.2.1") -> XKNX:
    xknx = XKNX()
    xknx.cemi_handler = DiagnosticCEMIHandler(xknx, mode="tunneling_tcp", target=target)
    return xknx


@pytest.mark.parametrize("code", ["29", "2e"])
@pytest.mark.parametrize("additional_info", ["00", "0404021234"])
def test_malformed_frame_details_reach_log_copy_and_file(
    code: str,
    additional_info: str,
) -> None:
    raw = bytes.fromhex(code + additional_info + _BODY)
    disk = io.StringIO()
    previous = session_log()
    set_session_log(disk)
    try:
        log = LogService()
        xknx = _xknx()
        xknx.cemi_handler.handle_raw_cemi(raw)
    finally:
        set_session_log(previous)

    (record,) = log.get_records()
    assert record.level == "warning"
    assert "APDU LEN should be 9 but is 1" in record.event
    assert record.payload["cemi_raw"] == raw.hex()
    assert record.payload["cemi_length"] == str(len(raw))
    assert record.payload["cemi_code"] == "0x" + code
    assert record.payload["cemi_service"] == (
        "L_DATA_IND" if code == "29" else "L_DATA_CON"
    )
    assert record.payload["cemi_source"] == "1.0.31"
    assert record.payload["cemi_destination"] == "1.2.51"
    assert record.payload["cemi_control"] == "0100"
    assert record.payload["tpdu_length_expected"] == "10"
    assert record.payload["tpdu_length_received"] == "2"
    assert record.payload["connection_mode"] == "tunneling_tcp"
    assert record.payload["connection_target"] == "192.0.2.1"
    if code == "2e":
        assert record.payload["cemi_confirm_error"] == "True"
    else:
        assert "cemi_confirm_error" not in record.payload
    assert xknx.connection_manager.cemi_count_incoming_error == 1
    assert xknx.telegrams.empty()
    assert cemi_error_details() == {}
    panel = LogPanel(log.get_records, log.records_version, log.clear)
    for output in (disk.getvalue(), panel._record_as_text(record)):
        assert raw.hex() in output
        assert record.payload["cemi_service"] in output
        assert "tpdu_length_expected" in output


@pytest.mark.parametrize(
    "raw", [b"", b"\x29", bytes.fromhex("29080402"), bytes.fromhex("2900b060")]
)
def test_truncated_headers_are_logged_without_inventing_fields(raw: bytes) -> None:
    log = LogService()
    _xknx().cemi_handler.handle_raw_cemi(raw)
    (record,) = log.get_records()
    assert record.payload["cemi_raw"] == raw.hex()
    assert record.payload["cemi_length"] == str(len(raw))
    assert "cemi_source" not in record.payload
    assert "tpdu_length_expected" not in record.payload


def test_valid_traffic_stays_quiet_and_unrelated_errors_have_no_frame() -> None:
    log = LogService()
    xknx = _xknx()
    xknx.cemi_handler.handle_raw_cemi(bytes.fromhex("2900" + _BODY))
    xknx.cemi_handler.handle_raw_cemi(_VALID)
    assert len(log.get_records()) == 1
    assert xknx.telegrams.qsize() == 1
    assert xknx.connection_manager.cemi_count_incoming == 1
    logging.getLogger("xknx.cemi").warning("Unrelated warning")
    assert log.get_records()[-1].payload == {}


def test_valid_confirmation_still_releases_the_waiting_sender() -> None:
    log = LogService()
    xknx = _xknx()
    xknx.cemi_handler.handle_raw_cemi(bytes.fromhex("2e") + _VALID[1:])
    assert xknx.cemi_handler._l_data_confirmation_event.is_set()
    assert not log.get_records()


def test_unsupported_service_is_not_promoted_to_a_warning() -> None:
    log = LogService()
    _xknx().cemi_handler.handle_raw_cemi(b"\xff")
    assert not log.get_records()
    assert cemi_error_details() == {}


def test_context_is_restored_if_a_receive_callback_raises() -> None:
    with (
        patch.object(
            CEMIHandler, "handle_raw_cemi", side_effect=RuntimeError("callback failed")
        ),
        pytest.raises(RuntimeError, match="callback failed"),
    ):
        _xknx().cemi_handler.handle_raw_cemi(_VALID)
    assert cemi_error_details() == {}


async def test_threaded_interface_logs_the_frame_when_main_loop_handles_it() -> None:
    log = LogService()
    xknx = _xknx()
    interface = ObservableKNXIPInterfaceThreaded(xknx)
    interface._main_loop = asyncio.get_running_loop()
    raw = bytes.fromhex("2e00" + _BODY)
    await asyncio.to_thread(interface.cemi_received, raw)
    await asyncio.sleep(0)
    (record,) = log.get_records()
    assert record.payload["cemi_raw"] == raw.hex()
    assert record.payload["cemi_service"] == "L_DATA_CON"
    assert cemi_error_details() == {}


def test_unexpected_parser_error_keeps_frame_and_clears_context() -> None:
    log = LogService()
    with patch(
        "xknx.cemi.cemi_handler.CEMIFrame.from_knx",
        side_effect=ValueError("broken parser"),
    ):
        _xknx().cemi_handler.handle_raw_cemi(_VALID)
    (record,) = log.get_records()
    assert record.level == "error"
    assert record.payload["cemi_raw"] == _VALID.hex()
    assert record.payload["cemi_destination"] == "1/0/1"
    assert cemi_error_details() == {}


async def test_callbacks_scheduled_by_a_frame_do_not_inherit_its_log_context() -> None:
    log = LogService()
    loop = asyncio.get_running_loop()

    def later(_handler: CEMIHandler, _raw: bytes) -> None:
        loop.call_soon(logging.getLogger("xknx.cemi").warning, "Later warning")

    with patch.object(CEMIHandler, "handle_raw_cemi", later):
        _xknx().cemi_handler.handle_raw_cemi(_VALID)
    await asyncio.sleep(0)
    (record,) = log.get_records()
    assert record.payload == {}


def test_concurrent_receive_contexts_stay_with_their_connections() -> None:
    first, second = _xknx("192.0.2.1"), _xknx("192.0.2.2")
    barrier = threading.Barrier(2)

    def check(handler: CEMIHandler, raw: bytes) -> None:
        barrier.wait(timeout=5)
        details = cemi_error_details()
        assert details["connection_target"] == (
            "192.0.2.1" if handler is first.cemi_handler else "192.0.2.2"
        )
        assert details["cemi_raw"] == raw.hex()

    with (
        patch.object(CEMIHandler, "handle_raw_cemi", check),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        tasks = [
            pool.submit(first.cemi_handler.handle_raw_cemi, _VALID),
            pool.submit(
                second.cemi_handler.handle_raw_cemi, bytes.fromhex("2900" + _BODY)
            ),
        ]
        for task in tasks:
            task.result(timeout=5)
    assert cemi_error_details() == {}

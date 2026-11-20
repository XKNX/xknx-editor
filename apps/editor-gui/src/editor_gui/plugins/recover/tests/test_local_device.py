"""While the connection reaches only a USB interface's own device (local device access), the
recovery scans and reads back only that device; anything else is refused before it starts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

from xknx import XKNX

from editor_gui.plugins.connection.service import ConnectionService, LocalDevice
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService


def _recover() -> tuple[RecoverService, ConnectionService]:
    connection = ConnectionService()
    connection.set_logger(MagicMock())
    connection.set_connection(XKNX(), None)
    connection.local_device = LocalDevice("1.0.2")
    service = RecoverService(cast(Any, SimpleNamespace(connection=connection)))
    service.set_logger(MagicMock())
    return service, connection


def test_a_scan_of_other_devices_is_refused() -> None:
    service, connection = _recover()
    service.start_scan("1.0.0", "1.0.20")
    assert service.error is not None and "scan from 1.0.2 to 1.0.2" in service.error
    assert connection.take_refusal() == service.error
    assert connection.busy_operation is None and not service.busy


def test_reading_back_other_devices_is_refused() -> None:
    service, connection = _recover()
    entries = [RecoverEntry("1.0.2", 0x07B0), RecoverEntry("1.0.9", 0x07B0)]
    for entry in entries:
        entry.selected = True
        entry.recovered = cast(Any, object())
    service.entries = entries
    service.chosen = lambda: entries  # type: ignore[method-assign]
    service.start_verify()
    assert service.error is not None and "not 1.0.9" in service.error
    assert connection.busy_operation is None and not service.busy

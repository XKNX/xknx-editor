"""Individual address download and overwrite: the current address entry and the routing."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import xknxeditor.download.commissioning as commissioning
from editor_gui.device_info import (
    parse_serial_text,
    serial_from_project,
    serial_to_project,
)
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.configure import overwrite_current_address
from xknxeditor.download.scope import DownloadScope


def test_current_address_is_normalised() -> None:
    assert overwrite_current_address(" 1.1.20 ", "1.1.5") == ("1.1.20", None)


@pytest.mark.parametrize("text", ["", "1.1", "1/1/20", "16.1.1", "4357", "abc"])
def test_current_address_must_be_a_dotted_address(text: str) -> None:
    assert overwrite_current_address(text, "1.1.5") == (
        None,
        S.OVERWRITE_ADDRESS_INVALID,
    )


def test_current_address_must_differ() -> None:
    assert overwrite_current_address("1.1.5", "1.1.5") == (
        None,
        S.OVERWRITE_ADDRESS_SAME.format(address="1.1.5"),
    )


def _service(monkeypatch: pytest.MonkeyPatch) -> tuple[ConnectionService, list[Any]]:
    calls: list[Any] = []

    async def fake_download(xknx: object, address: str, **kwargs: Any) -> None:
        calls.append((address, kwargs))

    def run_now(coro: Coroutine[Any, Any, Any]) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(asyncio.run(coro))
        return future

    monkeypatch.setattr(commissioning, "download_individual_address", fake_download)
    service = ConnectionService()
    service._xknx = MagicMock()  # pyright: ignore[reportPrivateUsage]
    service.set_logger(MagicMock())
    monkeypatch.setattr(service, "run_async", run_now)
    return service, calls


def _device(*, plugin_required: bool = False) -> Any:
    return SimpleNamespace(
        individual_address="1.1.5", name="Dev", download_plugin_required=plugin_required
    )


def test_address_download_goes_to_the_device_in_programming_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, calls = _service(monkeypatch)
    waiting = MagicMock()

    future = service.program_device(
        _device(plugin_required=True),
        DownloadScope.INDIVIDUAL_ADDRESS,
        current_address="1.1.20",
        waiting=waiting,
    )

    assert future is not None
    ((address, kwargs),) = calls
    assert address == "1.1.5"
    assert kwargs["current_address"] is None
    assert kwargs["waiting"] is waiting
    assert service.busy_operation is None


def test_overwrite_passes_the_current_address(monkeypatch: pytest.MonkeyPatch) -> None:
    service, calls = _service(monkeypatch)

    service.program_device(
        _device(), DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS, current_address="1.1.20"
    )

    ((address, kwargs),) = calls
    assert address == "1.1.5"
    assert kwargs["current_address"] == "1.1.20"


def test_overwrite_needs_the_current_address(monkeypatch: pytest.MonkeyPatch) -> None:
    service, calls = _service(monkeypatch)

    assert (
        service.program_device(_device(), DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS)
        is None
    )
    assert calls == []


def test_an_address_download_cannot_be_tested(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = _service(monkeypatch)

    assert service.evaluate_device(_device(), DownloadScope.INDIVIDUAL_ADDRESS) is None
    assert service.busy_operation is None


_SERIAL = bytes.fromhex("00837AA2039C")


@pytest.mark.parametrize("text", ["0083:7AA2039C", "00837aa2039c", " 0083 7AA2 039C "])
def test_serial_number_entry(text: str) -> None:
    assert parse_serial_text(text) == _SERIAL


@pytest.mark.parametrize("text", ["", "0083:7AA2039", "0083:7AA2039CX", "G0837AA2039C"])
def test_invalid_serial_number_entry(text: str) -> None:
    assert parse_serial_text(text) is None


def test_serial_number_in_the_project_file_form() -> None:
    assert serial_to_project(_SERIAL) == "AIN6ogOc"
    assert serial_from_project("AIN6ogOc") == _SERIAL
    assert serial_from_project("") is None
    assert serial_from_project("not base64!") is None
    assert serial_from_project("AAEC") is None


def test_address_download_by_serial_number(monkeypatch: pytest.MonkeyPatch) -> None:
    service, calls = _service(monkeypatch)

    service.program_device(
        _device(), DownloadScope.INDIVIDUAL_ADDRESS, serial_number=_SERIAL
    )

    ((_, kwargs),) = calls
    assert kwargs["serial_number"] == _SERIAL


def test_overwrite_ignores_a_serial_number(monkeypatch: pytest.MonkeyPatch) -> None:
    service, calls = _service(monkeypatch)

    service.program_device(
        _device(),
        DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS,
        current_address="1.1.20",
        serial_number=_SERIAL,
    )

    ((_, kwargs),) = calls
    assert kwargs["serial_number"] is None


def test_full_download_commissions_by_serial_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import editor_gui.programming as programming

    service, _ = _service(monkeypatch)
    downloads: list[dict[str, Any]] = []

    async def fake_download_device(*args: Any, **kwargs: Any) -> None:
        downloads.append(kwargs)

    monkeypatch.setattr(programming, "download_device", fake_download_device)

    service.program_device(_device(), DownloadScope.FULL, serial_number=_SERIAL)

    (kwargs,) = downloads
    assert kwargs["serial_number"] == _SERIAL

"""Tests for individual address programming."""

from __future__ import annotations

from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import apci
from xknx.telegram.address import IndividualAddress, IndividualAddressableType

import xknxeditor.download.commissioning as commissioning


async def test_program_via_programming_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Programming-mode write issues the address directly and does NOT restart.

    Restarting here would leave the device briefly unreachable and race the
    following download's first connect, so the address is written and the device
    is left running.
    """
    calls: list[tuple[str, object]] = []

    async def fake_read(xknx: XKNX, raise_if_multiple: bool = False) -> list[Any]:
        calls.append(("read", raise_if_multiple))
        return [IndividualAddress("15.15.255")]

    async def fake_check_conn(connection: object) -> bool:
        calls.append(("check", connection))
        return True

    async def fake_sleep(delay: float) -> None:
        calls.append(("sleep", delay))

    monkeypatch.setattr(commissioning, "nm_individual_address_read", fake_read)
    monkeypatch.setattr(
        commissioning, "nm_individual_address_check_conn", fake_check_conn
    )
    monkeypatch.setattr(commissioning.asyncio, "sleep", fake_sleep)

    await commissioning.program_individual_address(
        cast("XKNX", _FakeXKNX(calls)), "1.1.5"
    )

    assert [c[0] for c in calls] == ["read", "broadcast", "sleep", "connect", "check"]
    assert all(c[0] != "restart" for c in calls)
    payload = next(c[1] for c in calls if c[0] == "broadcast")
    assert isinstance(payload, apci.IndividualAddressWrite)
    assert payload.address == IndividualAddress("1.1.5")
    connect_address = next(c[1] for c in calls if c[0] == "connect")
    assert connect_address == IndividualAddress("1.1.5")


async def test_program_requires_programming_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Programming-mode write raises when no device is in programming mode."""

    async def fake_read(xknx: XKNX, raise_if_multiple: bool = False) -> list[Any]:
        return []

    monkeypatch.setattr(commissioning, "nm_individual_address_read", fake_read)

    with pytest.raises(ManagementConnectionError):
        await commissioning.program_individual_address(cast("XKNX", object()), "1.1.5")


async def test_program_via_serial_number(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bytes, object]] = []

    async def fake_serial(
        xknx: XKNX, serial: bytes, individual_address: IndividualAddressableType
    ) -> None:
        calls.append(("serial", serial, individual_address))

    monkeypatch.setattr(
        commissioning, "nm_individual_address_serial_number_write", fake_serial
    )

    serial = bytes.fromhex("00010203 0405".replace(" ", ""))
    await commissioning.program_individual_address(
        cast("XKNX", object()), "1.1.5", serial_number=serial
    )

    assert calls == [("serial", serial, IndividualAddress("1.1.5"))]


class _FakeConnection:
    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeBroadcast:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls

    async def send(self, payload: object) -> None:
        self._calls.append(("broadcast", payload))


class _FakeManagement:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls
        self.broadcast = _FakeBroadcast(calls)

    def connection(self, address: object) -> _FakeConnection:
        self._calls.append(("connect", address))
        return _FakeConnection()


class _FakeXKNX:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.management = _FakeManagement(calls)

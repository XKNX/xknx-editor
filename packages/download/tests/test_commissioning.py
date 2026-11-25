"""Tests for individual address programming."""

from __future__ import annotations

from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import apci
from xknx.telegram.address import IndividualAddress, IndividualAddressableType

import xknxeditor.download.commissioning as commissioning
from xknxeditor.download.errors import ProgrammingModeError, SerialNumberMismatchError


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

    async def fake_serial(xknx: XKNX, address: IndividualAddress) -> bytes:
        calls.append(("serial", address))
        return bytes.fromhex("00FAD6B25D85")

    monkeypatch.setattr(commissioning, "_plain_serial_number", fake_serial)

    # the serial number of the device is read, to be recorded
    assert await commissioning.program_individual_address(
        cast("XKNX", _FakeXKNX(calls)), "1.1.5"
    ) == bytes.fromhex("00FAD6B25D85")

    assert [c[0] for c in calls] == [
        "read",
        "broadcast",
        "sleep",
        "connect",
        "check",
        "serial",
    ]
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


@pytest.mark.usefixtures("no_programming_mode")
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


async def test_program_not_via_a_serial_number_devices_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    async def fake_serial(*args: object) -> None:
        calls.append("serial")

    async def fake_programming_mode(xknx: XKNX, address: IndividualAddress) -> None:
        calls.append(("programming_mode", address))

    monkeypatch.setattr(
        commissioning, "nm_individual_address_serial_number_write", fake_serial
    )
    monkeypatch.setattr(
        commissioning, "_write_address_via_programming_mode", fake_programming_mode
    )

    await commissioning.program_individual_address(
        cast("XKNX", object()), "1.1.5", serial_number=bytes.fromhex("00FAFFFFFFFF")
    )

    assert calls == [("programming_mode", IndividualAddress("1.1.5"))]


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


def _in_programming_mode(
    monkeypatch: pytest.MonkeyPatch, devices: list[IndividualAddress]
) -> None:
    async def fake_read(*args: object, **kwargs: object) -> list[IndividualAddress]:
        return devices

    monkeypatch.setattr(commissioning, "nm_individual_address_read", fake_read)


async def test_a_device_in_programming_mode_gets_the_address_not_the_serial_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the serial number stored for the device is another one's: the device whose programming
    # button was pressed is the one
    calls: list[object] = []

    async def fake_serial(*args: object) -> None:
        calls.append("serial")

    async def fake_programming_mode(xknx: XKNX, address: IndividualAddress) -> None:
        calls.append(("programming_mode", address))

    _in_programming_mode(monkeypatch, [IndividualAddress("15.15.255")])
    monkeypatch.setattr(
        commissioning, "nm_individual_address_serial_number_write", fake_serial
    )
    monkeypatch.setattr(
        commissioning, "_write_address_via_programming_mode", fake_programming_mode
    )
    await commissioning.program_individual_address(
        cast("XKNX", object()), "1.1.5", serial_number=bytes.fromhex("00FAE70AA822")
    )
    assert calls == [("programming_mode", IndividualAddress("1.1.5"))]


async def test_the_serial_number_only_without_a_device_in_programming_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serial = bytes.fromhex("00FAE70AA822")
    _in_programming_mode(monkeypatch, [])
    assert (
        await commissioning._serial_number_unless_programming_mode(  # pyright: ignore[reportPrivateUsage]
            cast("XKNX", object()), serial, None, None
        )
        == serial
    )
    _in_programming_mode(
        monkeypatch, [IndividualAddress("15.15.255"), IndividualAddress("1.1.9")]
    )
    with pytest.raises(ProgrammingModeError, match="More than one"):
        await commissioning._serial_number_unless_programming_mode(  # pyright: ignore[reportPrivateUsage]
            cast("XKNX", object()), serial, None, None
        )
    # nothing to look for without a serial number, nor for a KNX RF device (its own path)
    assert (
        await commissioning._serial_number_unless_programming_mode(  # pyright: ignore[reportPrivateUsage]
            cast("XKNX", object()), None, None, None
        )
        is None
    )
    assert (
        await commissioning._serial_number_unless_programming_mode(  # pyright: ignore[reportPrivateUsage]
            cast("XKNX", object()), serial, bytes(6), None
        )
        == serial
    )


@pytest.mark.parametrize("found", ["00FAE70AA822", "00FAD6B25D85", None])
async def test_a_secured_device_in_programming_mode_has_to_be_the_certificates(
    monkeypatch: pytest.MonkeyPatch, found: str | None
) -> None:
    serial = bytes.fromhex("00FAE70AA822")
    _in_programming_mode(monkeypatch, [IndividualAddress("15.15.255")])

    async def plain_serial(xknx: XKNX, address: IndividualAddress) -> bytes | None:
        assert address == IndividualAddress("15.15.255")
        return bytes.fromhex(found) if found is not None else None

    monkeypatch.setattr(commissioning, "_plain_serial_number", plain_serial)
    unless = commissioning._serial_number_unless_programming_mode  # pyright: ignore[reportPrivateUsage]
    if found == "00FAE70AA822":
        assert (
            await unless(cast("XKNX", object()), serial, None, cast("Any", object()))
            is None
        )
        return
    with pytest.raises(SerialNumberMismatchError, match="not 00FAE70AA822"):
        await unless(cast("XKNX", object()), serial, None, cast("Any", object()))

"""Tests for individual address programming."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import apci
from xknx.telegram.address import IndividualAddress, IndividualAddressableType

import xknxeditor.download.commissioning as commissioning
from xknxeditor.download.errors import (
    ProgrammingModeError,
    SerialNumberDeclined,
    SerialNumberMismatchError,
    SerialNumberTakenOver,
)


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


def _looked_for(
    devices: list[IndividualAddress],
) -> Callable[[], Awaitable[list[IndividualAddress]]]:
    async def in_programming_mode() -> list[IndividualAddress]:
        return devices

    return in_programming_mode


async def _no_serial_number(address: IndividualAddress) -> bytes | None:
    raise AssertionError("not read for a device in the clear")


_STORED = bytes.fromhex("00FAE70AA822")
_FOUND = bytes.fromhex("00FAD6B25D85")
_unless = commissioning._serial_number_unless_programming_mode  # pyright: ignore[reportPrivateUsage]


async def test_the_serial_number_only_without_a_device_in_programming_mode() -> None:
    one = _looked_for([IndividualAddress("15.15.255")])
    assert (
        await _unless(
            _STORED,
            None,
            in_programming_mode=_looked_for([]),
            read_serial_number=_no_serial_number,
        )
        == _STORED
    )
    assert (
        await _unless(
            _STORED, None, in_programming_mode=one, read_serial_number=_no_serial_number
        )
        is None
    )
    two = _looked_for([IndividualAddress("15.15.255"), IndividualAddress("1.1.9")])
    with pytest.raises(ProgrammingModeError, match="More than one"):
        await _unless(
            _STORED, None, in_programming_mode=two, read_serial_number=_no_serial_number
        )
    # nothing to look for without a serial number, nor with one known to be the device's
    for serial, known in ((None, False), (_STORED, True)):
        assert (
            await _unless(
                serial,
                None,
                in_programming_mode=one,
                read_serial_number=_no_serial_number,
                known=known,
            )
            == serial
        )


@pytest.mark.parametrize(
    ("found", "answer", "raised"),
    [
        (_STORED, None, None),
        (_FOUND, None, "SerialNumberMismatchError"),
        (None, True, "SerialNumberMismatchError"),
        (_FOUND, True, "SerialNumberTakenOver"),
        (_FOUND, False, "SerialNumberDeclined"),
    ],
)
async def test_a_secured_device_in_programming_mode_has_to_be_the_certificates(
    found: bytes | None, answer: bool | None, raised: str | None
) -> None:
    asked: list[tuple[bytes, bytes]] = []

    async def read_serial_number(address: IndividualAddress) -> bytes | None:
        assert address == IndividualAddress("15.15.255")
        return found

    async def confirm(found_serial: bytes, stored: bytes) -> bool:
        asked.append((found_serial, stored))
        assert answer is not None
        return answer

    call = _unless(
        _STORED,
        cast("Any", object()),
        in_programming_mode=_looked_for([IndividualAddress("15.15.255")]),
        read_serial_number=read_serial_number,
        confirm=confirm if answer is not None else None,
    )
    if raised is None:
        assert await call is None
        assert asked == []
        return
    errors = {
        "SerialNumberMismatchError": SerialNumberMismatchError,
        "SerialNumberTakenOver": SerialNumberTakenOver,
        "SerialNumberDeclined": SerialNumberDeclined,
    }
    with pytest.raises(errors[raised]) as error:
        await call
    if raised == "SerialNumberTakenOver":
        assert cast("SerialNumberTakenOver", error.value).serial_number == _FOUND
    if raised == "SerialNumberMismatchError" and found is None:
        # nothing to take over
        assert asked == []
    elif answer is not None:
        assert asked == [(_FOUND, _STORED)]


@pytest.mark.parametrize("multi_coupler", [False, True])
async def test_an_rf_device_is_looked_for_in_both_rf_modes(
    monkeypatch: pytest.MonkeyPatch, multi_coupler: bool
) -> None:
    events: list[object] = []
    answers: list[list[IndividualAddress]] = [[], [IndividualAddress("1.1.9")]]

    async def in_programming_mode(
        xknx: XKNX, system_broadcast: bool = True
    ) -> list[IndividualAddress]:
        events.append(("poll", system_broadcast))
        return answers.pop(0)

    async def switch(multi: bool) -> None:
        events.append(("multi", multi))

    monkeypatch.setattr(
        commissioning, "rf_devices_in_programming_mode", in_programming_mode
    )
    path = cast(
        "Any",
        SimpleNamespace(
            system_broadcast=True, switch_rf_mode=switch if multi_coupler else None
        ),
    )
    found = await commissioning._rf_serial_number_unless_programming_mode(  # pyright: ignore[reportPrivateUsage]
        cast("XKNX", object()), _STORED, None, path, known=False, confirm=None
    )
    if multi_coupler:
        # in KNX RF Ready first, then in KNX RF Multi, and back
        assert found is None
        assert events == [
            ("multi", False),
            ("poll", True),
            ("multi", True),
            ("poll", True),
            ("multi", False),
        ]
    else:
        # one look without a coupler to switch: none in programming mode
        assert found == _STORED
        assert events == [("poll", True)]


async def test_a_new_rf_device_in_programming_mode_gets_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    async def in_programming_mode(*args: object) -> list[IndividualAddress]:
        return [IndividualAddress("15.15.255")]

    async def rf_addresses(
        xknx: XKNX,
        address: IndividualAddress,
        domain_address: bytes,
        serial_number: bytes | None,
        security: object,
        path: object,
    ) -> bytes | None:
        calls.append(serial_number)
        return _FOUND

    @contextlib.asynccontextmanager
    async def no_path(*args: object, **kwargs: object) -> AsyncIterator[Any]:
        yield SimpleNamespace(system_broadcast=True, switch_rf_mode=None)

    monkeypatch.setattr(
        commissioning, "rf_devices_in_programming_mode", in_programming_mode
    )
    monkeypatch.setattr(commissioning, "_program_rf_addresses", rf_addresses)
    monkeypatch.setattr(commissioning, "broadcast_path", no_path)
    assert (
        await commissioning.program_individual_address(
            cast("XKNX", object()),
            "1.1.5",
            serial_number=_STORED,
            domain_address=bytes(6),
        )
        == _FOUND
    )
    # through programming mode, not by the serial number stored
    assert calls == [None]

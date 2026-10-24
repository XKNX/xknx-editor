"""Tests for the stand-alone individual address download and the programming mode switch."""

from __future__ import annotations

from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.telegram import apci
from xknx.telegram.address import IndividualAddress

import xknxeditor.download.commissioning as commissioning
import xknxeditor.download.session as session
from xknxeditor.download.errors import (
    AddressInUseError,
    ProgrammingModeError,
    SerialNumberNotFoundError,
)

from .conftest import FakeDevice

_NEW = IndividualAddress("1.1.5")
_CURRENT = IndividualAddress("1.1.9")


class _Manager:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device
        self.closed = False

    async def open(self) -> FakeDevice:
        return self.device

    async def close(self) -> None:
        self.closed = True


class _Broadcast:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, payload: object) -> None:
        self.sent.append(payload)


class _Management:
    def __init__(self) -> None:
        self.broadcast = _Broadcast()


class _XKNX:
    def __init__(self) -> None:
        self.management = _Management()


def _use_device(monkeypatch: pytest.MonkeyPatch, device: FakeDevice) -> list[Any]:
    opened: list[Any] = []

    def fake_session(
        xknx: XKNX, address: IndividualAddress, security: object
    ) -> _Manager:
        opened.append(address)
        return _Manager(device)

    monkeypatch.setattr(session, "management_session", fake_session)
    return opened


async def test_switch_programming_mode_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x0705)
    device.memory[0x60] = 0x2E
    opened = _use_device(monkeypatch, device)

    await commissioning.switch_programming_mode(cast("XKNX", _XKNX()), _CURRENT, True)

    assert opened == [_CURRENT]
    # Shared bits are kept, prog_mode set and the parity bit inverted.
    assert device.memory[0x60] == 0xAF
    assert any(isinstance(p, apci.MemoryWrite) for p in device.sent)


async def test_switch_programming_mode_in_memory_already_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x0705)
    device.memory[0x60] = 0x81
    _use_device(monkeypatch, device)

    await commissioning.switch_programming_mode(cast("XKNX", _XKNX()), _CURRENT, True)

    assert device.memory[0x60] == 0x81
    assert not any(isinstance(p, apci.MemoryWrite) for p in device.sent)


async def test_switch_programming_mode_by_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x07B0)
    _use_device(monkeypatch, device)

    await commissioning.switch_programming_mode(cast("XKNX", _XKNX()), _CURRENT, True)

    assert device.properties[(0, 54)] == b"\x01"
    assert not any(isinstance(p, apci.MemoryWrite) for p in device.sent)


def _fake_bus(
    monkeypatch: pytest.MonkeyPatch,
    *,
    occupied: bool,
    reads: list[list[IndividualAddress]],
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    async def fake_check(xknx: XKNX, address: IndividualAddress) -> bool:
        calls.append(("check", address))
        return occupied

    async def fake_read(
        xknx: XKNX, timeout: float | None = 3, raise_if_multiple: bool = False
    ) -> list[IndividualAddress]:
        calls.append(("read", timeout))
        return reads.pop(0)

    async def fake_switch(
        xknx: XKNX, address: IndividualAddress, enable: bool, **kwargs: object
    ) -> None:
        calls.append(("switch", (address, enable)))

    async def fake_verify(
        xknx: XKNX,
        address: IndividualAddress,
        security: object,
        application_id: str | None = None,
    ) -> None:
        calls.append(("verify", address))

    async def fake_sleep(delay: float) -> None:
        calls.append(("sleep", delay))

    monkeypatch.setattr(commissioning, "nm_individual_address_check", fake_check)
    monkeypatch.setattr(commissioning, "nm_individual_address_read", fake_read)
    monkeypatch.setattr(commissioning, "switch_programming_mode", fake_switch)
    monkeypatch.setattr(commissioning, "_verify_and_restart", fake_verify)
    monkeypatch.setattr(commissioning.asyncio, "sleep", fake_sleep)
    return calls


async def test_download_waits_for_the_programming_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_bus(
        monkeypatch, occupied=False, reads=[[], [], [IndividualAddress("15.15.255")]]
    )
    xknx = _XKNX()
    waited: list[bool] = []

    await commissioning.download_individual_address(
        cast("XKNX", xknx), _NEW, waiting=lambda: waited.append(True)
    )

    assert waited == [True]
    assert [c[0] for c in calls] == [
        "check",
        "read",
        "read",
        "read",
        "sleep",
        "verify",
    ]
    (payload,) = xknx.management.broadcast.sent
    assert isinstance(payload, apci.IndividualAddressWrite)
    assert payload.address == _NEW


async def test_download_to_a_device_that_already_has_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_bus(monkeypatch, occupied=True, reads=[[_NEW]])
    xknx = _XKNX()

    await commissioning.download_individual_address(cast("XKNX", xknx), _NEW)

    assert xknx.management.broadcast.sent == []
    assert ("verify", _NEW) in calls


async def test_download_refuses_an_address_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_bus(monkeypatch, occupied=True, reads=[[IndividualAddress("15.15.255")]])
    xknx = _XKNX()

    with pytest.raises(AddressInUseError):
        await commissioning.download_individual_address(cast("XKNX", xknx), _NEW)
    assert xknx.management.broadcast.sent == []


async def test_download_refuses_several_devices_in_programming_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_bus(
        monkeypatch,
        occupied=False,
        reads=[[IndividualAddress("1.1.20"), IndividualAddress("1.1.21")]],
    )

    with pytest.raises(ProgrammingModeError):
        await commissioning.download_individual_address(cast("XKNX", _XKNX()), _NEW)


async def test_overwrite_switches_programming_mode_remotely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_bus(monkeypatch, occupied=False, reads=[[_CURRENT]])
    xknx = _XKNX()

    await commissioning.download_individual_address(
        cast("XKNX", xknx), _NEW, current_address=_CURRENT
    )

    assert [c for c in calls if c[0] in ("switch", "verify")] == [
        ("switch", (_CURRENT, True)),
        ("verify", _NEW),
    ]
    (payload,) = xknx.management.broadcast.sent
    assert isinstance(payload, apci.IndividualAddressWrite)
    assert payload.address == _NEW


async def test_overwrite_does_not_wait_for_the_programming_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_bus(monkeypatch, occupied=False, reads=[[]])

    with pytest.raises(ProgrammingModeError):
        await commissioning.download_individual_address(
            cast("XKNX", _XKNX()), _NEW, current_address=_CURRENT
        )


async def test_overwrite_stops_when_another_device_is_in_programming_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_bus(
        monkeypatch, occupied=False, reads=[[IndividualAddress("1.1.30")]]
    )
    xknx = _XKNX()

    with pytest.raises(ProgrammingModeError):
        await commissioning.download_individual_address(
            cast("XKNX", xknx), _NEW, current_address=_CURRENT
        )
    assert ("switch", (_CURRENT, False)) in calls
    assert xknx.management.broadcast.sent == []


async def test_overwrite_refuses_an_address_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_bus(monkeypatch, occupied=True, reads=[])

    with pytest.raises(AddressInUseError):
        await commissioning.download_individual_address(
            cast("XKNX", _XKNX()), _NEW, current_address=_CURRENT
        )
    assert all(c[0] != "switch" for c in calls)


async def test_overwrite_rejects_the_same_address() -> None:
    with pytest.raises(ValueError, match="already has"):
        await commissioning.download_individual_address(
            cast("XKNX", _XKNX()), _NEW, current_address=_NEW
        )


async def test_verify_reads_the_descriptor_and_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x07B0)
    opened = _use_device(monkeypatch, device)

    await commissioning._verify_and_restart(  # pyright: ignore[reportPrivateUsage]
        cast("XKNX", _XKNX()), _NEW, None
    )

    assert opened == [_NEW]
    assert any(isinstance(p, apci.DeviceDescriptorRead) for p in device.sent)
    assert device.restarted


_SERIAL = bytes.fromhex("00837AA2039C")


def _fake_serial_bus(
    monkeypatch: pytest.MonkeyPatch,
    *,
    found: IndividualAddress | None,
    occupied: bool = False,
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    async def fake_serial_read(
        xknx: XKNX, serial: bytes, timeout: float = 3
    ) -> IndividualAddress | None:
        calls.append(("serial_read", serial))
        return found

    async def fake_serial_write(
        xknx: XKNX, serial: bytes, individual_address: IndividualAddress
    ) -> None:
        calls.append(("serial_write", (serial, individual_address)))

    async def fake_check(xknx: XKNX, address: IndividualAddress) -> bool:
        calls.append(("check", address))
        return occupied

    async def fake_read(*args: object, **kwargs: object) -> list[IndividualAddress]:
        raise AssertionError("programming mode must not be used")

    monkeypatch.setattr(
        commissioning, "nm_individual_address_serial_number_read", fake_serial_read
    )
    monkeypatch.setattr(
        commissioning, "nm_individual_address_serial_number_write", fake_serial_write
    )
    monkeypatch.setattr(commissioning, "nm_individual_address_check", fake_check)
    monkeypatch.setattr(commissioning, "nm_individual_address_read", fake_read)
    return calls


async def test_download_by_serial_number(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_serial_bus(monkeypatch, found=IndividualAddress("15.15.255"))

    serial = await commissioning.download_individual_address(
        cast("XKNX", _XKNX()), _NEW, serial_number=_SERIAL
    )

    assert serial == _SERIAL
    assert calls == [
        ("serial_read", _SERIAL),
        ("check", _NEW),
        ("serial_write", (_SERIAL, _NEW)),
    ]


async def test_download_by_serial_number_device_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_serial_bus(monkeypatch, found=None)

    with pytest.raises(SerialNumberNotFoundError, match="00837AA2039C"):
        await commissioning.download_individual_address(
            cast("XKNX", _XKNX()), _NEW, serial_number=_SERIAL
        )
    assert [c[0] for c in calls] == ["serial_read"]


async def test_download_by_serial_number_refuses_an_address_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_serial_bus(
        monkeypatch, found=IndividualAddress("15.15.255"), occupied=True
    )

    with pytest.raises(AddressInUseError):
        await commissioning.download_individual_address(
            cast("XKNX", _XKNX()), _NEW, serial_number=_SERIAL
        )
    assert all(c[0] != "serial_write" for c in calls)


async def test_download_by_serial_number_already_has_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_serial_bus(monkeypatch, found=_NEW, occupied=True)

    await commissioning.download_individual_address(
        cast("XKNX", _XKNX()), _NEW, serial_number=_SERIAL
    )
    assert [c[0] for c in calls] == ["serial_read"]


async def test_overwrite_cannot_use_a_serial_number() -> None:
    with pytest.raises(ValueError, match="current address"):
        await commissioning.download_individual_address(
            cast("XKNX", _XKNX()), _NEW, current_address=_CURRENT, serial_number=_SERIAL
        )


async def test_verify_returns_the_serial_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x07B0)
    device.properties[(0, 11)] = _SERIAL
    _use_device(monkeypatch, device)

    serial = await commissioning._verify_and_restart(  # pyright: ignore[reportPrivateUsage]
        cast("XKNX", _XKNX()), _NEW, None
    )

    assert serial == _SERIAL
    assert device.restarted


async def test_verify_does_not_read_a_serial_number_from_bcu1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x0012)
    _use_device(monkeypatch, device)

    serial = await commissioning._verify_and_restart(  # pyright: ignore[reportPrivateUsage]
        cast("XKNX", _XKNX()), _NEW, None
    )

    assert serial is None
    assert not any(isinstance(p, apci.PropertyValueRead) for p in device.sent)
    assert device.restarted


async def test_verify_skips_the_serial_number_of_listed_applications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x07B0)
    device.properties[(0, 11)] = _SERIAL
    _use_device(monkeypatch, device)

    serial = await commissioning._verify_and_restart(  # pyright: ignore[reportPrivateUsage]
        cast("XKNX", _XKNX()), _NEW, None, "M-004E_A-0020-10-1234"
    )

    assert serial is None
    assert not any(isinstance(p, apci.PropertyValueRead) for p in device.sent)
    assert device.restarted


async def test_verify_ignores_a_serial_number_many_devices_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x07B0)
    device.properties[(0, 11)] = bytes.fromhex("000810910000")
    _use_device(monkeypatch, device)

    serial = await commissioning._verify_and_restart(  # pyright: ignore[reportPrivateUsage]
        cast("XKNX", _XKNX()), _NEW, None
    )

    assert serial is None
    assert device.restarted

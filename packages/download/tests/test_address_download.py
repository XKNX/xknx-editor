"""Tests for the stand-alone individual address download and the programming mode switch."""

from __future__ import annotations

from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.telegram import apci
from xknx.telegram.address import IndividualAddress

import xknxeditor.download.commissioning as commissioning
import xknxeditor.download.session as session
from xknxeditor.download.errors import AddressInUseError, ProgrammingModeError

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


class _Management:
    def __init__(self) -> None:
        self.broadcasts: list[object] = []

    async def send_broadcast(self, payload: object) -> None:
        self.broadcasts.append(payload)


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
        xknx: XKNX, address: IndividualAddress, security: object
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
    (payload,) = xknx.management.broadcasts
    assert isinstance(payload, apci.IndividualAddressWrite)
    assert payload.address == _NEW


async def test_download_to_a_device_that_already_has_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_bus(monkeypatch, occupied=True, reads=[[_NEW]])
    xknx = _XKNX()

    await commissioning.download_individual_address(cast("XKNX", xknx), _NEW)

    assert xknx.management.broadcasts == []
    assert ("verify", _NEW) in calls


async def test_download_refuses_an_address_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_bus(monkeypatch, occupied=True, reads=[[IndividualAddress("15.15.255")]])
    xknx = _XKNX()

    with pytest.raises(AddressInUseError):
        await commissioning.download_individual_address(cast("XKNX", xknx), _NEW)
    assert xknx.management.broadcasts == []


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
    (payload,) = xknx.management.broadcasts
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
    assert xknx.management.broadcasts == []


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

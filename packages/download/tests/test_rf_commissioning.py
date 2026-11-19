"""Tests for the domain and individual address of KNX RF devices (system broadcasts)."""

from __future__ import annotations

import contextlib
import importlib
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.cemi import cemi_handler
from xknx.cemi.cemi_frame import CEMIFrame
from xknx.cemi.const import CEMIMessageCode
from xknx.exceptions import ConfirmationError, ManagementConnectionError
from xknx.telegram import GroupAddress, IndividualAddress, Telegram, apci
from xknx.telegram.tpci import TDataBroadcast

import xknxeditor.download.commissioning as commissioning
import xknxeditor.download.rf_commissioning as rf
import xknxeditor.download.session as session
from xknxeditor.download.communication_path import BroadcastPath, KnxMedium
from xknxeditor.download.errors import (
    AddressInUseError,
    ProgrammingModeError,
    SerialNumberNotFoundError,
)

from .conftest import FakeDevice

DOMAIN_ADDRESS = bytes.fromhex("00fa12345678")
_NEW = IndividualAddress("1.2.20")
_FACTORY = IndividualAddress("15.15.255")
_SERIAL = bytes.fromhex("00837AA2039C")


class _Interface:
    """A KNX interface that confirms every frame (or none)."""

    def __init__(self, xknx: XKNX, *, confirms: bool = True) -> None:
        self._xknx = xknx
        self.confirms = confirms
        self.sent: list[bytes] = []

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        raw = cemi.to_knx()
        self.sent.append(raw)
        if self.confirms:
            confirmation = CEMIFrame.from_knx(bytes((0x2E,)) + raw[1:])
            self._xknx.cemi_handler.handle_cemi_frame(confirmation)


def _xknx(*, confirms: bool = True) -> tuple[XKNX, _Interface]:
    xknx = XKNX()
    interface = _Interface(xknx, confirms=confirms)
    xknx.knxip_interface = cast(Any, interface)
    return xknx, interface


async def test_a_system_broadcast_has_the_flag_cleared() -> None:
    xknx, interface = _xknx()
    await rf.send_system_broadcast(xknx, apci.IndividualAddressRead())
    (raw,) = interface.sent
    assert raw[0] == CEMIMessageCode.L_DATA_REQ.value
    # Ctrl1 bit 4: 0 is a system broadcast; to the broadcast address 0/0/0
    assert not raw[2] & 0x10
    assert raw[6:8] == b"\x00\x00"
    frame = CEMIFrame.from_knx(raw)
    assert isinstance(frame.data.tpci, TDataBroadcast)  # type: ignore[union-attr]


async def test_a_system_broadcast_the_interface_does_not_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cemi_handler, "REQUEST_TO_CONFIRMATION_TIMEOUT", 0.05)
    xknx, _ = _xknx(confirms=False)
    with pytest.raises(ConfirmationError):
        await rf.send_system_broadcast(xknx, apci.IndividualAddressRead())


class _Bus:
    """The devices in programming mode, answering the system broadcasts sent."""

    def __init__(self, xknx: XKNX, polls: list[list[IndividualAddress]]) -> None:
        self.xknx = xknx
        self.polls = polls
        self.sent: list[apci.APCI] = []

    async def send(self, xknx: XKNX, payload: apci.APCI) -> None:
        self.sent.append(payload)
        if isinstance(payload, apci.IndividualAddressRead):
            for address in self.polls.pop(0):
                self.xknx.management.process(
                    Telegram(
                        source_address=address,
                        destination_address=GroupAddress(0),
                        tpci=TDataBroadcast(),
                        payload=apci.IndividualAddressResponse(),
                    )
                )


def _fake_bus(
    monkeypatch: pytest.MonkeyPatch,
    polls: list[list[IndividualAddress]],
    device: FakeDevice | None = None,
) -> tuple[XKNX, _Bus, list[Any]]:
    xknx, _ = _xknx()
    bus = _Bus(xknx, polls)
    monkeypatch.setattr(rf, "send_system_broadcast", bus.send)
    monkeypatch.setattr(rf, "_PROGRAMMING_MODE_POLL_S", 0.01)
    monkeypatch.setattr(rf, "_ADDRESS_ADOPTION_DELAY_S", 0)
    opened: list[Any] = []

    class _Manager:
        async def open(self) -> FakeDevice:
            if device is None:
                raise ManagementConnectionError("no answer")
            return device

        async def close(self) -> None:
            opened.append("closed")

    def fake_session(
        xknx: XKNX, address: IndividualAddress, security: object, **kwargs: object
    ) -> _Manager:
        opened.append((address, kwargs))
        return _Manager()

    monkeypatch.setattr(session, "management_session", fake_session)
    return xknx, bus, opened


async def test_write2(monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice(descriptor=0x2705)
    device.properties[(0, 11)] = _SERIAL
    xknx, bus, opened = _fake_bus(monkeypatch, [[], [_FACTORY]], device)
    waited: list[bool] = []

    current, serial = await rf.domain_and_individual_address_write2(
        xknx, DOMAIN_ADDRESS, _NEW, waiting=lambda: waited.append(True)
    )

    assert (current, serial) == (_FACTORY, _SERIAL)
    assert waited == [True]
    assert bus.sent == [
        apci.IndividualAddressRead(),
        apci.IndividualAddressRead(),
        apci.DomainAddressWrite(domain_address=DOMAIN_ADDRESS),
        apci.IndividualAddressWrite(address=_NEW),
    ]
    # verified connectionless at the new address, then restarted
    assert opened == [(_NEW, {"connectionless": True}), "closed"]
    assert any(isinstance(p, apci.DeviceDescriptorRead) for p in device.sent)
    assert device.restarted


async def test_write2_to_a_device_with_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x2705)
    xknx, bus, _ = _fake_bus(monkeypatch, [[_NEW]], device)

    current, serial = await rf.domain_and_individual_address_write2(
        xknx, DOMAIN_ADDRESS, _NEW, restart=False
    )

    assert (current, serial) == (_NEW, None)
    assert bus.sent == [
        apci.IndividualAddressRead(),
        apci.DomainAddressWrite(domain_address=DOMAIN_ADDRESS),
    ]
    assert not device.restarted


@pytest.mark.parametrize(
    ("polls", "message"),
    [([[]], "No device"), ([[_FACTORY, _NEW]], "More than one")],
)
async def test_write2_needs_one_device_in_programming_mode(
    monkeypatch: pytest.MonkeyPatch,
    polls: list[list[IndividualAddress]],
    message: str,
) -> None:
    xknx, bus, _ = _fake_bus(monkeypatch, polls)
    with pytest.raises(ProgrammingModeError, match=message):
        await rf.domain_and_individual_address_write2(xknx, DOMAIN_ADDRESS, _NEW)
    assert bus.sent == [apci.IndividualAddressRead()]


async def test_write2_refused_by_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    xknx, bus, _ = _fake_bus(monkeypatch, [[_FACTORY]])

    def refuse(found: IndividualAddress) -> None:
        raise AddressInUseError(str(found))

    with pytest.raises(AddressInUseError):
        await rf.domain_and_individual_address_write2(
            xknx, DOMAIN_ADDRESS, _NEW, check=refuse
        )
    assert bus.sent == [apci.IndividualAddressRead()]


async def test_write2_without_an_answer_at_the_new_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xknx, _, opened = _fake_bus(monkeypatch, [[_FACTORY]])
    with pytest.raises(
        ManagementConnectionError, match=r"No device answered at 1\.2\.20"
    ):
        await rf.domain_and_individual_address_write2(xknx, DOMAIN_ADDRESS, _NEW)
    assert opened[-1] == "closed"


async def test_domain_address_by_serial_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xknx, bus, _ = _fake_bus(monkeypatch, [])
    monkeypatch.setattr(rf, "_SERIAL_NUMBER_VERIFY_DELAY_S", 0)
    answers: list[IndividualAddress | None] = [None, _FACTORY]

    async def serial_read(xknx: XKNX, serial: bytes) -> IndividualAddress | None:
        assert serial == _SERIAL
        return answers.pop(0)

    read_module = importlib.import_module(
        "xknx.management.procedures.network.nm_individual_address_serial_number_read"
    )

    monkeypatch.setattr(
        read_module, "nm_individual_address_serial_number_read", serial_read
    )
    await rf.domain_address_serial_number_write(xknx, _SERIAL, DOMAIN_ADDRESS)
    # not found the first time: written again
    assert (
        bus.sent
        == [
            apci.DomainAddressSerialNumberWrite(
                serial=_SERIAL, domain_address=DOMAIN_ADDRESS
            )
        ]
        * 2
    )
    answers.extend([None, None])
    with pytest.raises(SerialNumberNotFoundError):
        await rf.domain_address_serial_number_write(xknx, _SERIAL, DOMAIN_ADDRESS)


def _commissioning_bus(
    monkeypatch: pytest.MonkeyPatch,
    *,
    occupied: bool = False,
    found: IndividualAddress = _FACTORY,
    local_system_broadcast: bool = True,
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    async def fake_check(xknx: XKNX, address: IndividualAddress) -> bool:
        calls.append(("check", address))
        return occupied

    async def fake_switch(
        xknx: XKNX, address: IndividualAddress, enable: bool, **kwargs: object
    ) -> None:
        calls.append(("switch", (address, enable)))

    async def fake_write2(
        xknx: XKNX,
        domain_address: bytes,
        address: IndividualAddress,
        **kwargs: Any,
    ) -> tuple[IndividualAddress, bytes | None]:
        calls.append(("write2", (domain_address, address, kwargs.get("restart", True))))
        if not kwargs.get("system_broadcast", True):
            calls.append(("as broadcast", address))
        check = kwargs.get("check")
        if check is not None:
            check(found)
        return found, _SERIAL

    async def fake_domain_by_serial(
        xknx: XKNX, serial: bytes, domain_address: bytes, **kwargs: Any
    ) -> None:
        calls.append(("domain by serial", (serial, domain_address)))
        if not kwargs.get("system_broadcast", True):
            calls.append(("as broadcast", serial))

    async def fake_serial_write(
        xknx: XKNX, address: IndividualAddress, serial: bytes
    ) -> None:
        calls.append(("address by serial", (address, serial)))

    async def fake_nm_serial_write(
        xknx: XKNX, serial: bytes, address: IndividualAddress
    ) -> None:
        calls.append(("address by serial", (address, serial)))

    monkeypatch.setattr(commissioning, "nm_individual_address_check", fake_check)
    monkeypatch.setattr(commissioning, "switch_programming_mode", fake_switch)
    monkeypatch.setattr(
        commissioning, "domain_and_individual_address_write2", fake_write2
    )
    monkeypatch.setattr(
        commissioning, "domain_address_serial_number_write", fake_domain_by_serial
    )

    async def fake_secure(
        xknx: XKNX,
        serial: bytes,
        address: IndividualAddress,
        security: object,
        **kwargs: Any,
    ) -> None:
        in_use = kwargs.get("address_in_use")
        calls.append(
            ("secure", (serial, address, security, kwargs.get("domain_address")))
        )
        if not kwargs.get("system_broadcast", True):
            calls.append(("as broadcast", serial))
        if in_use is not None:
            calls.append(("in use", await in_use()))

    async def fake_read_serial(
        xknx: XKNX, address: IndividualAddress, security: object
    ) -> bytes:
        calls.append(("read serial", (address, security)))
        return _SERIAL

    async def fake_verify(
        xknx: XKNX, address: IndividualAddress, security: object
    ) -> bytes:
        calls.append(("verify", (address, security)))
        return _SERIAL

    @contextlib.asynccontextmanager
    async def fake_routing(
        xknx: XKNX,
        target: IndividualAddress,
        *,
        system_broadcast: bool,
        medium: KnxMedium = KnxMedium.RF,
    ) -> AsyncGenerator[BroadcastPath]:
        calls.append(("routing", (target, system_broadcast)))
        yield BroadcastPath(system_broadcast and local_system_broadcast)
        calls.append(("routing end", target))

    monkeypatch.setattr(commissioning, "_write_by_serial_number", fake_serial_write)
    monkeypatch.setattr(
        commissioning,
        "nm_individual_address_serial_number_write",
        fake_nm_serial_write,
    )
    monkeypatch.setattr(commissioning, "secure_addresses_by_serial_number", fake_secure)
    monkeypatch.setattr(commissioning, "_read_serial_number", fake_read_serial)
    monkeypatch.setattr(commissioning, "_verify_and_restart", fake_verify)
    monkeypatch.setattr(commissioning, "broadcast_path", fake_routing)
    return calls


def _without_routing(calls: list[tuple[str, object]]) -> list[tuple[str, object]]:
    return [call for call in calls if not call[0].startswith("routing")]


async def test_an_rf_address_download(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _commissioning_bus(monkeypatch)
    serial = await commissioning.download_individual_address(
        cast("XKNX", None), _NEW, domain_address=DOMAIN_ADDRESS
    )
    assert serial == _SERIAL
    assert _without_routing(calls) == [
        ("check", _NEW),
        ("write2", (DOMAIN_ADDRESS, _NEW, True)),
    ]


async def test_an_rf_address_download_refuses_an_address_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _commissioning_bus(monkeypatch, occupied=True)
    with pytest.raises(AddressInUseError):
        await commissioning.download_individual_address(
            cast("XKNX", None), _NEW, domain_address=DOMAIN_ADDRESS
        )
    # the device that has it is the one in programming mode: written again
    calls = _commissioning_bus(monkeypatch, occupied=True, found=_NEW)
    await commissioning.download_individual_address(
        cast("XKNX", None), _NEW, domain_address=DOMAIN_ADDRESS
    )
    assert _without_routing(calls)[-1][0] == "write2"


async def test_an_rf_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    current = IndividualAddress("1.2.9")
    calls = _commissioning_bus(monkeypatch, found=current)
    await commissioning.download_individual_address(
        cast("XKNX", None),
        _NEW,
        current_address=current,
        domain_address=DOMAIN_ADDRESS,
    )
    assert _without_routing(calls)[:2] == [("check", _NEW), ("switch", (current, True))]
    # another device in programming mode: switched back, refused
    calls = _commissioning_bus(monkeypatch, found=_FACTORY)
    with pytest.raises(ProgrammingModeError, match=r"instead of 1\.2\.9"):
        await commissioning.download_individual_address(
            cast("XKNX", None),
            _NEW,
            current_address=current,
            domain_address=DOMAIN_ADDRESS,
        )
    assert _without_routing(calls)[-1] == ("switch", (current, False))


async def test_an_rf_download_by_serial_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _commissioning_bus(monkeypatch)
    await commissioning.download_individual_address(
        cast("XKNX", None), _NEW, serial_number=_SERIAL, domain_address=DOMAIN_ADDRESS
    )
    assert _without_routing(calls) == [
        ("domain by serial", (_SERIAL, DOMAIN_ADDRESS)),
        ("address by serial", (_NEW, _SERIAL)),
    ]


async def test_commissioning_an_rf_device_for_a_full_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _commissioning_bus(monkeypatch)
    await commissioning.program_individual_address(
        cast("XKNX", None), _NEW, domain_address=DOMAIN_ADDRESS
    )
    # left running for the download
    assert _without_routing(calls) == [("write2", (DOMAIN_ADDRESS, _NEW, False))]
    calls.clear()
    await commissioning.program_individual_address(
        cast("XKNX", None), _NEW, serial_number=_SERIAL, domain_address=DOMAIN_ADDRESS
    )
    assert _without_routing(calls) == [
        ("domain by serial", (_SERIAL, DOMAIN_ADDRESS)),
        ("address by serial", (_NEW, _SERIAL)),
    ]


class _Security:
    """A device's Data Secure keys, as the commissioning passes them on."""


async def test_a_secured_rf_address_download_by_serial_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _commissioning_bus(monkeypatch)
    security = _Security()
    serial = await commissioning.download_individual_address(
        cast("XKNX", None),
        _NEW,
        serial_number=_SERIAL,
        security=cast(Any, security),
        domain_address=DOMAIN_ADDRESS,
    )
    assert serial == _SERIAL
    assert calls == [
        ("routing", (_NEW, True)),
        ("secure", (_SERIAL, _NEW, security, DOMAIN_ADDRESS)),
        ("check", _NEW),
        ("in use", False),
        ("routing end", _NEW),
    ]


async def test_a_secured_rf_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    current = IndividualAddress("1.2.9")
    calls = _commissioning_bus(monkeypatch)
    security, current_security = _Security(), _Security()
    serial = await commissioning.download_individual_address(
        cast("XKNX", None),
        _NEW,
        current_address=current,
        security=cast(Any, security),
        current_security=cast(Any, current_security),
        domain_address=DOMAIN_ADDRESS,
    )
    assert serial == _SERIAL
    # a device in Security Mode takes its new addresses only secured, by serial number
    assert _without_routing(calls) == [
        ("read serial", (current, current_security)),
        ("secure", (_SERIAL, _NEW, current_security, DOMAIN_ADDRESS)),
        ("check", _NEW),
        ("in use", False),
        ("verify", (_NEW, security)),
    ]


async def test_a_secured_commissioning_for_a_full_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _commissioning_bus(monkeypatch)
    security = _Security()
    await commissioning.program_individual_address(
        cast("XKNX", None),
        _NEW,
        serial_number=_SERIAL,
        domain_address=DOMAIN_ADDRESS,
        security=cast(Any, security),
    )
    assert calls == [
        ("routing", (_NEW, True)),
        ("secure", (_SERIAL, _NEW, security, DOMAIN_ADDRESS)),
        ("routing end", _NEW),
    ]


async def test_the_serial_number_of_the_device_to_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x2705)
    device.properties[(0, 11)] = _SERIAL
    opened: list[Any] = []

    class _Manager:
        def __init__(self, device: FakeDevice | None) -> None:
            self.device = device

        async def open(self) -> FakeDevice:
            if self.device is None:
                raise ManagementConnectionError("no answer")
            return self.device

        async def close(self) -> None:
            opened.append("closed")

    devices: list[FakeDevice | None] = [device, None, FakeDevice()]
    monkeypatch.setattr(
        session, "management_session", lambda *args, **kwargs: _Manager(devices.pop(0))
    )
    current = IndividualAddress("1.2.9")
    read = commissioning._read_serial_number  # pyright: ignore[reportPrivateUsage]
    assert await read(cast("XKNX", None), current, cast(Any, None)) == _SERIAL
    with pytest.raises(SerialNumberNotFoundError, match="could not be read"):
        await read(cast("XKNX", None), current, cast(Any, None))
    with pytest.raises(SerialNumberNotFoundError, match="no serial number"):
        await read(cast("XKNX", None), current, cast(Any, None))
    assert opened == ["closed"] * 3


async def test_an_rf_overwrite_refuses_an_address_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _commissioning_bus(monkeypatch, occupied=True)
    with pytest.raises(AddressInUseError):
        await commissioning.download_individual_address(
            cast("XKNX", None),
            _NEW,
            current_address=IndividualAddress("1.2.9"),
            domain_address=DOMAIN_ADDRESS,
        )


async def test_the_serial_number_read(monkeypatch: pytest.MonkeyPatch) -> None:
    read_module = importlib.import_module(
        "xknx.management.procedures.network.nm_individual_address_serial_number_read"
    )

    async def serial_read(xknx: XKNX, serial: bytes) -> IndividualAddress | None:
        return _NEW if serial == _SERIAL else None

    monkeypatch.setattr(
        read_module, "nm_individual_address_serial_number_read", serial_read
    )
    read = rf._serial_number_read  # pyright: ignore[reportPrivateUsage]
    assert await read(cast("XKNX", None), _SERIAL) == _NEW
    assert await read(cast("XKNX", None), bytes(6)) is None


async def test_services_a_coupler_makes_system_broadcasts_go_as_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _commissioning_bus(monkeypatch, local_system_broadcast=False)
    await commissioning.download_individual_address(
        cast("XKNX", None), _NEW, domain_address=DOMAIN_ADDRESS
    )
    assert ("as broadcast", _NEW) in calls
    calls = _commissioning_bus(monkeypatch, local_system_broadcast=False)
    await commissioning.download_individual_address(
        cast("XKNX", None), _NEW, serial_number=_SERIAL, domain_address=DOMAIN_ADDRESS
    )
    assert ("as broadcast", _SERIAL) in calls
    calls = _commissioning_bus(monkeypatch, local_system_broadcast=False)
    await commissioning.program_individual_address(
        cast("XKNX", None), _NEW, domain_address=DOMAIN_ADDRESS
    )
    assert ("as broadcast", _NEW) in calls
    calls = _commissioning_bus(monkeypatch, local_system_broadcast=False)
    await commissioning.program_individual_address(
        cast("XKNX", None), _NEW, serial_number=_SERIAL, domain_address=DOMAIN_ADDRESS
    )
    assert ("as broadcast", _SERIAL) in calls
    # secured, by serial number
    for program in (
        commissioning.program_individual_address,
        commissioning.download_individual_address,
    ):
        calls = _commissioning_bus(monkeypatch, local_system_broadcast=False)
        await program(
            cast("XKNX", None),
            _NEW,
            serial_number=_SERIAL,
            domain_address=DOMAIN_ADDRESS,
            security=cast(Any, _Security()),
        )
        assert ("as broadcast", _SERIAL) in calls
    calls = _commissioning_bus(monkeypatch, local_system_broadcast=False)
    await commissioning.download_individual_address(
        cast("XKNX", None),
        _NEW,
        current_address=IndividualAddress("1.2.9"),
        current_security=cast(Any, _Security()),
        domain_address=DOMAIN_ADDRESS,
    )
    assert ("as broadcast", _SERIAL) in calls


async def test_a_tp_address_download_opens_the_path_for_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _commissioning_bus(monkeypatch)
    await commissioning.download_individual_address(
        cast("XKNX", None), _NEW, serial_number=_SERIAL
    )
    assert calls == [
        ("routing", (_NEW, False)),
        ("address by serial", (_NEW, _SERIAL)),
        ("routing end", _NEW),
    ]
    calls = _commissioning_bus(monkeypatch)
    await commissioning.program_individual_address(
        cast("XKNX", None), _NEW, serial_number=_SERIAL
    )
    assert calls == [
        ("routing", (_NEW, False)),
        ("address by serial", (_NEW, _SERIAL)),
        ("routing end", _NEW),
    ]


async def test_write2_as_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice(descriptor=0x2705)
    xknx, bus, _ = _fake_bus(monkeypatch, [[_FACTORY]], device)
    broadcasts: list[apci.APCI] = []

    async def broadcast_send(self: object, payload: apci.APCI) -> None:
        broadcasts.append(payload)
        await bus.send(xknx, payload)

    monkeypatch.setattr(type(xknx.management.broadcast), "send", broadcast_send)
    await rf.domain_and_individual_address_write2(
        xknx, DOMAIN_ADDRESS, _NEW, system_broadcast=False
    )
    # the same services, as broadcasts for the tool's own coupler to route on
    assert broadcasts == bus.sent
    assert [type(p) for p in broadcasts] == [
        apci.IndividualAddressRead,
        apci.DomainAddressWrite,
        apci.IndividualAddressWrite,
    ]

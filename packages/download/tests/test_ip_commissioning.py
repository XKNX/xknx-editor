"""Tests for the domain address of KNX IP devices, and for the commissioning through couplers
that change how a device is reached."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress, apci

import xknxeditor.download.commissioning as commissioning
import xknxeditor.download.rf_commissioning as rf
from xknxeditor.download.communication_path import (
    BroadcastPath,
    KnxMedium,
    SecureSystemBroadcastBlocked,
)
from xknxeditor.download.data_secure import SecureProgrammingError

SERIAL = bytes.fromhex("00837AA2039C")
MULTICAST = bytes((224, 0, 23, 12))
BACKBONE = bytes(range(16))
_NEW = IndividualAddress("1.0.20")
_OLD = IndividualAddress("15.15.255")
_COUPLER = IndividualAddress("1.0.0")


def test_the_domain_address_of_an_ip_device() -> None:
    assert rf.ip_domain_address(MULTICAST, None) == MULTICAST
    secured = rf.ip_domain_address(MULTICAST, BACKBONE)
    assert secured == MULTICAST + b"\x01" + BACKBONE
    with pytest.raises(ValueError, match="multicast"):
        rf.ip_domain_address(b"\x00", None)
    with pytest.raises(ValueError, match="backbone"):
        rf.ip_domain_address(MULTICAST, b"\x00")
    assert rf.domain_address_apdu(
        SERIAL, secured
    ) == apci.DomainAddressSerialNumberWrite(
        serial=SERIAL,
        domain_address=MULTICAST,
        routing_security_version=1,
        backbone_key=BACKBONE,
    )
    assert rf.domain_address_apdu(SERIAL, MULTICAST) == (
        apci.DomainAddressSerialNumberWrite(serial=SERIAL, domain_address=MULTICAST)
    )


def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rf, "_IP_DOMAIN_ADDRESS_DELAY_S", 0)
    monkeypatch.setattr(rf, "_SERIAL_NUMBER_POLL_S", 0)


def _reads(
    monkeypatch: pytest.MonkeyPatch, reads: list[IndividualAddress | Exception | None]
) -> None:
    async def serial_read(xknx: XKNX, serial: bytes) -> IndividualAddress | None:
        answer = reads.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(rf, "_serial_number_read", serial_read)


async def test_the_device_is_waited_for_by_serial_number(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _fast(monkeypatch)
    _reads(monkeypatch, [None, ManagementConnectionError("no answer"), _OLD])
    assert await rf.wait_by_serial_number(cast("XKNX", None), SERIAL) == _OLD
    # it gives up after the time
    monkeypatch.setattr(rf, "_SERIAL_NUMBER_WAIT_S", 0)
    _reads(monkeypatch, [None])
    with caplog.at_level(logging.WARNING):
        assert await rf.wait_by_serial_number(cast("XKNX", None), SERIAL) is None
    assert "00837AA2039C still does not answer" in caplog.text


def _xknx() -> tuple[XKNX, AsyncMock]:
    send = AsyncMock()
    xknx = SimpleNamespace(
        management=SimpleNamespace(broadcast=SimpleNamespace(send=send))
    )
    return cast("XKNX", xknx), send


async def test_the_addresses_of_an_ip_device(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast(monkeypatch)
    _reads(monkeypatch, [_OLD])
    sent: list[tuple[apci.APCI, bool]] = []

    async def fake_send(xknx: XKNX, payload: apci.APCI, system_broadcast: bool) -> None:
        sent.append((payload, system_broadcast))

    monkeypatch.setattr(rf, "_send", fake_send)
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    xknx, broadcast = _xknx()
    await rf.ip_addresses_by_serial_number(
        xknx,
        SERIAL,
        _NEW,
        MULTICAST,
        system_broadcast=True,
        close_system_broadcast=close,
    )
    assert sent == [
        (
            apci.DomainAddressSerialNumberWrite(
                serial=SERIAL, domain_address=MULTICAST
            ),
            True,
        )
    ]
    assert closed == [True]
    broadcast.assert_awaited_once_with(
        apci.IndividualAddressSerialWrite(address=_NEW, serial=SERIAL)
    )
    # a device that has the address already keeps it
    _reads(monkeypatch, [_NEW])
    xknx, broadcast = _xknx()
    await rf.ip_addresses_by_serial_number(
        xknx, SERIAL, _NEW, MULTICAST, system_broadcast=False
    )
    broadcast.assert_not_awaited()


async def test_the_rf_mode_is_switched_while_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: list[list[IndividualAddress]] = [[], [], [_OLD]]

    async def in_programming_mode(
        xknx: XKNX, system_broadcast: bool = True
    ) -> list[IndividualAddress]:
        return answers.pop(0)

    monkeypatch.setattr(rf, "_devices_in_programming_mode", in_programming_mode)
    modes: list[bool] = []

    async def switch(multi: bool) -> None:
        modes.append(multi)

    found = await rf.wait_for_rf_programming_mode(
        cast("XKNX", None), lambda: None, switch_rf_mode=switch
    )
    assert found == _OLD
    # Ready first, then RF Multi and back
    assert modes == [False, True, False]


def _path(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, object]],
    *,
    local: bool = False,
    restriction: IndividualAddress | None = None,
) -> None:
    async def close() -> None:
        calls.append(("close", None))

    @contextlib.asynccontextmanager
    async def fake_path(
        xknx: XKNX,
        target: IndividualAddress,
        *,
        system_broadcast: bool,
        medium: KnxMedium = KnxMedium.RF,
    ) -> AsyncGenerator[BroadcastPath]:
        calls.append(("path", (target, system_broadcast, medium)))
        yield BroadcastPath(
            local, secure_restriction=restriction, close_system_broadcast=close
        )

    monkeypatch.setattr(commissioning, "broadcast_path", fake_path)


def _ip_bus(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, object]],
    *,
    secure_fails: bool = False,
) -> None:
    async def plain(
        xknx: XKNX,
        serial: bytes,
        address: IndividualAddress,
        domain: bytes,
        **kwargs: Any,
    ) -> None:
        calls.append(("plain", (serial, address, domain, kwargs["system_broadcast"])))
        await kwargs["close_system_broadcast"]()

    async def secured(
        xknx: XKNX,
        serial: bytes,
        address: IndividualAddress,
        security: object,
        **kwargs: Any,
    ) -> None:
        calls.append(("secured", (serial, address, kwargs["domain_address"])))
        in_use = kwargs.get("address_in_use")
        if in_use is not None:
            calls.append(("in use", await in_use()))
        if secure_fails:
            raise SecureProgrammingError("none of the keys")

    async def read_serial(
        xknx: XKNX, address: IndividualAddress, security: object
    ) -> bytes:
        calls.append(("read serial", address))
        return SERIAL

    async def verify(xknx: XKNX, address: IndividualAddress, security: object) -> bytes:
        calls.append(("verify", address))
        return SERIAL

    async def check(xknx: XKNX, address: IndividualAddress) -> bool:
        return False

    monkeypatch.setattr(commissioning, "ip_addresses_by_serial_number", plain)
    monkeypatch.setattr(commissioning, "secure_addresses_by_serial_number", secured)
    monkeypatch.setattr(commissioning, "_read_serial_number", read_serial)
    monkeypatch.setattr(commissioning, "_verify_and_restart", verify)
    monkeypatch.setattr(commissioning, "nm_individual_address_check", check)


async def test_an_ip_device_by_serial_number(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    _path(monkeypatch, calls, local=True)
    _ip_bus(monkeypatch, calls)
    serial = await commissioning.download_individual_address(
        cast("XKNX", None),
        _NEW,
        serial_number=SERIAL,
        domain_address=MULTICAST,
        ip_system_broadcast=True,
    )
    assert serial == SERIAL
    assert calls == [
        ("path", (_NEW, True, KnxMedium.IP)),
        ("plain", (SERIAL, _NEW, MULTICAST, True)),
        ("close", None),
    ]
    # the full download's commissioning, secured with its keys
    calls.clear()
    await commissioning.program_individual_address(
        cast("XKNX", None),
        _NEW,
        serial_number=SERIAL,
        domain_address=MULTICAST + b"\x01" + BACKBONE,
        security=cast("Any", object()),
    )
    assert calls == [
        ("path", (_NEW, False, KnxMedium.IP)),
        ("secured", (SERIAL, _NEW, MULTICAST + b"\x01" + BACKBONE)),
    ]


async def test_an_ip_device_overwritten_secured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    _path(monkeypatch, calls)
    _ip_bus(monkeypatch, calls)
    current = IndividualAddress("1.0.9")
    await commissioning.download_individual_address(
        cast("XKNX", None),
        _NEW,
        current_address=current,
        current_security=cast("Any", object()),
        domain_address=MULTICAST,
    )
    assert calls == [
        ("path", (_NEW, False, KnxMedium.IP)),
        ("read serial", current),
        ("secured", (SERIAL, _NEW, MULTICAST)),
        ("in use", False),
        ("verify", _NEW),
    ]


async def test_an_ip_device_through_programming_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the domain address of an IP device is written by serial number only
    calls: list[tuple[str, object]] = []
    _path(monkeypatch, calls)
    written: list[IndividualAddress] = []

    async def via_programming_mode(xknx: XKNX, address: IndividualAddress) -> None:
        written.append(address)

    monkeypatch.setattr(
        commissioning, "_write_address_via_programming_mode", via_programming_mode
    )
    await commissioning.program_individual_address(
        cast("XKNX", None), _NEW, domain_address=MULTICAST
    )
    assert calls == [("path", (_NEW, False, KnxMedium.TP))]
    assert written == [_NEW]


async def test_the_backbone_key_is_never_written_in_the_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    _path(monkeypatch, calls)
    _ip_bus(monkeypatch, calls)
    with pytest.raises(SecureProgrammingError, match="Tool Key"):
        await commissioning.program_individual_address(
            cast("XKNX", None),
            _NEW,
            serial_number=SERIAL,
            domain_address=MULTICAST + b"\x01" + BACKBONE,
        )
    # a coupler that does not pass secured system broadcasts stops it
    calls.clear()
    _path(monkeypatch, calls, restriction=_COUPLER)
    _ip_bus(monkeypatch, calls, secure_fails=True)
    with pytest.raises(SecureSystemBroadcastBlocked) as caught:
        await commissioning.program_individual_address(
            cast("XKNX", None),
            _NEW,
            serial_number=SERIAL,
            domain_address=MULTICAST + b"\x01" + BACKBONE,
            security=cast("Any", object()),
        )
    assert caught.value.coupler == _COUPLER


async def test_written_in_the_clear_when_a_coupler_stops_secured_system_broadcasts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[str, object]] = []
    _path(monkeypatch, calls, restriction=_COUPLER)
    _ip_bus(monkeypatch, calls, secure_fails=True)
    with caplog.at_level(logging.WARNING):
        await commissioning.download_individual_address(
            cast("XKNX", None),
            _NEW,
            serial_number=SERIAL,
            domain_address=MULTICAST,
            security=cast("Any", object()),
        )
    assert ("plain", (SERIAL, _NEW, MULTICAST, False)) in calls
    assert "coupler 1.0.0 does not pass secured system broadcasts" in caplog.text
    # without a coupler to blame the failure stands
    calls.clear()
    _path(monkeypatch, calls)
    with pytest.raises(SecureProgrammingError):
        await commissioning.download_individual_address(
            cast("XKNX", None),
            _NEW,
            serial_number=SERIAL,
            domain_address=MULTICAST,
            security=cast("Any", object()),
        )


async def test_an_rf_device_written_in_the_clear_behind_such_a_coupler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    _path(monkeypatch, calls, restriction=_COUPLER)
    _ip_bus(monkeypatch, calls, secure_fails=True)
    rf_domain = bytes.fromhex("00fa12345678")

    async def domain_by_serial(
        xknx: XKNX, serial: bytes, domain: bytes, **kwargs: Any
    ) -> None:
        calls.append(("rf domain", domain))

    async def address_by_serial(
        xknx: XKNX, serial: bytes, address: IndividualAddress
    ) -> None:
        calls.append(("rf address", address))

    async def write_by_serial(
        xknx: XKNX, address: IndividualAddress, serial: bytes
    ) -> None:
        calls.append(("rf address", address))

    monkeypatch.setattr(
        commissioning, "domain_address_serial_number_write", domain_by_serial
    )
    monkeypatch.setattr(
        commissioning, "nm_individual_address_serial_number_write", address_by_serial
    )
    monkeypatch.setattr(commissioning, "_write_by_serial_number", write_by_serial)
    await commissioning.program_individual_address(
        cast("XKNX", None),
        _NEW,
        serial_number=SERIAL,
        domain_address=rf_domain,
        security=cast("Any", object()),
    )
    assert calls[-2:] == [("rf domain", rf_domain), ("rf address", _NEW)]
    # also for the address download, and for the overwrite of a secured device
    for kwargs in (
        {"serial_number": SERIAL, "security": cast("Any", object())},
        {
            "current_address": IndividualAddress("1.0.9"),
            "current_security": cast("Any", object()),
        },
    ):
        calls.clear()
        await commissioning.download_individual_address(
            cast("XKNX", None), _NEW, domain_address=rf_domain, **kwargs
        )
        assert ("rf domain", rf_domain) in calls
        assert ("rf address", _NEW) in calls


def test_the_medium_by_the_domain_address() -> None:
    medium = commissioning._domain_medium  # pyright: ignore[reportPrivateUsage]
    assert medium(None, None) is KnxMedium.TP
    assert medium(bytes(6), None) is KnxMedium.RF
    assert medium(MULTICAST, SERIAL) is KnxMedium.IP
    assert medium(MULTICAST, None) is KnxMedium.TP
    assert medium(bytes(2), SERIAL) is KnxMedium.TP
    system_broadcast = commissioning._system_broadcast  # pyright: ignore[reportPrivateUsage]
    assert system_broadcast(KnxMedium.RF, False)
    assert system_broadcast(KnxMedium.IP, True)
    assert not system_broadcast(KnxMedium.IP, False)
    assert not system_broadcast(KnxMedium.TP, True)

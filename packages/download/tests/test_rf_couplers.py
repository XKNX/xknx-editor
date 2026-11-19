"""Tests for the routing of system broadcasts in KNX TP1/RF Media Couplers."""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress

import xknxeditor.download.rf_couplers as rf_couplers
import xknxeditor.download.session as session
from xknxeditor.download.rf_couplers import (
    CouplerRoutingError,
    RFCouplers,
    RFSBCMode,
    set_rf_sbc_control,
    system_broadcast_routing,
)

from .conftest import FakeDevice

_COUPLER = IndividualAddress("1.2.0")
_OTHER = IndividualAddress("1.3.0")


def _coupler(monkeypatch: pytest.MonkeyPatch, device: FakeDevice | None) -> list[Any]:
    opened: list[Any] = []

    class _Manager:
        async def open(self) -> FakeDevice:
            if device is None:
                raise ManagementConnectionError("no answer")
            return device

        async def close(self) -> None:
            opened.append("closed")

    def fake_session(
        xknx: XKNX, address: IndividualAddress, security: object
    ) -> _Manager:
        opened.append(address)
        return _Manager()

    monkeypatch.setattr(session, "management_session", fake_session)
    return opened


def _media_coupler() -> FakeDevice:
    # Device Object, the Router Object of the Primary Side, the RF Medium Object, the Router
    # Object of the Secondary Side
    return FakeDevice(object_types={0: 0, 1: 6, 2: 19, 3: 6}, descriptor=0x2920)


async def test_the_rf_side_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    device = _media_coupler()
    opened = _coupler(monkeypatch, device)
    mode = await set_rf_sbc_control(
        cast("XKNX", None), _COUPLER, RFSBCMode.SYSTEM_BROADCAST
    )
    assert mode is RFSBCMode.SYSTEM_BROADCAST
    # PID_RF_SBC_CONTROL of the second Router Object
    assert device.function_properties[(3, 112)] == b"\x02"
    assert opened == [_COUPLER, "closed"]


async def test_a_refused_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    device = _media_coupler()
    device.function_property_return_code = 3
    _coupler(monkeypatch, device)
    with pytest.raises(
        CouplerRoutingError, match=r"1\.2\.0 could not .* refused with return code 0x03"
    ):
        await set_rf_sbc_control(cast("XKNX", None), _COUPLER, RFSBCMode.DISABLED)


async def test_a_coupler_that_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _coupler(monkeypatch, None)
    with pytest.raises(
        CouplerRoutingError, match=r"could not be set .*: disabled: no answer"
    ):
        await set_rf_sbc_control(cast("XKNX", None), _COUPLER, RFSBCMode.DISABLED)
    # a coupler without an RF side
    _coupler(monkeypatch, FakeDevice(object_types={0: 0, 1: 6}))
    with pytest.raises(CouplerRoutingError):
        await set_rf_sbc_control(cast("XKNX", None), _COUPLER, RFSBCMode.DISABLED)


def _recorded(
    monkeypatch: pytest.MonkeyPatch, failing: set[tuple[IndividualAddress, RFSBCMode]]
) -> list[tuple[IndividualAddress, RFSBCMode]]:
    calls: list[tuple[IndividualAddress, RFSBCMode]] = []

    async def fake_set(
        xknx: XKNX, coupler: IndividualAddress, mode: RFSBCMode
    ) -> RFSBCMode:
        calls.append((coupler, mode))
        if (coupler, mode) in failing:
            raise CouplerRoutingError(coupler, "failed")
        return mode

    monkeypatch.setattr(rf_couplers, "set_rf_sbc_control", fake_set)
    return calls


async def test_the_routing_while_the_addresses_are_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _recorded(monkeypatch, set())
    async with system_broadcast_routing(
        cast("XKNX", None), RFCouplers(_COUPLER, (_OTHER,))
    ):
        calls.append((IndividualAddress("0.0.0"), RFSBCMode.BROADCAST))
    assert calls == [
        (_OTHER, RFSBCMode.DISABLED),
        (_COUPLER, RFSBCMode.SYSTEM_BROADCAST),
        (IndividualAddress("0.0.0"), RFSBCMode.BROADCAST),
        (_COUPLER, RFSBCMode.DISABLED),
    ]
    # without couplers nothing is set
    calls.clear()
    async with system_broadcast_routing(cast("XKNX", None), None):
        pass
    assert calls == []


async def test_a_coupler_that_can_not_be_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _recorded(monkeypatch, {(_OTHER, RFSBCMode.DISABLED)})
    with pytest.raises(CouplerRoutingError, match=r"1\.3\.0"):
        async with system_broadcast_routing(
            cast("XKNX", None), RFCouplers(_COUPLER, (_OTHER,))
        ):
            pytest.fail("the addresses must not be written")
    # the coupler towards the device was not touched
    assert calls == [(_OTHER, RFSBCMode.DISABLED)]


async def test_a_coupler_that_can_not_be_set_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _recorded(monkeypatch, {(_COUPLER, RFSBCMode.DISABLED)})
    with caplog.at_level(logging.WARNING):
        async with system_broadcast_routing(cast("XKNX", None), RFCouplers(_COUPLER)):
            pass
    # it ends by itself after 10 minutes: only a warning
    assert "1.2.0 could not be set to route system broadcasts: failed" in caplog.text

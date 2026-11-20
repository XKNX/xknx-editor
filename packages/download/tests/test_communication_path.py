"""Tests for the communication path to a device through the couplers of the installation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress

import xknxeditor.download.communication_path as communication_path
import xknxeditor.download.session as session
from xknxeditor.download.communication_path import (
    CommunicationPathError,
    CommunicationPaths,
    Coupler,
    CouplerAction,
    Installation,
    KnxMedium,
    RFMode,
    SecureSystemBroadcastBlocked,
    broadcast_path,
    communication_paths,
    release_installation,
    routed_manager,
    set_installation,
)

from .conftest import FakeDevice

_TOOL = IndividualAddress("1.1.250")
_DEVICE = IndividualAddress("1.2.5")
# The tool's line coupler (passed upwards), the device's (passed downwards).
_UP = IndividualAddress("1.1.0")
_DOWN = IndividualAddress("1.2.0")


def _xknx() -> XKNX:
    # A real connection object: it takes no weak reference and no attribute it has no slot
    # for, which a stand-in would hide.
    xknx = XKNX()
    xknx.current_address = _TOOL
    return xknx


def _media_coupler() -> FakeDevice:
    # Device Object, the Router Object of the Primary Side, the RF Medium Object, the Router
    # Object of the Secondary Side
    return FakeDevice(object_types={0: 0, 1: 6, 2: 19, 3: 6}, descriptor=0x2920)


def _couplers(
    monkeypatch: pytest.MonkeyPatch, devices: dict[IndividualAddress, FakeDevice | None]
) -> list[tuple[IndividualAddress, Any]]:
    """Fake sessions to the couplers: ``None`` for one that does not answer. Returns the
    sessions opened, with the security they were opened with."""
    opened: list[tuple[IndividualAddress, Any]] = []

    class _Manager:
        def __init__(self, address: IndividualAddress) -> None:
            self.address = address

        async def open(self) -> FakeDevice:
            device = devices[self.address]
            if device is None:
                raise ManagementConnectionError("no answer")
            return device

        async def close(self) -> None:
            pass

    def fake_session(
        xknx: XKNX,
        address: IndividualAddress,
        security: object,
        *,
        connectionless: bool = False,
        routed: bool = True,
    ) -> _Manager:
        assert not routed
        opened.append((address, security))
        return _Manager(address)

    monkeypatch.setattr(session, "management_session", fake_session)
    return opened


def _installation(
    *couplers: Coupler, rf_connection: bool = False, **kwargs: Any
) -> Installation:
    return Installation(
        {coupler.address: coupler for coupler in couplers},
        **{
            "connection_medium": KnxMedium.RF if rf_connection else KnxMedium.TP,
            **kwargs,
        },
    )


def test_what_the_couplers_are_set_to() -> None:
    proxy_up = Coupler(_UP, block_unicast=True, block_broadcast=True)
    media = Coupler(_DOWN, secondary=KnxMedium.RF, block_unicast=True)
    paths = CommunicationPaths(_xknx(), _installation(proxy_up, media))
    unicast = paths.unicast(_DEVICE)
    assert unicast is not None
    needs = communication_path._needs(unicast)
    # each blocking coupler lets the tool reach the device; the nearer one also the further
    # coupler it sets up
    assert needs[_UP].actions == CouplerAction.UNICAST_WHITE_LIST
    assert needs[_UP].white_list == {_DEVICE, _DOWN}
    assert needs[_DOWN].white_list == {_DEVICE}
    broadcast = paths.broadcast(_DEVICE, system_broadcast=True)
    assert broadcast is not None
    needs = communication_path._needs(broadcast)
    assert needs[_UP].actions == (
        CouplerAction.UNICAST_WHITE_LIST | CouplerAction.BROADCAST_CONTROL
    )
    # the last coupler, towards the RF device, makes the broadcasts system broadcasts
    assert needs[_DOWN].actions == (
        CouplerAction.UNICAST_WHITE_LIST | CouplerAction.SYSTEM_BROADCAST
    )
    no_sbc = paths.broadcast(_DEVICE, system_broadcast=False)
    assert no_sbc is not None
    assert communication_path._needs(no_sbc)[_DOWN].actions & CouplerAction.BROADCAST


def test_a_coupler_of_the_tools_own_rf_line() -> None:
    local = Coupler(_UP, secondary=KnxMedium.RF)
    remote = Coupler(_DOWN, secondary=KnxMedium.RF)
    paths = CommunicationPaths(
        _xknx(), _installation(local, remote, rf_connection=True)
    )
    request = paths.broadcast(_DEVICE, system_broadcast=True)
    assert request is not None
    needs = communication_path._needs(request)
    # passed upwards: only broadcasts
    assert needs[_UP].actions == CouplerAction.BROADCAST
    assert needs[_DOWN].actions == CouplerAction.SYSTEM_BROADCAST
    # the remote coupler makes them system broadcasts: the tool sends broadcasts
    assert not paths.local_system_broadcast(request)


def test_without_anything_to_set() -> None:
    paths = CommunicationPaths(_xknx(), _installation(Coupler(_UP), Coupler(_DOWN)))
    assert paths.unicast(_DEVICE) is None
    assert paths.broadcast(_DEVICE, system_broadcast=True) is None
    # a device on the tool's own line passes no coupler
    assert paths.unicast(IndividualAddress("1.1.7")) is None
    # an RF connection reaches an RF device on its own line by system broadcasts
    assert not paths.local_system_broadcast(None)
    paths.installation = _installation(rf_connection=True)
    assert paths.local_system_broadcast(None)


def test_the_local_system_broadcasts() -> None:
    paths = CommunicationPaths(
        _xknx(), _installation(Coupler(_DOWN, block_broadcast=True), rf_connection=True)
    )
    request = paths.broadcast(_DEVICE, system_broadcast=True)
    assert paths.local_system_broadcast(request)
    plain = paths.broadcast(_DEVICE, system_broadcast=False)
    assert not paths.local_system_broadcast(plain)


def test_the_ranges_of_a_white_list() -> None:
    assert communication_path._ranges([5, 3, 4, 9, 10, 1]) == [(1, 1), (3, 3), (9, 2)]
    assert communication_path._ranges([]) == []


async def test_a_media_coupler_routes_the_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _media_coupler()
    opened = _couplers(monkeypatch, {_DOWN: device})
    xknx = _xknx()
    set_installation(xknx, _installation(Coupler(_DOWN, secondary=KnxMedium.RF)))
    async with broadcast_path(xknx, _DEVICE, system_broadcast=True) as local:
        assert not local.system_broadcast
        # PID_RF_SBC_CONTROL of the second Router Object
        assert device.function_properties[(3, 112)] == b"\x02"
    assert device.function_properties[(3, 112)] == b"\x00"
    assert [address for address, _security in opened] == [_DOWN, _DOWN]
    paths = communication_paths(xknx)
    assert paths is not None
    assert paths._retrigger is None


async def test_a_coupler_with_the_security_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _media_coupler()
    security = object()
    opened = _couplers(monkeypatch, {_DOWN: device})
    xknx = _xknx()
    set_installation(
        xknx,
        _installation(
            Coupler(
                _DOWN,
                secondary=KnxMedium.RF,
                security=cast("Any", security),
                block_unicast=True,
                block_broadcast=True,
            )
        ),
    )
    async with broadcast_path(xknx, _DEVICE, system_broadcast=True):
        pass
    user = _TOOL.raw.to_bytes(2, "big")
    assert device.ext_function_commands == [
        # the white list: the device
        (6, 1, 83, b"\x00\x00" + user + _DEVICE.raw.to_bytes(2, "big") + b"\x00\x01"),
        # broadcasts on both sides, system broadcasts onto RF
        (6, 1, 84, b"\x00\x00\x01"),
        (6, 2, 84, b"\x00\x00\x01"),
        (6, 2, 84, b"\x00\x01\x01"),
        # set back
        (6, 1, 83, b"\x00\x01" + user),
        (6, 1, 84, b"\x00\x00\x00"),
        (6, 2, 84, b"\x00\x00\x00"),
        (6, 2, 84, b"\x00\x01\x00"),
    ]
    # reached secured with its own key
    assert opened[0] == (_DOWN, security)


async def test_the_white_list_of_overlapping_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice()
    _couplers(monkeypatch, {_DOWN: device})
    xknx = _xknx()
    paths = CommunicationPaths(xknx, _installation(Coupler(_DOWN, block_unicast=True)))
    first = paths.unicast(_DEVICE)
    second = paths.unicast(IndividualAddress("1.2.6"))
    assert first is not None and second is not None
    await paths.add(first)
    await paths.add(second)
    user = _TOOL.raw.to_bytes(2, "big")
    added = [data for *_target, data in device.ext_function_commands]
    # the second only adds its own device
    assert added == [
        b"\x00\x00" + user + _DEVICE.raw.to_bytes(2, "big") + b"\x00\x01",
        b"\x00\x00"
        + user
        + IndividualAddress("1.2.6").raw.to_bytes(2, "big")
        + b"\x00\x01",
    ]
    await paths.remove(first)
    # still needed for the second: not revoked, only retriggered
    assert device.ext_function_commands[2] == (6, 1, 83, b"\x00\x02" + user)
    await paths.remove(second)
    assert device.ext_function_commands[-1][3] == b"\x00\x01" + user


async def test_the_couplers_get_their_settings_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice()
    _couplers(monkeypatch, {_DOWN: device})
    monkeypatch.setattr(communication_path, "RETRIGGER_INTERVAL_S", 0.01)
    xknx = _xknx()
    paths = CommunicationPaths(
        xknx, _installation(Coupler(_DOWN, block_unicast=True, block_broadcast=True))
    )
    request = paths.broadcast(_DEVICE, system_broadcast=False)
    assert request is not None
    await paths.add(request)
    user = _TOOL.raw.to_bytes(2, "big")
    for _ in range(100):
        if (6, 1, 83, b"\x00\x02" + user) in device.ext_function_commands:
            break
        await asyncio.sleep(0.01)
    # the white list is retriggered, the broadcast routing enabled again
    assert (6, 1, 83, b"\x00\x02" + user) in device.ext_function_commands
    assert device.ext_function_commands.count((6, 1, 84, b"\x00\x00\x01")) >= 2
    await paths.remove(request)
    assert paths._retrigger is None


async def test_a_retrigger_that_fails_is_only_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    devices: dict[IndividualAddress, FakeDevice | None] = {_DOWN: FakeDevice()}
    _couplers(monkeypatch, devices)
    monkeypatch.setattr(communication_path, "RETRIGGER_INTERVAL_S", 0.01)
    paths = CommunicationPaths(
        _xknx(), _installation(Coupler(_DOWN, block_unicast=True))
    )
    request = paths.unicast(_DEVICE)
    assert request is not None
    await paths.add(request)
    devices[_DOWN] = None
    with caplog.at_level(logging.WARNING):
        for _ in range(100):
            if "could not be opened" in caplog.text:
                break
            await asyncio.sleep(0.01)
        await paths.remove(request)
    assert "The communication path through the coupler 1.2.0" in caplog.text


async def test_a_coupler_that_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    first = FakeDevice()
    _couplers(monkeypatch, {_UP: first, _DOWN: None})
    paths = CommunicationPaths(
        _xknx(),
        _installation(
            Coupler(_UP, block_unicast=True), Coupler(_DOWN, block_unicast=True)
        ),
    )
    request = paths.unicast(_DEVICE)
    assert request is not None
    with pytest.raises(
        CommunicationPathError, match=r"coupler 1\.2\.0 could not be opened: no answer"
    ) as caught:
        await paths.add(request)
    assert caught.value.coupler == _DOWN
    # the one prepared before is set back
    user = _TOOL.raw.to_bytes(2, "big")
    assert first.ext_function_commands[-1] == (6, 1, 83, b"\x00\x01" + user)
    assert paths._retrigger is None


async def test_the_steps_done_are_undone_when_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a media coupler without an RF Router Object: its white list is revoked again
    device = FakeDevice(object_types={0: 0, 1: 6})
    _couplers(monkeypatch, {_DOWN: device})
    paths = CommunicationPaths(
        _xknx(),
        _installation(Coupler(_DOWN, secondary=KnxMedium.RF, block_unicast=True)),
    )
    # the white list comes after the RF side: nothing to undo but the white list itself
    request = paths.broadcast(_DEVICE, system_broadcast=True)
    assert request is not None
    with pytest.raises(CommunicationPathError):
        await paths.add(request)
    assert device.ext_function_commands == []


async def test_a_refusal_is_only_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    device = FakeDevice()
    device.function_property_return_code = 0xF2
    _couplers(monkeypatch, {_DOWN: device})
    paths = CommunicationPaths(
        _xknx(), _installation(Coupler(_DOWN, block_broadcast=True))
    )
    request = paths.broadcast(_DEVICE, system_broadcast=False)
    assert request is not None
    with caplog.at_level(logging.WARNING):
        await paths.add(request)
    assert (
        "Coupler 1.2.0: function property 84 with 000001 returned 0xf2" in caplog.text
    )
    await paths.remove(request)


async def test_a_coupler_that_can_not_be_set_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    devices: dict[IndividualAddress, FakeDevice | None] = {_DOWN: _media_coupler()}
    _couplers(monkeypatch, devices)
    xknx = _xknx()
    set_installation(xknx, _installation(Coupler(_DOWN, secondary=KnxMedium.RF)))
    with caplog.at_level(logging.WARNING):
        async with broadcast_path(xknx, _DEVICE, system_broadcast=True):
            devices[_DOWN] = None
    # its setting ends by itself: only a warning
    assert "1.2.0 could not be opened: no answer" in caplog.text


async def test_a_session_keeps_the_path_open(monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice()
    _couplers(monkeypatch, {_DOWN: device})
    xknx = _xknx()

    class _Inner:
        device_sequence_number = 7

        def __init__(self) -> None:
            self.opened = 0

        async def open(self) -> object:
            self.opened += 1
            if self.opened == 2:
                raise ManagementConnectionError("no answer")
            return "connection"

        async def close(self) -> None:
            pass

    inner = _Inner()
    # without couplers, the session is as it is
    assert routed_manager(xknx, _DEVICE, cast("Any", inner)) is inner
    set_installation(xknx, _installation(Coupler(_DOWN, block_unicast=True)))
    # nothing to set on the way to another device
    assert routed_manager(xknx, IndividualAddress("1.1.7"), cast("Any", inner)) is inner
    manager = routed_manager(xknx, _DEVICE, cast("Any", inner))
    assert manager is not inner
    # the session's own properties
    assert getattr(manager, "device_sequence_number") == 7  # noqa: B009
    assert await manager.open() == "connection"
    assert len(device.ext_function_commands) == 1
    await manager.close()
    assert len(device.ext_function_commands) == 2
    # a connection that fails sets the path back at once
    with pytest.raises(ManagementConnectionError):
        await manager.open()
    assert len(device.ext_function_commands) == 4
    await manager.close()
    assert len(device.ext_function_commands) == 4


async def test_the_installation_can_be_taken_away() -> None:
    xknx = _xknx()
    set_installation(xknx, None)
    assert communication_paths(xknx) is None
    set_installation(xknx, _installation(Coupler(_DOWN, block_unicast=True)))
    paths = communication_paths(xknx)
    assert paths is not None
    set_installation(xknx, _installation())
    assert communication_paths(xknx) is paths
    set_installation(xknx, None)
    assert paths.unicast(_DEVICE) is None
    # without couplers the services go as they were asked for
    async with broadcast_path(xknx, _DEVICE, system_broadcast=True) as local:
        assert not local.system_broadcast
    other = _xknx()
    async with broadcast_path(other, _DEVICE, system_broadcast=True) as local:
        assert local.system_broadcast
    # a stand-in for a connection that has none
    assert communication_paths(cast("XKNX", None)) is None


def test_the_paths_end_with_the_connection() -> None:
    xknx = _xknx()
    set_installation(xknx, _installation(Coupler(_DOWN, block_unicast=True)))
    assert communication_paths(xknx) is not None
    # another connection has its own, none until it is given couplers
    other = _xknx()
    assert communication_paths(other) is None
    release_installation(other)
    assert communication_paths(xknx) is not None
    release_installation(xknx)
    assert communication_paths(xknx) is None
    release_installation(xknx)


def test_the_path_of_the_installation() -> None:
    installation = _installation(Coupler(_UP), Coupler(IndividualAddress("1.0.0")))
    # the area coupler 1.0.0 is not passed within an area; 1.2.0 is unknown
    assert installation.path(_TOOL, _DEVICE) == ((installation.couplers[_UP], False),)


class _NoBroadcastControl(FakeDevice):
    """A coupler that does not answer PID_BC_ROUTING_TEMP."""

    async def request(self, payload: Any, expected: Any) -> Any:
        if getattr(payload, "property_id", None) == 84:
            raise ManagementConnectionError("no answer")
        return await super().request(payload, expected)


async def test_the_steps_done_are_set_back_when_a_later_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _NoBroadcastControl()
    _couplers(monkeypatch, {_DOWN: device})
    paths = CommunicationPaths(
        _xknx(),
        _installation(Coupler(_DOWN, block_unicast=True, block_broadcast=True)),
    )
    request = paths.broadcast(_DEVICE, system_broadcast=False)
    assert request is not None
    with pytest.raises(CommunicationPathError):
        await paths.add(request)
    user = _TOOL.raw.to_bytes(2, "big")
    # the white list was added, and revoked again
    assert [data[:2] for *_target, data in device.ext_function_commands] == [
        b"\x00\x00",
        b"\x00\x01",
    ]
    assert device.ext_function_commands[-1] == (6, 1, 83, b"\x00\x01" + user)


async def test_a_path_that_stays_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice()
    _couplers(monkeypatch, {_DOWN: device})
    paths = CommunicationPaths(
        _xknx(), _installation(Coupler(_DOWN, block_unicast=True))
    )
    request = paths.unicast(_DEVICE)
    assert request is not None
    await paths.add(request)
    await paths.add(request)
    # the second one needs nothing new
    assert len(device.ext_function_commands) == 1
    await paths.remove(request)
    await paths.remove(request)
    assert len(device.ext_function_commands) == 2


async def test_a_path_that_can_not_be_kept_for_the_others(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    devices: dict[IndividualAddress, FakeDevice | None] = {_DOWN: FakeDevice()}
    _couplers(monkeypatch, devices)
    paths = CommunicationPaths(
        _xknx(), _installation(Coupler(_DOWN, block_unicast=True))
    )
    first = paths.unicast(_DEVICE)
    second = paths.unicast(IndividualAddress("1.2.6"))
    assert first is not None and second is not None
    await paths.add(first)
    await paths.add(second)
    devices[_DOWN] = None
    with caplog.at_level(logging.WARNING):
        # the end of one operation does not fail it
        await paths.remove(first)
    assert "1.2.0 could not be opened" in caplog.text
    await paths.remove(second)


def test_a_session_without_its_inner_one() -> None:
    manager = communication_path._RoutedManager.__new__(
        communication_path._RoutedManager
    )
    with pytest.raises(AttributeError):
        _ = manager.device_sequence_number


async def test_a_coupler_with_access_protection_is_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice()
    _couplers(monkeypatch, {_DOWN: device})
    paths = CommunicationPaths(
        _xknx(),
        _installation(Coupler(_DOWN, block_unicast=True, access_key=0x11223344)),
    )
    request = paths.unicast(_DEVICE)
    assert request is not None
    await paths.add(request)
    await paths.remove(request)
    # before enabling and before setting back: its key, the free one, and its key again (both
    # get the same level, so the first wins and is presented last)
    key, free = 0x11223344, 0xFFFFFFFF
    assert device.authorize_keys == [key, free, key] * 2


# ---- IP routers, closing, restriction, RF Multi --------------------------------------------

_ROUTER = IndividualAddress("1.0.0")
_IP_DEVICE = IndividualAddress("0.0.5")


def _ip_router() -> FakeDevice:
    # Device Object, the Router Object of the Primary Side (IP), of the Secondary Side
    return FakeDevice(object_types={0: 0, 1: 6, 2: 6})


async def test_an_ip_router_routes_the_system_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = _ip_router()
    _couplers(monkeypatch, {_ROUTER: router})
    xknx = _xknx()
    router_coupler = Coupler(_ROUTER, primary=KnxMedium.IP, ip_system_broadcast=True)
    set_installation(xknx, _installation(router_coupler))
    # the tool on line 1.1 reaches the IP device on the backbone upwards through 1.0.0
    async with broadcast_path(
        xknx, _IP_DEVICE, system_broadcast=True, medium=KnxMedium.IP
    ) as path:
        # PID_IP_SBC_CONTROL of the first Router Object
        assert router.function_properties[(1, 120)] == b"\x00\x00\x01"
        # the tool is on TP: it sends broadcasts
        assert not path.system_broadcast
        await path.close_system_broadcast()
        assert router.function_properties[(1, 120)] == b"\x00\x00\x00"
    assert router.function_properties[(1, 120)] == b"\x00\x00\x00"
    # without system broadcasts nothing is set
    plain = _ip_router()
    _couplers(monkeypatch, {_ROUTER: plain})
    async with broadcast_path(
        xknx, _IP_DEVICE, system_broadcast=False, medium=KnxMedium.IP
    ):
        pass
    assert (1, 120) not in plain.function_properties


async def test_closing_keeps_what_else_is_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = _media_coupler()
    _couplers(monkeypatch, {_DOWN: media})
    xknx = _xknx()
    set_installation(
        xknx,
        _installation(Coupler(_DOWN, secondary=KnxMedium.RF, block_unicast=True)),
    )
    async with broadcast_path(xknx, _DEVICE, system_broadcast=True) as path:
        assert media.function_properties[(3, 112)] == b"\x02"
        await path.close_system_broadcast()
        # the media coupler routes broadcasts only now
        assert media.function_properties[(3, 112)] == b"\x01"
    assert media.function_properties[(3, 112)] == b"\x00"


async def test_closing_a_coupler_that_refuses(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    devices: dict[IndividualAddress, FakeDevice | None] = {_DOWN: _media_coupler()}
    _couplers(monkeypatch, devices)
    xknx = _xknx()
    set_installation(
        xknx,
        _installation(Coupler(_DOWN, secondary=KnxMedium.RF, block_unicast=True)),
    )
    with caplog.at_level(logging.WARNING):
        async with broadcast_path(xknx, _DEVICE, system_broadcast=True) as path:
            devices[_DOWN] = None
            await path.close_system_broadcast()
    assert "1.2.0 could not be opened" in caplog.text


def test_the_system_broadcasts_from_the_connection() -> None:
    paths = CommunicationPaths(_xknx(), _installation(connection_medium=KnxMedium.IP))
    # an IP connection sends them to an IP device that takes them, a PL one to PL devices
    assert paths.local_system_broadcast(None, medium=KnxMedium.IP)
    assert not paths.local_system_broadcast(
        None, system_broadcast=False, medium=KnxMedium.IP
    )
    assert not paths.local_system_broadcast(None, medium=KnxMedium.RF)
    paths.installation = _installation(connection_medium=KnxMedium.PL)
    assert paths.local_system_broadcast(None, medium=KnxMedium.PL)
    # never from TP1
    paths.installation = _installation()
    assert not paths.local_system_broadcast(None, medium=KnxMedium.TP)


def test_the_coupler_that_stops_secured_system_broadcasts() -> None:
    media = Coupler(_DOWN, secondary=KnxMedium.RF, secure_system_broadcast=True)
    paths = CommunicationPaths(_xknx(), _installation(media))
    request = paths.broadcast(_DEVICE, system_broadcast=True)
    # a media coupler passed downwards onto RF
    assert paths.secure_restriction(request) == _DOWN
    assert paths.secure_restriction(None) is None
    plain = paths.broadcast(_DEVICE, system_broadcast=False)
    assert paths.secure_restriction(plain) is None
    # a coupler that does not pass secured system broadcasts
    router = Coupler(_ROUTER, primary=KnxMedium.IP, ip_system_broadcast=True)
    paths.installation = _installation(router)
    request = paths.broadcast(_IP_DEVICE, system_broadcast=True, medium=KnxMedium.IP)
    assert paths.secure_restriction(request) == _ROUTER
    # an IP router passed upwards from TP1 onto IP
    paths.installation = _installation(
        Coupler(
            _ROUTER,
            primary=KnxMedium.IP,
            ip_system_broadcast=True,
            secure_system_broadcast=True,
        )
    )
    request = paths.broadcast(_IP_DEVICE, system_broadcast=True, medium=KnxMedium.IP)
    assert paths.secure_restriction(request) == _ROUTER
    # one passed upwards onto IP from another medium passes them
    paths.installation = _installation(
        Coupler(
            _ROUTER,
            primary=KnxMedium.IP,
            secondary=KnxMedium.RF,
            ip_system_broadcast=True,
            secure_system_broadcast=True,
        )
    )
    request = paths.broadcast(_IP_DEVICE, system_broadcast=True, medium=KnxMedium.IP)
    assert paths.secure_restriction(request) is None


_LOCAL = IndividualAddress("1.6.0")
_RF_TOOL = IndividualAddress("1.6.250")


class _RFXknx:
    def __init__(self) -> None:
        self.current_address = _RF_TOOL


async def test_the_rf_modes_towards_a_knx_rf_multi_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, towards = _media_coupler(), _media_coupler()
    opened = _couplers(monkeypatch, {_LOCAL: local, _DOWN: towards})
    xknx = cast("XKNX", _RFXknx())
    set_installation(
        xknx,
        _installation(
            Coupler(_LOCAL, secondary=KnxMedium.RF, rf_multi=True),
            Coupler(_DOWN, secondary=KnxMedium.RF, rf_multi=True),
            rf_connection=True,
            rf_multi_connection=True,
            rf_modes={_DEVICE: RFMode.MULTI_FAST},
        ),
    )

    class _Inner:
        async def open(self) -> object:
            return "connection"

        async def close(self) -> None:
            pass

    manager = routed_manager(xknx, _DEVICE, cast("Any", _Inner()))
    await manager.open()
    # the coupler of the connection's own line speaks RF Multi towards it, the one towards
    # the device the device's mode (PID 79 of the RF Router Object)
    assert local.function_properties[(3, 79)] == b"\x00\x00\x09"
    assert towards.function_properties[(3, 79)] == b"\x00\x00\x09"
    await manager.close()
    # every connection sets them again
    local.function_properties.clear()
    towards.function_properties.clear()
    await manager.open()
    await manager.close()
    assert local.function_properties[(3, 79)] == b"\x00\x00\x09"
    assert towards.function_properties[(3, 79)] == b"\x00\x00\x09"
    assert opened


def test_the_rf_modes_of_other_paths() -> None:
    paths = CommunicationPaths(
        cast("XKNX", _RFXknx()),
        _installation(
            Coupler(_LOCAL, secondary=KnxMedium.RF),
            Coupler(_DOWN, secondary=KnxMedium.RF),
            rf_connection=True,
            rf_modes={_DEVICE: RFMode.READY},
        ),
    )
    # Ready towards a coupler that does not speak RF Multi: nothing to set
    assert paths.unicast(_DEVICE) is None
    request = communication_path._Request(
        paths.path_to(_DEVICE).path, frozenset({_DEVICE})
    )
    assert paths._rf_mode_couplers(request) == []
    # a broadcast, a device without a mode, a path upwards
    assert paths._rf_mode_couplers(paths.path_to(_DEVICE)) == []
    assert (
        paths._rf_mode_couplers(communication_path._Request((), frozenset({_DEVICE})))
        == []
    )
    other = IndividualAddress("1.2.9")
    assert (
        paths._rf_mode_couplers(
            communication_path._Request(paths.path_to(other).path, frozenset({other}))
        )
        == []
    )
    upwards = communication_path._Request(
        ((Coupler(_LOCAL, secondary=KnxMedium.RF), False),), frozenset({_DEVICE})
    )
    assert paths._rf_mode_couplers(upwards) == []


async def test_setting_an_rf_mode(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    coupler = _media_coupler()
    _couplers(monkeypatch, {_DOWN: coupler})
    paths = CommunicationPaths(_xknx(), _installation())
    media = Coupler(_DOWN, secondary=KnxMedium.RF, rf_multi=True, access_key=0x1)
    with caplog.at_level(logging.INFO):
        await paths.set_rf_mode(
            media, unicast=RFMode.MULTI_SLOW, broadcast=RFMode.MULTI_SLOW
        )
    # broadcasts know Ready and Multi only
    assert coupler.function_properties[(3, 79)] == b"\x00\x00\x0a"
    assert coupler.function_properties[(3, 114)] == b"\x00\x00\x09"
    assert coupler.authorize_keys == [1, 0xFFFFFFFF, 1]
    assert "unicast MULTI_SLOW, broadcast MULTI_SLOW" in caplog.text
    await paths.set_rf_mode(media, broadcast=RFMode.READY)
    assert coupler.function_properties[(3, 114)] == b"\x00\x00\x24"
    _couplers(monkeypatch, {_DOWN: None})
    with pytest.raises(CommunicationPathError, match="no answer"):
        await paths.set_rf_mode(media, unicast=RFMode.READY)


async def test_switching_the_rf_mode_while_a_device_is_looked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coupler = _media_coupler()
    _couplers(monkeypatch, {_DOWN: coupler})
    xknx = _xknx()
    set_installation(
        xknx,
        _installation(Coupler(_DOWN, secondary=KnxMedium.RF, rf_multi=True)),
    )
    async with broadcast_path(xknx, _DEVICE, system_broadcast=True) as path:
        assert path.switch_rf_mode is not None
        await path.switch_rf_mode(True)
        assert coupler.function_properties[(3, 79)] == b"\x00\x00\x09"
        assert coupler.function_properties[(3, 114)] == b"\x00\x00\x09"
        await path.switch_rf_mode(False)
        assert coupler.function_properties[(3, 114)] == b"\x00\x00\x24"
    paths = communication_paths(xknx)
    assert paths is not None
    assert paths.rf_mode_switch(None) is None
    # a coupler without RF Multi, or a path upwards, has nothing to switch
    paths.installation = _installation(Coupler(_DOWN, secondary=KnxMedium.RF))
    assert paths.rf_mode_switch(paths.path_to(_DEVICE)) is None
    upwards = communication_path._Request(
        ((Coupler(_LOCAL, secondary=KnxMedium.RF, rf_multi=True), False),),
        frozenset({_DEVICE}),
    )
    assert paths.rf_mode_switch(upwards) is None


async def test_without_couplers_to_set_the_rf_mode_switch_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coupler = _media_coupler()
    _couplers(monkeypatch, {_DOWN: coupler})
    xknx = _xknx()
    # an RF connection on another domain behind a coupler without settings: none are needed
    set_installation(
        xknx,
        _installation(
            Coupler(_DOWN, primary=KnxMedium.TP, secondary=KnxMedium.TP),
        ),
    )
    async with broadcast_path(xknx, _DEVICE, system_broadcast=False) as path:
        assert path.switch_rf_mode is None
        assert path.secure_restriction is None
        await path.close_system_broadcast()


def test_the_rf_connection_of_an_installation() -> None:
    assert _installation(rf_connection=True).rf_connection
    assert not _installation().rf_connection


async def test_closing_with_another_path_open(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy, media = FakeDevice(), _media_coupler()
    _couplers(monkeypatch, {_UP: proxy, _DOWN: media})
    paths = CommunicationPaths(
        _xknx(),
        _installation(
            Coupler(_UP, block_broadcast=True),
            Coupler(_DOWN, secondary=KnxMedium.RF),
        ),
    )
    first = paths.broadcast(_DEVICE, system_broadcast=True)
    second = paths.broadcast(_DEVICE, system_broadcast=True)
    assert first is not None and second is not None
    await paths.add(first)
    await paths.add(second)
    await paths.close_system_broadcast(first)
    # still needed by the second: the media coupler keeps routing system broadcasts
    assert media.function_properties[(3, 112)] == b"\x02"
    await paths.close_system_broadcast(second)
    assert media.function_properties[(3, 112)] == b"\x01"
    await paths.remove(first)
    await paths.remove(second)


async def test_an_rf_connection_behind_a_coupler_without_rf_multi() -> None:
    paths = CommunicationPaths(
        cast("XKNX", _RFXknx()),
        _installation(Coupler(_LOCAL, secondary=KnxMedium.RF), rf_connection=True),
    )
    # nothing is set: the coupler does not speak RF Multi
    await paths._rf_multi_interface(paths.path_to(_DEVICE))
    assert not paths._rf_multi_set


def test_the_error_of_a_coupler_without_secured_system_broadcasts() -> None:
    error = SecureSystemBroadcastBlocked(_DOWN)
    assert error.coupler == _DOWN
    assert "coupler 1.2.0 does not pass secured system broadcasts" in str(error)


async def test_an_rf_multi_interface_on_the_devices_domain_switches_itself() -> None:
    switched: list[bool] = []

    async def switch(multi: bool) -> None:
        switched.append(multi)

    xknx = cast("XKNX", _RFXknx())
    set_installation(xknx, _installation(rf_connection=True, rf_multi_switch=switch))
    device = IndividualAddress("1.6.7")
    async with broadcast_path(xknx, device, system_broadcast=True) as path:
        assert path.system_broadcast
        assert path.switch_rf_mode is switch
    # not for a device of another medium
    async with broadcast_path(
        xknx, device, system_broadcast=False, medium=KnxMedium.TP
    ) as path:
        assert path.switch_rf_mode is None

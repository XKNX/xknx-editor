"""The communication path to a device through the couplers of the installation.

Couplers on the way can stop the frames the editor sends to a device:

- a KNX TP1/RF Media Coupler routes broadcasts and system broadcasts onto RF only while its
  PID_RF_SBC_CONTROL says so (KNX v01.10.01 - Resources 03.05.01 - §4.5.15.1), and a new RF
  device only receives system broadcasts (KNX v02.01.01 - Configuration Procedures 03.05.03 -
  §2.3.1.4);
- a coupler with the security extensions does not route point-to-point or broadcast messages
  onto a side whose PID_PTP_CONFIG or PID_BC_CONFIG is BLOCK, except to the partners of a
  Temporary Unicast White List (PID_PTP_ROUTING_TEMP) and while the temporary broadcast routing
  is enabled (PID_BC_ROUTING_TEMP); both end 60 s after they are set (KNX AN192 2.2.2.5,
  2.2.2.6, 2.3.1.3, 2.3.1.4).

Before the editor talks to a device (:func:`.session.management_session`) or reaches it by
broadcast (:func:`broadcast_path`), every coupler on the route from the connection gets what
the frames need, the one nearest to the connection first. They get it again every 30 s, and when
nothing needs them any more they are set back, in reverse order. Paths that are open at the same
time are merged. A coupler that can't be reached fails the operation
(:class:`CommunicationPathError`), and the couplers already prepared for it are set back; a
coupler that refuses a setting is only logged. Couplers off the route are left as they are.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum, IntEnum, IntFlag
from typing import TYPE_CHECKING, Any

from xknx.exceptions import XKNXException
from xknx.telegram import IndividualAddress

from .errors import DownloadError
from .route import route_steps

if TYPE_CHECKING:
    from xknx import XKNX

    from .data_secure import DeviceSecurity
    from .programmer import BusConnection, ConnectionManager, DeviceProgrammer

logger = logging.getLogger(__name__)

# The Router Object; instance 1 stands for the primary side of a coupler, 2 for the secondary
# one (KNX AN192 2.7.2, KNX v01.01.01 - Mask 2920h 06.02.42 - §1.2).
_ROUTER_OBJECT = 6
_PRIMARY = 1
_SECONDARY = 2
PID_PTP_ROUTING_TEMP = 83
PID_BC_ROUTING_TEMP = 84
PID_RF_SBC_CONTROL = 112
PID_IP_SBC_CONTROL = 120
# The RF mode of a TP1/RF Media Coupler towards KNX RF Multi devices, unicast and broadcast.
PID_RF_MULTI_UNICAST_CONTROL = 79
PID_RF_MULTI_BROADCAST_CONTROL = 114
# PID_PTP_ROUTING_TEMP services: add to the Temporary Unicast White List, revoke it, retrigger
# it; PID_BC_ROUTING_TEMP services: the broadcast and the system broadcast routing mode.
_ADD = 0x00
_REVOKE = 0x01
_RETRIGGER = 0x02
_BROADCAST_MODE = 0x00
_SYSTEM_BROADCAST_MODE = 0x01
# The couplers get their settings again this often; the temporary ones end after 60 s.
RETRIGGER_INTERVAL_S = 30.0


class KnxMedium(Enum):
    """The medium on a side of a coupler."""

    TP = "TP"
    PL = "PL"
    RF = "RF"
    IP = "IP"


class RFSBCMode(IntEnum):
    """Routing of system broadcast services onto KNX RF (03.05.01 §4.5.15.1, Table 27)."""

    DISABLED = 0
    BROADCAST = 1
    SYSTEM_BROADCAST = 2


class CouplerAction(IntFlag):
    """What a coupler on the path is set to do."""

    NONE = 0
    # A TP1/RF Media Coupler routes broadcasts onto RF, or system broadcasts as well.
    BROADCAST = 1
    SYSTEM_BROADCAST = 2
    # A coupler that blocks point-to-point messages passes those of the connection to these.
    UNICAST_WHITE_LIST = 4
    # A coupler that blocks broadcasts passes them for a while.
    BROADCAST_CONTROL = 8


class CommunicationPathError(DownloadError):
    """A coupler on the path to a device could not be prepared: ``coupler``, and ``reason``."""

    def __init__(self, coupler: IndividualAddress, reason: str) -> None:
        super().__init__(
            f"The communication path through the coupler {coupler} could not be opened: "
            f"{reason}"
        )
        self.coupler = coupler
        self.reason = reason


class SecureSystemBroadcastBlocked(DownloadError):
    """The domain address of a secured device (the backbone key of an IP device) can't be
    written: ``coupler`` on the way does not pass secured system broadcasts."""

    def __init__(self, coupler: IndividualAddress) -> None:
        super().__init__(
            f"The domain address of this secure device can't be written: the coupler "
            f"{coupler} does not pass secured system broadcasts"
        )
        self.coupler = coupler


@dataclass(frozen=True, slots=True)
class Coupler:
    """A coupler of the installation: the media on its primary and its secondary side, the
    Data Secure material it is reached with, whether it blocks point-to-point and broadcast
    messages (a coupler with the security extensions used as Security Proxy), and the access
    key it is authorized with (the project's; the free one: none)."""

    address: IndividualAddress
    primary: KnxMedium = KnxMedium.TP
    secondary: KnxMedium = KnxMedium.TP
    security: DeviceSecurity | None = None
    block_unicast: bool = False
    block_broadcast: bool = False
    access_key: int = 0xFFFFFFFF
    # A KNXnet/IP router that routes system broadcasts onto IP while PID_IP_SBC_CONTROL says
    # so (its application program's SupportsIpSystemBroadcast).
    ip_system_broadcast: bool = False
    # It passes secured system broadcasts (its application program is secure enabled).
    secure_system_broadcast: bool = False
    # A TP1/RF Media Coupler that speaks KNX RF Multi (its RFTxCapabilities are not Ready).
    rf_multi: bool = False


class RFMode(IntEnum):
    """The KNX RF mode a media coupler uses towards a device: unicast (PID 79) and broadcast
    (PID 114) values of its RF Router Object."""

    READY = 0x24
    MULTI_FAST = 0x09
    MULTI_SLOW = 0x0A


@dataclass(frozen=True)
class Installation:
    """The couplers the editor knows of, by their individual address; the medium of the
    connection and whether it is a KNX RF Multi interface; the RF mode of the KNX RF devices
    that speak KNX RF Multi."""

    couplers: Mapping[IndividualAddress, Coupler] = field(
        default_factory=dict[IndividualAddress, Coupler]
    )
    connection_medium: KnxMedium = KnxMedium.TP
    rf_multi_connection: bool = False
    rf_modes: Mapping[IndividualAddress, RFMode] = field(
        default_factory=dict[IndividualAddress, RFMode]
    )

    @property
    def rf_connection(self) -> bool:
        """Whether the connection is on KNX RF."""
        return self.connection_medium is KnxMedium.RF

    def path(
        self, interface: IndividualAddress, target: IndividualAddress
    ) -> tuple[tuple[Coupler, bool], ...]:
        """The known couplers from ``interface`` to ``target``, each with whether the frames
        pass it downwards (see :func:`.route.route_steps`)."""
        return tuple(
            (self.couplers[address], downwards)
            for address, downwards in route_steps(interface, target)
            if address in self.couplers
        )


@dataclass(eq=False)
class _Request:
    """What one operation needs: its path, the devices it addresses point-to-point, whether it
    reaches its target by broadcast (or system broadcast) and the medium of the target. Its
    system broadcast routing can be closed before it ends."""

    path: tuple[tuple[Coupler, bool], ...]
    targets: frozenset[IndividualAddress]
    broadcast: bool = False
    system_broadcast: bool = False
    medium: KnxMedium = KnxMedium.TP
    system_broadcast_closed: bool = False


@dataclass(slots=True)
class _Need:
    coupler: Coupler
    actions: CouplerAction = CouplerAction.NONE
    white_list: set[IndividualAddress] = field(default_factory=set[IndividualAddress])


def _needs(request: _Request) -> dict[IndividualAddress, _Need]:
    """What each coupler on the request's path is set to do, in path order.

    A coupler that blocks point-to-point messages lets the connection reach the targets and
    the couplers further on that are set up as well. For a broadcast, every TP1/RF Media
    Coupler routes broadcasts onto RF, the last one towards the target system broadcasts when
    they are asked for, as does the last one passed upwards when it is a KNXnet/IP router that
    routes system broadcasts onto IP; a coupler that blocks broadcasts passes them. Once the
    system broadcast routing of the request is closed it asks for broadcasts only."""
    path = request.path
    needs = {coupler.address: _Need(coupler) for coupler, _downwards in path}
    for coupler, _downwards in path:
        if coupler.block_unicast:
            _add_white_list(needs[coupler.address], request.targets)
    system_broadcast = request.system_broadcast and not request.system_broadcast_closed
    if request.broadcast:
        for position, (coupler, downwards) in enumerate(path):
            need = needs[coupler.address]
            leaf = position == len(path) - 1
            if (
                leaf
                and not downwards
                and coupler.primary is KnxMedium.IP
                and coupler.ip_system_broadcast
                and system_broadcast
            ):
                need.actions |= CouplerAction.SYSTEM_BROADCAST
            elif coupler.secondary is KnxMedium.RF:
                need.actions |= (
                    CouplerAction.SYSTEM_BROADCAST
                    if leaf and downwards and system_broadcast
                    else CouplerAction.BROADCAST
                )
            if coupler.block_broadcast:
                need.actions |= CouplerAction.BROADCAST_CONTROL
    for position, (coupler, _downwards) in enumerate(path):
        if coupler.block_unicast:
            further = {
                other.address
                for other, _ in path[position + 1 :]
                if needs[other.address].actions
            }
            _add_white_list(needs[coupler.address], further)
    return needs


def _add_white_list(need: _Need, addresses: Iterable[IndividualAddress]) -> None:
    partners = {address for address in addresses if address != need.coupler.address}
    if partners:
        need.actions |= CouplerAction.UNICAST_WHITE_LIST
        need.white_list |= partners


def _merge(requests: Iterable[_Request]) -> dict[IndividualAddress, _Need]:
    """The needs of all ``requests`` together, each coupler where it first comes."""
    merged: dict[IndividualAddress, _Need] = {}
    for request in requests:
        for address, need in _needs(request).items():
            into = merged.setdefault(address, _Need(need.coupler))
            into.actions |= need.actions
            into.white_list |= need.white_list
    return {address: need for address, need in merged.items() if need.actions}


def _ranges(addresses: Iterable[int]) -> list[tuple[int, int]]:
    """Sorted raw addresses as ranges of consecutive ones: ``(start, count)``."""
    ranges: list[tuple[int, int]] = []
    for address in sorted(set(addresses)):
        if ranges and ranges[-1][0] + ranges[-1][1] == address:
            ranges[-1] = (ranges[-1][0], ranges[-1][1] + 1)
        else:
            ranges.append((address, 1))
    return ranges


@dataclass(frozen=True, slots=True)
class _Applied:
    coupler: Coupler
    actions: CouplerAction
    white_list: frozenset[IndividualAddress]


class CommunicationPaths:
    """The communication paths open on one connection (see the module docstring)."""

    def __init__(self, xknx: XKNX, installation: Installation) -> None:
        self._xknx = xknx
        self.installation = installation
        self._requests: list[_Request] = []
        self._applied: dict[IndividualAddress, _Applied] = {}
        # The partners each coupler's Temporary Unicast White List has already.
        self._added: dict[IndividualAddress, set[IndividualAddress]] = {}
        self._lock = asyncio.Lock()
        self._retrigger: asyncio.Task[None] | None = None
        # The couplers of the connection's own RF line that got their RF mode towards it.
        self._rf_multi_set: set[IndividualAddress] = set()

    def unicast(self, target: IndividualAddress) -> _Request | None:
        """The request to talk to ``target`` point-to-point; None when no coupler on the way
        needs anything for it."""
        return self._request(target, broadcast=False, system_broadcast=False)

    def broadcast(
        self,
        target: IndividualAddress,
        *,
        system_broadcast: bool,
        medium: KnxMedium = KnxMedium.RF,
    ) -> _Request | None:
        """The request to reach ``target`` on ``medium`` by broadcast (or system broadcast)
        and then point-to-point; None when no coupler on the way needs anything for it."""
        return self._request(
            target, broadcast=True, system_broadcast=system_broadcast, medium=medium
        )

    def _request(
        self,
        target: IndividualAddress,
        *,
        broadcast: bool,
        system_broadcast: bool,
        medium: KnxMedium = KnxMedium.TP,
    ) -> _Request | None:
        path = self.installation.path(self._xknx.current_address, target)
        request = _Request(
            path, frozenset({target}), broadcast, system_broadcast, medium
        )
        if not any(need.actions for need in _needs(request).values()) and not (
            self._rf_mode_couplers(request)
        ):
            return None
        return request

    def path_to(self, target: IndividualAddress) -> _Request:
        """The request of a broadcast to ``target``, also when no coupler needs anything."""
        path = self.installation.path(self._xknx.current_address, target)
        return _Request(path, frozenset({target}), broadcast=True)

    def local_system_broadcast(
        self,
        request: _Request | None,
        *,
        system_broadcast: bool = True,
        medium: KnxMedium = KnxMedium.RF,
    ) -> bool:
        """Whether the services of a broadcast that asks for ``system_broadcast`` towards a
        device on ``medium`` (of ``request``, when there is one) go as system broadcasts from
        the connection: only when the connection is on that medium and not on TP1, and on
        KNX RF only when no coupler on the path makes them system broadcasts itself."""
        if request is not None:
            system_broadcast, medium = request.system_broadcast, request.medium
        connection = self.installation.connection_medium
        if (
            not system_broadcast
            or connection is KnxMedium.TP
            or connection is not medium
        ):
            return False
        if medium is not KnxMedium.RF or request is None:
            return True
        return not any(
            need.actions & CouplerAction.SYSTEM_BROADCAST
            and need.coupler.secondary is KnxMedium.RF
            for need in _needs(request).values()
        )

    def secure_restriction(self, request: _Request | None) -> IndividualAddress | None:
        """The coupler that stops a secured system broadcast of ``request``: the last coupler
        towards the device, when the services don't go as system broadcasts from the
        connection and it is a media coupler passed downwards onto RF or an IP router passed
        upwards from TP1 onto IP, or one that does not pass secured system broadcasts."""
        if (
            request is None
            or not request.path
            or not request.system_broadcast
            or self.local_system_broadcast(request)
        ):
            return None
        coupler, downwards = request.path[-1]
        if not coupler.secure_system_broadcast or (
            (downwards and coupler.secondary is KnxMedium.RF)
            or (
                not downwards
                and coupler.primary is KnxMedium.IP
                and coupler.secondary is KnxMedium.TP
            )
        ):
            return coupler.address
        return None

    async def add(self, request: _Request) -> None:
        """Prepare the couplers for ``request``; raises :class:`CommunicationPathError` when
        one can't be reached, after setting back those this prepared."""
        async with self._lock:
            await self._rf_multi_interface(request)
            await self._apply(_merge([*self._requests, request]))
            self._requests.append(request)
            if self._retrigger is None:
                self._retrigger = asyncio.create_task(self._retrigger_loop())
            await self._rf_mode(request)

    async def close_system_broadcast(self, request: _Request) -> None:
        """End the system broadcast routing ``request`` asked for, on the coupler towards the
        device, before ``request`` ends: a media coupler routes broadcasts only, an IP router
        no system broadcasts any more. A coupler that refuses is only warned about."""
        async with self._lock:
            request.system_broadcast_closed = True
            needs = _merge(self._requests)
            for address, applied in list(self._applied.items()):
                if not applied.actions & CouplerAction.SYSTEM_BROADCAST:
                    continue
                need = needs.get(address)
                if need is None:
                    await self._disable(address)
                    continue
                if need.actions & CouplerAction.SYSTEM_BROADCAST:
                    continue
                try:
                    await self._set(
                        _Applied(applied.coupler, need.actions, applied.white_list),
                        enable=True,
                        close_system_broadcast=True,
                    )
                except CommunicationPathError as exc:
                    logger.warning("%s", exc)
                self._applied[address] = _Applied(
                    applied.coupler, need.actions, applied.white_list
                )

    def _rf_mode_couplers(self, request: _Request) -> list[tuple[Coupler, RFMode]]:
        """The RF modes the media couplers on the way to a KNX RF Multi device are set to
        before it is reached point-to-point: the media coupler towards it to the device's
        mode, the first one (of the connection's own RF line) to MultiFast when the connection
        is a KNX RF Multi interface, else to Ready."""
        if request.broadcast or len(request.targets) != 1:
            return []
        (target,) = request.targets
        mode = self.installation.rf_modes.get(target)
        if mode is None or not request.path:
            return []
        towards, downwards = request.path[-1]
        if not downwards or towards.secondary is not KnxMedium.RF:
            return []
        modes: list[tuple[Coupler, RFMode]] = []
        first, first_downwards = request.path[0]
        if (
            first is not towards
            and not first_downwards
            and first.secondary is KnxMedium.RF
            and first.rf_multi
        ):
            modes.append(
                (
                    first,
                    RFMode.MULTI_FAST
                    if self.installation.rf_multi_connection
                    else RFMode.READY,
                )
            )
        if towards.rf_multi or mode is not RFMode.READY:
            modes.append((towards, mode))
        return modes

    async def _rf_mode(self, request: _Request) -> None:
        """Set the RF modes of :meth:`_rf_mode_couplers` (unicast)."""
        for coupler, mode in self._rf_mode_couplers(request):
            await self.set_rf_mode(coupler, unicast=mode)

    async def _rf_multi_interface(self, request: _Request) -> None:
        """On a KNX RF connection, the first coupler, of the connection's own RF line, uses
        KNX RF Multi towards it when both speak it, else KNX RF Ready (unicast)."""
        if not self.installation.rf_connection or not request.path:
            return
        first, downwards = request.path[0]
        if downwards or first.secondary is not KnxMedium.RF or not first.rf_multi:
            return
        if first.address in self._rf_multi_set:
            return
        await self.set_rf_mode(
            first,
            unicast=RFMode.MULTI_FAST
            if self.installation.rf_multi_connection
            else RFMode.READY,
        )
        self._rf_multi_set.add(first.address)

    async def set_rf_mode(
        self,
        coupler: Coupler,
        *,
        unicast: RFMode | None = None,
        broadcast: RFMode | None = None,
    ) -> None:
        """Set the RF mode a TP1/RF Media Coupler uses for unicast and broadcast frames onto
        RF (PID 79 and PID 114 of its RF Router Object, ``[00 00 mode]``; broadcasts know only
        Ready and Multi). Raises :class:`CommunicationPathError` when it can't be reached; a
        refusal is only logged."""
        from .programmer import FREE_ACCESS_KEY, DeviceProgrammer
        from .session import management_session

        manager = management_session(
            self._xknx, coupler.address, coupler.security, routed=False
        )
        try:
            programmer = DeviceProgrammer(await manager.open())
            if coupler.access_key != FREE_ACCESS_KEY:
                await programmer.authorize(coupler.access_key)
            index = await programmer.locate_object(_ROUTER_OBJECT, _SECONDARY - 1)
            if unicast is not None:
                await _command(
                    programmer,
                    coupler,
                    index,
                    PID_RF_MULTI_UNICAST_CONTROL,
                    (0, 0, unicast),
                )
            if broadcast is not None:
                value = RFMode.READY if broadcast is RFMode.READY else RFMode.MULTI_FAST
                await _command(
                    programmer,
                    coupler,
                    index,
                    PID_RF_MULTI_BROADCAST_CONTROL,
                    (0, 0, value),
                )
            logger.info(
                "Media coupler %s: RF mode unicast %s, broadcast %s",
                coupler.address,
                unicast.name if unicast is not None else "-",
                broadcast.name if broadcast is not None else "-",
            )
        except (XKNXException, DownloadError, TimeoutError) as exc:
            raise CommunicationPathError(
                coupler.address, str(exc) or repr(exc)
            ) from exc
        finally:
            with contextlib.suppress(XKNXException, DownloadError, TimeoutError):
                await manager.close()

    def rf_mode_switch(
        self, request: _Request | None
    ) -> Callable[[bool], Awaitable[None]] | None:
        """For the wait for a KNX RF device in programming mode behind a media coupler that
        speaks KNX RF Multi: a function that sets the coupler's unicast and broadcast RF mode
        to MultiFast (``True``) or Ready; None for any other path."""
        if request is None or not request.path:
            return None
        towards, downwards = request.path[-1]
        if (
            not downwards
            or towards.secondary is not KnxMedium.RF
            or not towards.rf_multi
        ):
            return None

        async def switch(multi: bool) -> None:
            mode = RFMode.MULTI_FAST if multi else RFMode.READY
            await self.set_rf_mode(towards, unicast=mode, broadcast=mode)

        return switch

    async def remove(self, request: _Request) -> None:
        """``request`` is done: set back the couplers nothing needs any more."""
        async with self._lock:
            with contextlib.suppress(ValueError):
                self._requests.remove(request)
            try:
                await self._apply(_merge(self._requests))
            except CommunicationPathError as exc:
                logger.warning("%s", exc)
            if not self._requests and self._retrigger is not None:
                self._retrigger.cancel()
                self._retrigger = None

    async def _apply(self, needs: Mapping[IndividualAddress, _Need]) -> None:
        for address in reversed([a for a in self._applied if a not in needs]):
            await self._disable(address)
        enabled: list[IndividualAddress] = []
        try:
            for address, need in needs.items():
                applied = _Applied(
                    need.coupler, need.actions, frozenset(need.white_list)
                )
                old = self._applied.get(address)
                if old == applied:
                    continue
                await self._set(applied, enable=True)
                if old is None:
                    enabled.append(address)
                self._applied[address] = applied
        except CommunicationPathError:
            for address in reversed(enabled):
                await self._disable(address)
            raise

    async def _disable(self, address: IndividualAddress) -> None:
        """Set a coupler back; one that fails is only warned about, its temporary settings
        end by themselves."""
        applied = self._applied.pop(address)
        try:
            await self._set(applied, enable=False)
        except CommunicationPathError as exc:
            logger.warning("%s", exc)
        self._added.pop(address, None)

    async def _retrigger_loop(self) -> None:
        while True:
            await asyncio.sleep(RETRIGGER_INTERVAL_S)
            async with self._lock:
                for applied in list(self._applied.values()):
                    try:
                        await self._set(applied, enable=True, retrigger=True)
                    except CommunicationPathError as exc:
                        logger.warning("%s", exc)

    async def _set(
        self,
        applied: _Applied,
        *,
        enable: bool,
        retrigger: bool = False,
        close_system_broadcast: bool = False,
    ) -> None:
        """Enable (or retrigger, or disable) a coupler's actions over a connection of its own;
        when enabling fails, the actions already set are set back. With
        ``close_system_broadcast`` an IP router's system broadcast routing is disabled."""
        from .programmer import FREE_ACCESS_KEY, DeviceProgrammer
        from .session import management_session

        coupler, actions = applied.coupler, applied.actions
        manager = management_session(
            self._xknx, coupler.address, coupler.security, routed=False
        )
        undo: list[Callable[[], Awaitable[object]]] = []
        try:
            programmer = DeviceProgrammer(await manager.open())
            if coupler.access_key != FREE_ACCESS_KEY:
                await programmer.authorize(coupler.access_key)
            if (
                actions & (CouplerAction.BROADCAST | CouplerAction.SYSTEM_BROADCAST)
                and coupler.secondary is KnxMedium.RF
            ):
                mode = RFSBCMode.DISABLED
                if enable:
                    mode = (
                        RFSBCMode.SYSTEM_BROADCAST
                        if actions & CouplerAction.SYSTEM_BROADCAST
                        else RFSBCMode.BROADCAST
                    )
                index = await programmer.locate_object(_ROUTER_OBJECT, _SECONDARY - 1)
                await _command(programmer, coupler, index, PID_RF_SBC_CONTROL, (mode,))
                undo.append(
                    functools.partial(
                        _command,
                        programmer,
                        coupler,
                        index,
                        PID_RF_SBC_CONTROL,
                        (RFSBCMode.DISABLED,),
                    )
                )
            elif coupler.primary is KnxMedium.IP and (
                actions & CouplerAction.SYSTEM_BROADCAST or close_system_broadcast
            ):
                # KNX v01.10.01 - Resources 03.05.01 - §4.5.16.1: the IP System Broadcast
                # Routing Mode in the first Router Object, [00h, 00h, mode]
                on = enable and bool(actions & CouplerAction.SYSTEM_BROADCAST)
                index = await programmer.locate_object(_ROUTER_OBJECT, _PRIMARY - 1)
                await _command(
                    programmer, coupler, index, PID_IP_SBC_CONTROL, (0, 0, int(on))
                )
                undo.append(
                    functools.partial(
                        _command,
                        programmer,
                        coupler,
                        index,
                        PID_IP_SBC_CONTROL,
                        (0, 0, 0),
                    )
                )
            if actions & CouplerAction.UNICAST_WHITE_LIST:
                await self._white_list(
                    programmer, applied, enable=enable, retrigger=retrigger
                )
                undo.append(
                    functools.partial(
                        self._white_list, programmer, applied, enable=False
                    )
                )
            if actions & CouplerAction.BROADCAST_CONTROL:
                await _broadcast_control(programmer, coupler, actions, enable)
                undo.append(
                    functools.partial(
                        _broadcast_control, programmer, coupler, actions, False
                    )
                )
        except (XKNXException, DownloadError, TimeoutError) as exc:
            if enable:
                for step in reversed(undo):
                    with contextlib.suppress(
                        XKNXException, DownloadError, TimeoutError
                    ):
                        await step()
            raise CommunicationPathError(
                coupler.address, str(exc) or repr(exc)
            ) from exc
        finally:
            with contextlib.suppress(XKNXException, DownloadError, TimeoutError):
                await manager.close()

    async def _white_list(
        self,
        programmer: DeviceProgrammer,
        applied: _Applied,
        *,
        enable: bool,
        retrigger: bool = False,
    ) -> None:
        """The coupler's Temporary Unicast White List for the connection's address: partners
        added that it doesn't have yet (else retriggered), retriggered, or revoked."""
        from .programmer import extended_object_index

        coupler = applied.coupler
        user = self._xknx.current_address.raw.to_bytes(2, "big")
        index = extended_object_index(_ROUTER_OBJECT, _PRIMARY)
        added = self._added.setdefault(coupler.address, set())
        if not enable:
            await _command(
                programmer, coupler, index, PID_PTP_ROUTING_TEMP, (0, _REVOKE), user
            )
            added.clear()
            return
        new = [
            address.raw
            for address in applied.white_list
            if address not in added and address != coupler.address
        ]
        if retrigger or not new:
            await _command(
                programmer, coupler, index, PID_PTP_ROUTING_TEMP, (0, _RETRIGGER), user
            )
            return
        for start, count in _ranges(new):
            data = user + start.to_bytes(2, "big") + count.to_bytes(2, "big")
            if await _command(
                programmer, coupler, index, PID_PTP_ROUTING_TEMP, (0, _ADD), data
            ):
                added.update(
                    IndividualAddress(raw) for raw in range(start, start + count)
                )


async def _broadcast_control(
    programmer: DeviceProgrammer, coupler: Coupler, actions: CouplerAction, enable: bool
) -> None:
    """Enable or disable the temporary broadcast routing on both sides of the coupler, and
    the system broadcast routing on an IP primary or RF secondary side when it is asked for."""
    from .programmer import extended_object_index

    mode = 1 if enable else 0
    for instance, medium, system_medium in (
        (_PRIMARY, coupler.primary, KnxMedium.IP),
        (_SECONDARY, coupler.secondary, KnxMedium.RF),
    ):
        index = extended_object_index(_ROUTER_OBJECT, instance)
        await _command(
            programmer, coupler, index, PID_BC_ROUTING_TEMP, (0, _BROADCAST_MODE, mode)
        )
        if actions & CouplerAction.SYSTEM_BROADCAST and medium is system_medium:
            await _command(
                programmer,
                coupler,
                index,
                PID_BC_ROUTING_TEMP,
                (0, _SYSTEM_BROADCAST_MODE, mode),
            )


async def _command(
    programmer: DeviceProgrammer,
    coupler: Coupler,
    index: int,
    property_id: int,
    head: tuple[int, ...],
    data: bytes = b"",
) -> bool:
    """Send a function property command to the coupler; whether it returned success. A
    refusal is only logged."""
    payload = bytes(head) + data
    result = await programmer.function_property_raw(
        index, property_id, payload, command=True
    )
    if result and result[0] == 0:
        return True
    logger.warning(
        "Coupler %s: function property %d with %s returned %s",
        coupler.address,
        property_id,
        payload.hex(),
        f"{result[0]:#04x}" if result else "no data",
    )
    return False


_paths: weakref.WeakKeyDictionary[XKNX, CommunicationPaths] = (
    weakref.WeakKeyDictionary()
)


def set_installation(xknx: XKNX, installation: Installation | None) -> None:
    """The couplers the communication paths of ``xknx``'s connection go through (None: no
    paths are prepared). Paths that are open stay as they are."""
    paths = _paths.get(xknx)
    if installation is None:
        if paths is not None:
            paths.installation = Installation()
        return
    if paths is None:
        _paths[xknx] = CommunicationPaths(xknx, installation)
    else:
        paths.installation = installation


def communication_paths(xknx: XKNX) -> CommunicationPaths | None:
    """The communication paths of ``xknx``'s connection, if couplers were given."""
    try:
        return _paths.get(xknx)
    except TypeError:
        # an object no weak reference can be made to has none
        return None


async def _nothing_to_close() -> None:
    """No system broadcast routing to close."""


@dataclass(frozen=True)
class BroadcastPath:
    """The open path of :func:`broadcast_path`: whether the services go as system broadcasts
    from the connection; the coupler that stops secured system broadcasts towards the device
    (see :meth:`CommunicationPaths.secure_restriction`); ``close_system_broadcast()`` ends the
    system broadcast routing on the way before the path ends; ``switch_rf_mode`` (None: not
    needed) switches the media coupler towards a KNX RF device between MultiFast (True) and
    Ready while it is looked for in programming mode."""

    system_broadcast: bool
    secure_restriction: IndividualAddress | None = None
    close_system_broadcast: Callable[[], Awaitable[None]] = _nothing_to_close
    switch_rf_mode: Callable[[bool], Awaitable[None]] | None = None


@contextlib.asynccontextmanager
async def broadcast_path(
    xknx: XKNX,
    target: IndividualAddress,
    *,
    system_broadcast: bool,
    medium: KnxMedium = KnxMedium.RF,
) -> AsyncGenerator[BroadcastPath]:
    """Keep the path to ``target``, a device on ``medium``, open for reaching it by broadcast
    and then point-to-point, while the context is open; ``system_broadcast`` when the services
    are system broadcasts (a KNX RF device, an IP device that takes them). Without couplers
    given the services go as ``system_broadcast`` says."""
    paths = communication_paths(xknx)
    if paths is None:
        yield BroadcastPath(system_broadcast)
        return
    request = paths.broadcast(target, system_broadcast=system_broadcast, medium=medium)
    local = paths.local_system_broadcast(
        request, system_broadcast=system_broadcast, medium=medium
    )
    if request is None:
        yield BroadcastPath(
            local, switch_rf_mode=paths.rf_mode_switch(paths.path_to(target))
        )
        return
    await paths.add(request)
    try:
        yield BroadcastPath(
            local,
            secure_restriction=paths.secure_restriction(request),
            close_system_broadcast=functools.partial(
                paths.close_system_broadcast, request
            ),
            switch_rf_mode=paths.rf_mode_switch(request),
        )
    finally:
        await paths.remove(request)


class _RoutedManager:
    """A connection manager whose connection keeps the path to the device open."""

    def __init__(
        self, inner: ConnectionManager, paths: CommunicationPaths, request: _Request
    ) -> None:
        self._inner = inner
        self._paths = paths
        self._request = request
        self._added = False

    async def open(self) -> BusConnection:
        if not self._added:
            await self._paths.add(self._request)
            self._added = True
        try:
            return await self._inner.open()
        except BaseException:
            await self._release()
            raise

    async def close(self) -> None:
        try:
            await self._inner.close()
        finally:
            await self._release()

    async def _release(self) -> None:
        if self._added:
            self._added = False
            await self._paths.remove(self._request)

    def __getattr__(self, name: str) -> Any:
        # The session's own properties, such as the secure session's sequence number.
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)


def routed_manager(
    xknx: XKNX, address: IndividualAddress, manager: ConnectionManager
) -> ConnectionManager:
    """``manager``, keeping the path to ``address`` open while it is connected when a coupler
    on the way needs it."""
    paths = communication_paths(xknx)
    if paths is None:
        return manager
    request = paths.unicast(address)
    if request is None:
        return manager
    return _RoutedManager(manager, paths, request)

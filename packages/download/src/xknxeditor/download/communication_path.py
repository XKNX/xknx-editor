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


@dataclass(frozen=True)
class Installation:
    """The couplers the editor knows of, by their individual address, and whether the
    connection is on KNX RF."""

    couplers: Mapping[IndividualAddress, Coupler] = field(
        default_factory=dict[IndividualAddress, Coupler]
    )
    rf_connection: bool = False

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


@dataclass(frozen=True)
class _Request:
    """What one operation needs: its path, the devices it addresses point-to-point, and
    whether it reaches its target by broadcast (or system broadcast)."""

    path: tuple[tuple[Coupler, bool], ...]
    targets: frozenset[IndividualAddress]
    broadcast: bool = False
    system_broadcast: bool = False


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
    they are asked for; a coupler that blocks broadcasts passes them."""
    path = request.path
    needs = {coupler.address: _Need(coupler) for coupler, _downwards in path}
    for coupler, _downwards in path:
        if coupler.block_unicast:
            _add_white_list(needs[coupler.address], request.targets)
    if request.broadcast:
        for position, (coupler, downwards) in enumerate(path):
            need = needs[coupler.address]
            if coupler.secondary is KnxMedium.RF:
                leaf = position == len(path) - 1
                need.actions |= (
                    CouplerAction.SYSTEM_BROADCAST
                    if leaf and downwards and request.system_broadcast
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

    def unicast(self, target: IndividualAddress) -> _Request | None:
        """The request to talk to ``target`` point-to-point; None when no coupler on the way
        needs anything for it."""
        return self._request(target, broadcast=False, system_broadcast=False)

    def broadcast(
        self, target: IndividualAddress, *, system_broadcast: bool
    ) -> _Request | None:
        """The request to reach ``target`` by broadcast (or system broadcast) and then
        point-to-point; None when no coupler on the way needs anything for it."""
        return self._request(target, broadcast=True, system_broadcast=system_broadcast)

    def _request(
        self, target: IndividualAddress, *, broadcast: bool, system_broadcast: bool
    ) -> _Request | None:
        path = self.installation.path(self._xknx.current_address, target)
        request = _Request(path, frozenset({target}), broadcast, system_broadcast)
        if not any(need.actions for need in _needs(request).values()):
            return None
        return request

    def local_system_broadcast(self, request: _Request | None) -> bool:
        """Whether the services of a broadcast ``request`` that asks for system broadcasts
        go as system broadcasts from the connection: only on KNX RF, and only when no coupler
        on the path makes them system broadcasts itself."""
        if request is None:
            return self.installation.rf_connection
        if not (request.system_broadcast and self.installation.rf_connection):
            return False
        return not any(
            need.actions & CouplerAction.SYSTEM_BROADCAST
            for need in _needs(request).values()
        )

    async def add(self, request: _Request) -> None:
        """Prepare the couplers for ``request``; raises :class:`CommunicationPathError` when
        one can't be reached, after setting back those this prepared."""
        async with self._lock:
            await self._apply(_merge([*self._requests, request]))
            self._requests.append(request)
            if self._retrigger is None:
                self._retrigger = asyncio.create_task(self._retrigger_loop())

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
        self, applied: _Applied, *, enable: bool, retrigger: bool = False
    ) -> None:
        """Enable (or retrigger, or disable) a coupler's actions over a connection of its own;
        when enabling fails, the actions already set are set back."""
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


@contextlib.asynccontextmanager
async def broadcast_path(
    xknx: XKNX, target: IndividualAddress, *, system_broadcast: bool
) -> AsyncGenerator[bool]:
    """Keep the path to ``target`` open for reaching it by broadcast and then point-to-point,
    while the context is open; ``system_broadcast`` when the services are system broadcasts
    (a KNX RF device). Yields whether to send them as system broadcasts from the connection;
    without couplers given, ``system_broadcast``."""
    paths = communication_paths(xknx)
    if paths is None:
        yield system_broadcast
        return
    request = paths.broadcast(target, system_broadcast=system_broadcast)
    local = system_broadcast and paths.local_system_broadcast(request)
    if request is None:
        yield local
        return
    await paths.add(request)
    try:
        yield local
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

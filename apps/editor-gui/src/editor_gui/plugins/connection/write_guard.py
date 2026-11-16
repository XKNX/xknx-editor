"""Block Bus Writes: with it on, the editor reads from the bus but changes nothing there.

The check runs at two places, so it holds for every code path (downloads, individual address and
serial number writes, restarts, property and memory writes, group writes and responses, KNX
Secure key writes, DALI commands and MCP tools alike):

- the CEMI handler (``DiagnosticCEMIHandler.send_telegram``) checks each telegram before it is
  turned into a frame, and so before KNX Data Secure encrypts it (xknx's group Data Secure and
  the download's Tool Key session alike): reads of secured devices keep working;
- the observable interfaces' ``send_cemi`` (:mod:`editor_gui.plugins.connection.interface`)
  check every frame that did not come through that handler: frames sent as they are (the Group
  Monitor's, a replay, raw frames from MCP) and requests to the interface itself. A Data Secure
  frame sent that way cannot be looked into and counts as a write.

What is let through is a fixed list of reads (and the transport layer's connect, disconnect and
acknowledgements they need); everything else counts as a write. Operations that only write
refuse before they start (``ConnectionService.writes_refused``), and the Group Monitor refuses a
group write also when it would send it through Home Assistant's connection.

Protected Group Addresses (e.g. the ones of gates or an alarm system) are checked at the same two
places, but always: nothing is sent to them, not even reads, whether Block Bus Writes is on or off.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData, CEMIMPropReadRequest
from xknx.exceptions import XKNXException
from xknx.telegram import GroupAddress, Telegram, apci, tpci

# Services that only ask a device, the interface or the bus for information.
_READS: tuple[type[apci.APCI], ...] = (
    apci.GroupValueRead,
    apci.IndividualAddressRead,
    apci.IndividualAddressSerialRead,
    apci.ADCRead,
    apci.FunctionPropertyStateRead,
    apci.FunctionPropertyExtStateRead,
    apci.SystemNetworkParameterRead,
    apci.NetworkParameterRead,
    apci.PropertyValueRead,
    apci.PropertyDescriptionRead,
    apci.PropertyExtValueRead,
    apci.PropertyExtDescriptionRead,
    apci.MemoryRead,
    apci.MemoryExtendedRead,
    apci.UserMemoryRead,
    apci.UserManufacturerInfoRead,
    apci.DeviceDescriptorRead,
    apci.FilterTableRead,
    apci.RouterMemoryRead,
    apci.RouterStatusRead,
    apci.LinkRead,
    apci.GroupPropValueRead,
    apci.DomainAddressRead,
    apci.DomainAddressSelectiveRead,
    apci.DomainAddressSerialNumberRead,
    # Gives the access level a read of protected memory needs; it changes nothing.
    apci.AuthorizeRequest,
)
# By service code: a request carried in another class (such as the download's trigger of an
# S-A_Sync, sent as a device descriptor read) counts as the read it is on the bus.
_READ_SERVICES = frozenset(read.CODE for read in _READS)
# The transport layer's own frames around a connection-oriented read.
_TRANSPORT_CONTROL = (tpci.TConnect, tpci.TDisconnect, tpci.TAck, tpci.TNak)

# Set while the CEMI handler sends a telegram it checked already (before encryption): the
# interface then lets its frame through, encrypted or not.
_checked_telegram: ContextVar[bool] = ContextVar("checked_telegram", default=False)


class BusWritesBlocked(XKNXException):
    """A frame that would change something on the bus, refused because Block Bus Writes is on."""

    def __init__(self, what: str) -> None:
        super().__init__(
            f"not sent: Block Bus Writes is on (Settings > Connection) - {what}"
        )


def _is_write(tpci_: tpci.TPCI, payload: apci.APCI | None) -> bool:
    if isinstance(tpci_, _TRANSPORT_CONTROL):
        return False
    if payload is None:
        return True
    return payload.CODE not in _READ_SERVICES


def is_bus_write(cemi: CEMIFrame) -> bool:
    """Whether sending ``cemi`` could change something on the bus or in the interface."""
    data = cemi.data
    if isinstance(data, CEMIMPropReadRequest):
        return False
    if not isinstance(data, CEMILData):
        # Property writes, function property commands and resets of the interface itself.
        return True
    return _is_write(data.tpci, data.payload)


def is_bus_write_telegram(telegram: Telegram) -> bool:
    """Whether sending ``telegram`` could change something on the bus, judged by its plain
    service before any Data Secure encryption."""
    return _is_write(telegram.tpci, telegram.payload)


def describe_telegram(telegram: Telegram) -> str:
    """The service and destination of ``telegram``, for the log."""
    payload = telegram.payload
    service = type(payload).__name__ if payload is not None else "no payload"
    return f"{service} to {telegram.destination_address}"


def check_telegram(
    writes_blocked: Callable[[], bool] | None, telegram: Telegram
) -> None:
    """Raise :class:`ProtectedRefused` for a telegram to a protected group address, and
    :class:`BusWritesBlocked` for a telegram that writes while Block Bus Writes is on."""
    destination = telegram.destination_address
    if isinstance(destination, GroupAddress) and (
        reason := protected_reason(str(destination))
    ):
        raise ProtectedRefused(describe_telegram(telegram), reason)
    if (
        writes_blocked is not None
        and writes_blocked()
        and is_bus_write_telegram(telegram)
    ):
        raise BusWritesBlocked(describe_telegram(telegram))


def check_frame(writes_blocked: Callable[[], bool] | None, cemi: CEMIFrame) -> None:
    """Raise :class:`BusWritesBlocked` for a frame that writes while Block Bus Writes is on,
    unless it carries a telegram the CEMI handler checked already. A frame to a protected group
    address is always refused (:class:`ProtectedRefused`)."""
    if reason := protected_allows(cemi):
        raise ProtectedRefused(describe(cemi), reason)
    if _checked_telegram.get():
        return
    if writes_blocked is not None and writes_blocked() and is_bus_write(cemi):
        raise BusWritesBlocked(describe(cemi))


@contextmanager
def checked_telegram() -> Generator[None]:
    """Mark the frames sent within as carrying a telegram checked by :func:`check_telegram`."""
    token = _checked_telegram.set(True)
    try:
        yield
    finally:
        _checked_telegram.reset(token)


def describe(cemi: CEMIFrame) -> str:
    """The service and destination of ``cemi``, for the log."""
    data = cemi.data
    if isinstance(data, CEMILData):
        service = (
            type(data.payload).__name__ if data.payload is not None else "no payload"
        )
        return f"{service} to {data.dst_addr}"
    return cemi.code.name


class ProtectedRefused(BusWritesBlocked):
    """A frame to a protected group address: the editor never sends to these, reads included."""

    def __init__(self, what: str, reason: str) -> None:
        XKNXException.__init__(
            self, f"not sent: {reason} (Settings > Connection) - {what}"
        )


class _Protected:
    exact: frozenset[str] = frozenset()
    prefixes: tuple[str, ...] = ()


_protected = _Protected()


def parse_protected(text: str | Iterable[str]) -> list[str]:
    """Group addresses and groups from a setting, separated by commas, spaces or new lines; a
    group ends with ``/`` (e.g. ``7/0/``, ``7/``)."""
    items = text.replace(",", " ").split() if isinstance(text, str) else list(text)
    return sorted({item.strip() for item in items if item.strip()})


def set_protected(patterns: Iterable[str]) -> None:
    """Protected Group Addresses: nothing is sent to them, reads included, whatever else is
    allowed (exact addresses, or groups ending with ``/``)."""
    items = parse_protected(patterns)
    _protected.exact = frozenset(p for p in items if not p.endswith("/"))
    _protected.prefixes = tuple(p for p in items if p.endswith("/"))


def protected_patterns() -> list[str]:
    """The protected group addresses and groups, sorted."""
    return sorted([*_protected.exact, *_protected.prefixes])


def protected_reason(group_address: str) -> str:
    """Why ``group_address`` (printed, e.g. ``1/2/3``) may not be sent to, "" when it may."""
    if group_address in _protected.exact:
        return f"{group_address} is a protected group address"
    for prefix in _protected.prefixes:
        if group_address.startswith(prefix):
            return f"{group_address} is in the protected group {prefix}"
    return ""


def protected_allows(cemi: CEMIFrame) -> str:
    """Why ``cemi`` may not go out (a frame to a protected group address), "" when it may."""
    data = cemi.data
    if not isinstance(data, CEMILData) or not isinstance(data.dst_addr, GroupAddress):
        return ""
    return protected_reason(str(data.dst_addr))

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
from dataclasses import dataclass

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData, CEMIMPropReadRequest
from xknx.exceptions import CouldNotParseAddress, XKNXException
from xknx.telegram import (
    GroupAddress,
    IndividualAddress,
    Telegram,
    apci,
    tpci,
)
from xknx.telegram.address import InternalGroupAddress

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
_mdt_identity_read: ContextVar[tuple[IndividualAddress, bytes] | None] = ContextVar(
    "mdt_identity_read", default=None
)


@contextmanager
def mdt_firmware_info_read(
    address: IndividualAddress, sequence: int
) -> Generator[None]:
    """Classify one identity request to a manufacturer-verified MDT device as a read."""
    if not 0 <= sequence < 28:
        raise ValueError("Invalid firmware identity sequence")
    token = _mdt_identity_read.set((address, bytes((0, 0x11, sequence))))
    try:
        yield
    finally:
        _mdt_identity_read.reset(token)


class BusWritesBlocked(XKNXException):
    """A frame that would change something on the bus, refused because Block Bus Writes is on."""

    def __init__(self, what: str) -> None:
        super().__init__(
            f"not sent: Block Bus Writes is on (Settings > Connection) - {what}"
        )


def _is_write(
    tpci_: tpci.TPCI,
    payload: apci.APCI | None,
    destination: IndividualAddress | GroupAddress | InternalGroupAddress,
) -> bool:
    if isinstance(tpci_, _TRANSPORT_CONTROL):
        return False
    if payload is None:
        return True
    if (
        type(payload) is apci.FunctionPropertyCommand
        and isinstance(destination, IndividualAddress)
        and payload.object_index == 0
        and payload.property_id == 242
        and _mdt_identity_read.get() == (destination, payload.data)
    ):
        return False
    return payload.CODE not in _READ_SERVICES


def is_bus_write(cemi: CEMIFrame) -> bool:
    """Whether sending ``cemi`` could change something on the bus or in the interface."""
    data = cemi.data
    if isinstance(data, CEMIMPropReadRequest):
        return False
    if not isinstance(data, CEMILData):
        # Property writes, function property commands and resets of the interface itself.
        return True
    return _is_write(data.tpci, data.payload, data.dst_addr)


def is_bus_write_telegram(telegram: Telegram) -> bool:
    """Whether sending ``telegram`` could change something on the bus, judged by its plain
    service before any Data Secure encryption."""
    return _is_write(telegram.tpci, telegram.payload, telegram.destination_address)


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
        reason := protected_reason(destination)
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


@dataclass(frozen=True, slots=True)
class ProtectedEntry:
    """One entry of Protected Group Addresses: a group address, or a main group (``7/``) or
    middle group (``7/0/``), as the range of raw address values it covers."""

    text: str
    first: int
    last: int

    def covers(self, raw: int) -> bool:
        return self.first <= raw <= self.last


def parse_protected_entry(text: str) -> ProtectedEntry:
    """``text`` as a :class:`ProtectedEntry`; :class:`ValueError` when it is no group address
    (any spelling ``GroupAddress`` takes: ``1/2/3``, ``1/515``, ``2563``) or group (``7/``,
    ``7/0/``)."""
    item = text.strip()
    try:
        if item.endswith("/"):
            parts = item[:-1].split("/")
            if len(parts) == 1:
                main = GroupAddress(f"{int(parts[0])}/0/0").main or 0
                return ProtectedEntry(f"{main}/", main << 11, (main << 11) | 0x7FF)
            if len(parts) == 2:
                address = GroupAddress(f"{int(parts[0])}/{int(parts[1])}/0")
                start = address.raw
                return ProtectedEntry(
                    f"{address.main}/{address.middle}/", start, start | 0xFF
                )
            raise ValueError(item)
        address = GroupAddress(item)
    except (CouldNotParseAddress, ValueError):
        raise ValueError(f"not a group address or group: {item!r}") from None
    printed = f"{address.raw >> 11}/{(address.raw >> 8) & 0x7}/{address.raw & 0xFF}"
    return ProtectedEntry(printed, address.raw, address.raw)


def parse_protected(text: str | Iterable[str]) -> list[str]:
    """The entries of a setting (separated by commas, spaces or new lines), each written the
    same way (``07/0/1`` and ``14337`` become ``7/0/1``), sorted by address; a group ends with
    ``/`` (``7/``, ``7/0/``). Raises :class:`ValueError` naming every entry that is no group
    address or group."""
    items = text.replace(",", " ").split() if isinstance(text, str) else list(text)
    entries: dict[str, ProtectedEntry] = {}
    invalid: list[str] = []
    for item in items:
        if not item.strip():
            continue
        try:
            entry = parse_protected_entry(item)
        except ValueError:
            invalid.append(item.strip())
            continue
        entries[entry.text] = entry
    if invalid:
        raise ValueError(f"not a group address or group: {', '.join(invalid)}")
    ordered = sorted(entries.values(), key=lambda e: (e.first, -e.last))
    return [entry.text for entry in ordered]


class _Protected:
    entries: tuple[ProtectedEntry, ...] = ()


_protected = _Protected()


def set_protected(patterns: Iterable[str]) -> None:
    """Protected Group Addresses: nothing is sent to them, reads included, whatever else is
    allowed (group addresses, or main and middle groups ending with ``/``). Raises
    :class:`ValueError` for an entry that is no group address or group."""
    _protected.entries = tuple(
        parse_protected_entry(text) for text in parse_protected(patterns)
    )


def protected_patterns() -> list[str]:
    """The protected group addresses and groups, sorted by address."""
    return [entry.text for entry in _protected.entries]


def protected_reason(group_address: str | int | GroupAddress) -> str:
    """Why ``group_address`` (any spelling ``GroupAddress`` takes, e.g. ``1/2/3``, ``1/515``,
    ``2563``) may not be sent to, "" when it may (also when it is no group address: whoever
    sends it refuses that)."""
    try:
        address = (
            group_address
            if isinstance(group_address, GroupAddress)
            else GroupAddress(group_address)
        )
    except CouldNotParseAddress:
        return ""
    raw = address.raw
    printed = f"{raw >> 11}/{(raw >> 8) & 0x7}/{raw & 0xFF}"
    for entry in _protected.entries:
        if entry.covers(raw):
            if entry.first == entry.last:
                return f"{printed} is a protected group address"
            return f"{printed} is in the protected group {entry.text}"
    return ""


def protected_allows(cemi: CEMIFrame) -> str:
    """Why ``cemi`` may not go out (a frame to a protected group address), "" when it may."""
    data = cemi.data
    if not isinstance(data, CEMILData) or not isinstance(data.dst_addr, GroupAddress):
        return ""
    return protected_reason(data.dst_addr)

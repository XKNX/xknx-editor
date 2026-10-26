"""`ProjectService` — holds any number of open projects (one SQLite file each), keyed by project id.

Each edit goes through an :class:`~xknxeditor.proj.core.event_store.EventStore` that applies the
event, appends it to the ``events`` log, and moves the undo/redo cursor. Live state lives in the
relational tables and reads hand back ORM rows. Installation-scoped calls pass the 0-based
``index``; everything else uses internal row ids.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import datetime
import ipaddress
import secrets
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4
from xml.etree import ElementTree as ET

from sqlalchemy import func, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from xknxeditor.proj.core import ip_secure
from xknxeditor.proj.core.addressing import (
    GroupAddressStyle,
    format_ga,
    format_group_range,
    format_ia,
    parse_ia,
    ranges_for,
)
from xknxeditor.proj.core.bus_load import (
    BusLoad,
    Hardware,
    device_current,
    needs_estimate,
    shown_current,
)
from xknxeditor.proj.core.bus_load import total as bus_total
from xknxeditor.proj.core.compact import (
    CompactResult,
    analyse_compaction,
    compact_project,
)
from xknxeditor.proj.core.coupler_rules import CouplerDevice, check_coupler
from xknxeditor.proj.core.dpt import normalize_datapoint_type
from xknxeditor.proj.core.event_store import EventStore, HistoryEntry
from xknxeditor.proj.core.events import (
    COM_OBJECT_FLAGS,
    COM_OBJECT_PRIORITIES,
    STATUS_KINDS,
    TRADE_FIELDS,
    AddDevice,
    AddFunctionGroupAddress,
    AddInstallation,
    AddTradeDevice,
    ChangeDeviceApplication,
    CompositeEvent,
    CreateArea,
    CreateFunction,
    CreateFunctionWithGroupAddresses,
    CreateGroupAddress,
    CreateGroupRange,
    CreateLine,
    CreateSegment,
    CreateSpace,
    CreateTrade,
    Event,
    ImportGroupAddresses,
    LinkComObject,
    MoveDevice,
    MoveFunction,
    MoveGroupRange,
    MoveLine,
    MoveSegment,
    MoveSpace,
    MoveTrade,
    RemoveArea,
    RemoveDevice,
    RemoveFunction,
    RemoveFunctionGroupAddress,
    RemoveGroupAddress,
    RemoveGroupRange,
    RemoveLine,
    RemoveSegment,
    RemoveSpace,
    RemoveTrade,
    RemoveTradeDevice,
    RenameArea,
    RenameFunction,
    RenameGroupAddress,
    RenameGroupRange,
    RenameLine,
    RenameSegment,
    RenameSpace,
    SetComment,
    SetComObjectDatapointType,
    SetComObjectDescription,
    SetComObjectFlag,
    SetComObjectPriority,
    SetComObjectSending,
    SetCompletionStatus,
    SetDeviceAdditionalAddresses,
    SetDeviceCertificates,
    SetDeviceCommissioning,
    SetDeviceDescription,
    SetDeviceIpConfig,
    SetDeviceName,
    SetDeviceSecureCommissioning,
    SetDeviceSpace,
    SetFields,
    SetFunctionDescription,
    SetFunctionType,
    SetGroupAddressAddress,
    SetGroupAddressCentral,
    SetGroupAddressDatapointType,
    SetGroupAddressSecurity,
    SetGroupDescription,
    SetGroupRangeAddress,
    SetGroupRangeSecurity,
    SetInstallationAttribute,
    SetParameter,
    SetProjectName,
    SetSegmentBusAccess,
    SetSegmentDomainAddress,
    SetSegmentMedium,
    SetSpaceDefaultLine,
    SetSpaceDescription,
    SetSpaceNumber,
    SetSpaceUsage,
    SetTopologyAddress,
    SetTopologyDescription,
    SetTradeField,
    SyncDeviceComObjects,
    SyncDeviceModuleInstances,
    UnlinkComObject,
    UpdateDeviceApplication,
    UpsertDeviceBinaryData,
    additional_address_entries,
    addresses_below,
    stored_comment,
    stored_completion_status,
)
from xknxeditor.proj.core.fdsk import Fdsk, InvalidFdskError, parse_fdsk
from xknxeditor.proj.core.function_detect import classify_function
from xknxeditor.proj.core.ga_export import (
    GaExport,
    GaRange,
    GroupAddressStyleMismatch,
)
from xknxeditor.proj.core.ga_security import (
    AUTO_MODES,
    FORCED_MODES,
    commissioned_devices,
    effective_mode,
    forcing_range,
    is_secured,
    linked_devices,
    secure_commissioned,
    secured_group_addresses,
    security_attributes,
)
from xknxeditor.proj.core.ip_settings import (
    ASSIGN_AUTO,
    ASSIGN_FIXED,
    ASSIGNMENTS,
    parse_ipv4,
)
from xknxeditor.proj.core.medium import (
    MAX_LINES,
    MAX_SEGMENTS,
    MEDIUM_IP,
    MEDIUM_NAMES,
    MEDIUM_TP,
    LineView,
    MediumError,
    SegmentView,
    check_line_move,
    check_medium_change,
    check_new_sub_segment,
    check_segment_move,
    domain_address_changes,
    domain_addresses,
    new_domain_address,
    sub_segment_media,
)
from xknxeditor.proj.core.paste import Allocation, place_range
from xknxeditor.proj.core.product_prune import ProgressCallback
from xknxeditor.proj.core.project_secrets import (
    InvalidProjectPassword,
    ProjectCipher,
    ProjectPasswordRequired,
    has_secure_data,
    new_salt,
    recrypt,
)
from xknxeditor.proj.core.skeleton import seed_new_project
from xknxeditor.proj.core.space_rules import SpaceRuleError, check_child, holds_devices
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import (
    COMPLETION_STATUSES,
    Area,
    Base,
    ComObject,
    ComObjectLink,
    Device,
    DeviceAdditionalAddress,
    DeviceSnapshot,
    Function,
    FunctionGroupAddress,
    GroupAddress,
    GroupRange,
    Installation,
    Line,
    Project,
    ProjectTrace,
    Segment,
    Space,
    Trade,
)

# A group address linked across at least this many devices is treated as a central/shared function
# (time, date, day/night, weather) rather than part of a single load, and is excluded from function
# suggestions. Real loads are linked by only a handful of devices (actuator + a sensor or two).
_CENTRAL_GA_MIN_DEVICES = 4
# The KNXnet/IP routing multicast address a backbone has unless it sets another.
DEFAULT_MULTICAST_ADDRESS = "224.0.23.12"


@dataclass
class _Open:
    engine: Engine
    session: Session
    store: EventStore
    # The key of a password-protected project and its password (needed by the export), held only
    # while the project is open; ``None`` for an unprotected project.
    cipher: ProjectCipher | None = None
    password: str | None = None


@dataclass(frozen=True)
class GroupAddressInfo:
    """A display-ready group address: value, style-formatted string, and its links."""

    id: int
    address: int
    text: str
    name: str
    datapoint_type: str | None
    links: list[int]
    description: str
    comment: str
    # Whether it is secured now (see :mod:`.ga_security`).
    data_secure: bool
    # The ``@Security`` mode: "Auto"/"On"/"Off" (None = default, treated as Auto).
    security_mode: str | None = None
    # The mode that counts: the one of a group above it that forces one (``forced_by``), else
    # its own.
    effective_security_mode: str | None = None
    forced_by: ForcedSecurity | None = None
    # Marked as central (``@Central``).
    central: bool = False


@dataclass(frozen=True)
class ForcedSecurity:
    """A main or middle group that forces its Security on what is below it: its id, its name
    and the mode it forces ("On" or "Off")."""

    group_range_id: int
    name: str
    mode: str


@dataclass(frozen=True)
class GroupRangeInfo:
    """A node in the group-address range tree, resolved for display (recursive)."""

    id: int
    name: str
    range_start: int
    range_end: int
    children: list[GroupRangeInfo]
    group_addresses: list[GroupAddressInfo]
    # The folder's style-formatted number (main group "0", middle group "0/1"); empty for Free.
    address: str = ""
    description: str = ""
    # Its own ``@Security`` mode (None = default, Auto), and the group above it that forces one.
    security_mode: str | None = None
    forced_by: ForcedSecurity | None = None


@dataclass(frozen=True)
class SpaceDeviceInfo:
    """A device as referenced from a space (building tree leaf), with display metadata."""

    id: int
    name: str
    individual_address: str | None
    description: str
    product_name: str
    hardware_name: str
    manufacturer_name: str


@dataclass(frozen=True)
class FunctionGroupAddressInfo:
    """A group address referenced by a function, with its role."""

    id: int  # FunctionGroupAddress link id (for removal)
    group_address_id: int
    text: str
    role: str
    name: str = ""


@dataclass(frozen=True)
class FunctionInfo:
    """A function assigned to a space, resolved for display."""

    id: int
    name: str
    function_type: str
    usage_text: str
    group_addresses: list[FunctionGroupAddressInfo]
    description: str = ""


@dataclass(frozen=True)
class FunctionCandidate:
    """A suggested building function inferred from how a device channel's group addresses are wired.

    Produced by :meth:`ProjectService.function_candidates` and consumed by
    :meth:`ProjectService.accept_function_candidate`. ``space_id`` is the room the device is placed
    in; candidates are only produced for placed devices, so a function can always be created."""

    device_id: int
    channel_id: str | None
    name: str
    function_type: str
    space_id: int
    space_name: str
    device_name: str
    group_address_ids: list[int]
    group_address_texts: list[str]


@dataclass(frozen=True)
class SpaceInfo:
    """A node in the building/location tree, resolved for display (recursive)."""

    id: int
    name: str
    space_type: str
    number: str
    usage_text: str
    description: str
    children: list[SpaceInfo]
    devices: list[SpaceDeviceInfo]
    functions: list[FunctionInfo]
    # The room's use: a SpaceUsage id of the master data (``SU-4``), "" for none.
    usage: str = ""
    # The space's own Current Line, None for none (see ``ProjectService.current_line``).
    default_line_id: int | None = None


@dataclass(frozen=True)
class TradeDeviceInfo:
    """A device in a trade: the reference's id and the device."""

    link_id: int
    device: SpaceDeviceInfo


@dataclass(frozen=True)
class TradeInfo:
    """A trade with its sub-trades and devices, in their order (recursive)."""

    id: int
    name: str
    number: str
    description: str
    children: list[TradeInfo]
    devices: list[TradeDeviceInfo]


@dataclass(frozen=True)
class DevicePlacement:
    """A device's segment and line (both None on no line), its device number (None for none)
    and its hardware program (``hardware2program_ref_id``, None for none)."""

    segment_id: int | None
    line_id: int | None
    address: int | None
    hardware2program_ref_id: str | None


@dataclass(frozen=True)
class CurrentLine:
    """The Current Line of a space: the line, and the space that sets it (the space itself or
    one above it)."""

    line_id: int
    space_id: int


@dataclass(frozen=True)
class DeviceKeys:
    """A device's KNX Data Secure tool keys from the project. ``loaded_tool_key`` is the key the
    device holds; ``tool_key`` the one it is given on secure commissioning; ``fdsk`` its factory
    key from the device certificates. ``None`` when absent."""

    tool_key: bytes | None
    loaded_tool_key: bytes | None
    fdsk: bytes | None = None


@dataclass(frozen=True)
class IpBusInterface:
    """A bus interface of an IP device: its application ``@RefId``, its password (``None`` when
    unknown) and whether it is secured (``@IsSecureEnabled``)."""

    ref_id: str
    password: str | None = None
    secure: bool = True


@dataclass(frozen=True)
class IpSecrets:
    """The KNX IP Secure passwords the project holds for an IP device, decrypted: its device
    authentication code, its management (user 1) password and its bus interfaces in the
    device's order, which gives each its user id (see :mod:`.ip_secure`). ``None`` when
    unknown."""

    device_authentication_code: str | None = None
    management_password: str | None = None
    bus_interfaces: list[IpBusInterface] = field(default_factory=list[IpBusInterface])


@dataclass(frozen=True)
class SecureCommissioningImpact:
    """What deactivating a device's secure commissioning changes: the group addresses that then
    communicate in the clear (``Auto`` ones it is secured with now) and the links that are
    removed, as they may not communicate in the clear (see
    :meth:`ProjectService.secure_commissioning_impact`), with those group addresses."""

    unsecured: list[int]
    removed_links: list[int]
    removed_from: list[int]


@dataclass(frozen=True)
class AdditionalAddressInfo:
    """One additional individual address of a device: its entry number ``index`` (1-based, the
    ``AddressIndex`` a tunnelling bus interface names), the full ``address`` (``a.l.d`` in the
    device's line) and its device number ``octet`` (both ``None`` when the entry has no address),
    and its name and description."""

    index: int
    address: str | None
    octet: int | None
    name: str
    description: str


@dataclass(frozen=True)
class DeviceInfo:
    """A display-ready device. The refs point at the catalog product and loaded program; the caller
    resolves the application from them, since this package is ref-only."""

    id: int
    name: str
    individual_address: str | None
    product_ref_id: str
    hardware2program_ref_id: str | None
    description: str
    order_number: str
    hardware_name: str
    product_name: str
    manufacturer_name: str
    # The IP settings of an IP device as the project stores them (its <IPConfig> attributes, see
    # ``core.ip_settings``); None when it has none.
    ip_config: dict[str, str] | None
    # Commissioning state ("loaded" ticks + serial / last download).
    serial_number: str
    last_download: str | None
    individual_address_loaded: bool
    application_program_loaded: bool
    communication_part_loaded: bool
    medium_config_loaded: bool
    parameters_loaded: bool
    # The additional individual addresses (tunnelling addresses of an IP interface), in order.
    additional_addresses: tuple[AdditionalAddressInfo, ...] = ()
    # When its configuration last changed (UTC, as the project stores it), None for never.
    last_modified: str | None = None


@dataclass(frozen=True)
class LinkInfo:
    """A com-object-to-group-address link; ``is_sending`` flags a transmitting one."""

    id: int
    com_object_id: int
    group_address_id: int
    is_sending: bool


@dataclass(frozen=True)
class DeviceLinkInfo:
    """A group-address link with the device of its com-object and the com-object's effective flags."""

    device_id: int
    group_address_id: int
    is_sending: bool
    communication: bool
    read: bool
    write: bool
    transmit: bool
    update: bool


def _forced_by(group_range: GroupRange | None) -> ForcedSecurity | None:
    """The group from ``group_range`` up that forces its Security on what is below it."""
    forcing = forcing_range(group_range)
    if forcing is None or forcing.security_mode is None:
        return None
    return ForcedSecurity(forcing.id, forcing.name, forcing.security_mode)


def _line_views(
    installation: Installation, line_names: Mapping[int, str] | None = None
) -> list[LineView]:
    """The installation's lines as the medium rules see them; a line without a name of its own
    is called as ``line_names`` (by line id) shows it."""
    names = line_names or {}
    return [
        LineView(
            id=line.id,
            area=area.address,
            address=line.address,
            name=line.name or names.get(line.id, ""),
            segments=tuple(
                SegmentView(
                    id=s.id,
                    number=s.number,
                    medium=s.medium_type,
                    name=s.name,
                    domain_address=s.domain_address,
                    devices=tuple((d.id, d.address) for d in s.devices),
                )
                for s in sorted(line.segments, key=lambda s: (s.number, s.id))
            ),
        )
        for area in installation.areas
        for line in area.lines
        if line.segments
    ]


def _new_line_medium(
    session: Session, area_id: int, address: int
) -> tuple[str, int | None]:
    """The medium and domain address of a new line ``address`` in the area ``area_id``: a
    backbone gets TP, a main line the backbone's medium, any other line its area main line's
    medium and domain address; TP when there is none of them."""
    area = session.get(Area, area_id)
    if area is None or (area.address == 0 and address == 0):
        return MEDIUM_TP, None
    if address == 0:
        source = next(
            (
                ln
                for a in area.installation.areas
                if a.address == 0
                for ln in a.lines
                if ln.address == 0 and ln.segments
            ),
            None,
        )
        return (
            (_main_segment(source).medium_type, None) if source else (MEDIUM_TP, None)
        )
    main = next((ln for ln in area.lines if ln.address == 0 and ln.segments), None)
    if main is None:
        return MEDIUM_TP, None
    segment = _main_segment(main)
    return segment.medium_type, segment.domain_address


# A device's coupler data and name for the coupler rules, by its id and hardware program; None
# for a device that is no coupler.
CouplerOf = Callable[[int, str | None], "tuple[CouplerDevice, str] | None"]


def _address_change_events(
    devices: Iterable[Device], couplers: Collection[int] = ()
) -> list[Event]:
    """What a change of their individual addresses takes from the downloaded state of
    ``devices``: the address, of a coupler (``couplers``: the devices that are couplers but no
    segment couplers) also its group communication, and of a device with additional addresses
    its medium configuration."""
    events: list[Event] = []
    for device in devices:
        address = device.individual_address_loaded
        communication = device.id in couplers and device.communication_part_loaded
        medium = bool(device.additional_addresses) and device.medium_config_loaded
        if address or communication or medium:
            events.append(
                SetDeviceCommissioning(
                    device_id=device.id,
                    individual_address_loaded=False if address else None,
                    communication_part_loaded=False if communication else None,
                    medium_config_loaded=False if medium else None,
                )
            )
    return events


def _moved_line_domain(
    medium: str, target_main: SegmentView | None, used: set[int]
) -> int | None:
    """The domain address of the main segment of a line moved into an area: none on TP or IP,
    the one of the area's main line when it has the same medium, else a new one."""
    if medium in (MEDIUM_TP, MEDIUM_IP):
        return None
    if target_main is not None and target_main.medium == medium:
        return target_main.domain_address
    return new_domain_address(medium, used)


def _free_number(used: set[int], count: int, wanted: int | None) -> int | None:
    """``wanted`` when it is one of ``0..count-1`` and not ``used``, without one the lowest such
    number not used; None when there is none."""
    if wanted is not None:
        return wanted if 0 <= wanted < count and wanted not in used else None
    return next((n for n in range(count) if n not in used), None)


def _main_segment(line: Line) -> Segment:
    """A line's main segment: its lowest numbered one."""
    return min(line.segments, key=lambda s: (s.number, s.id))


def _address_order(device: Device) -> tuple[int, int, int, int, int]:
    """Numeric individual-address order; devices without an address (or on no line) come last
    by id."""
    if device.address is None or device.segment is None:
        return (1, 0, 0, 0, device.id)
    line = device.segment.line
    return (0, line.area.address, line.address, device.address, device.id)


def _take_legacy_password(session: Session) -> str | None:
    """The password an earlier build kept in the clear in ``projects.password``, removed from the
    file together with the history steps that recorded it."""
    columns = {row[1] for row in session.execute(text("PRAGMA table_info(projects)"))}
    if "password" not in columns:
        return None
    password = session.execute(text("SELECT password FROM projects")).scalar()
    if password is None:
        return None
    session.execute(text("UPDATE projects SET password = NULL"))
    session.execute(text("DELETE FROM events WHERE type = 'SetProjectPassword'"))
    session.commit()
    return str(password) or None


def _p2p_links(xml: str) -> list[tuple[str | None, list[tuple[str, list[str]]]]]:
    """Each ``<P2PLink>`` as ``(key, [(device id, security role ids)])`` from a stored
    ``<P2PLinks>`` string. Both endpoint kinds (``DeviceEndpoint``, ``BusInterfaceEndpoint``) name
    their device by ``@DeviceRefId``; a device endpoint lists the roles its device is given on the
    other end (``@SecurityRoles``)."""
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    links: list[tuple[str | None, list[tuple[str, list[str]]]]] = []
    for link in root.iter("P2PLink"):
        endpoints = [
            (ref, (endpoint.get("SecurityRoles") or "").split())
            for endpoint in link
            if (ref := endpoint.get("DeviceRefId"))
        ]
        links.append((link.get("Key"), endpoints))
    return links


def _last_valid_sequence_number(device: Device) -> int:
    """The last valid Sequence Number of a sender for a receiver's Security Individual Address
    Table: one below the device's ``<Security @SequenceNumber>`` (its next Sequence Number
    Sending, as last read), else 0 (unknown, 3/5/1 6.3.8.5)."""
    value = security_attributes(device).get("SequenceNumber")
    try:
        return max(int(value) - 1, 0) if value else 0
    except ValueError:
        return 0


def _sequence_timestamp() -> str:
    """The current UTC time in the form of ``<Security @SequenceNumberTimestamp>``."""
    now = datetime.datetime.now(datetime.UTC)
    return f"{now:%Y-%m-%dT%H:%M:%S}.{now.microsecond:06d}0Z"


def _key(value: str | None) -> bytes | None:
    """A base64 key attribute as its 16 key bytes, or ``None`` when absent or malformed."""
    if not value:
        return None
    try:
        key = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return key if len(key) == 16 else None


NOT_ENOUGH_ADDITIONAL_ADDRESSES = (
    "The line does not provide enough addresses for all necessary additional addresses."
)


class AdditionalAddressError(ValueError):
    """An additional individual address that cannot be set or assigned. ``kind`` says why:
    :attr:`NOT_ENOUGH` (the line has too few free device numbers), :attr:`INVALID` (not a device
    number from 1 to 255), :attr:`OTHER_LINE` (outside the device's line, ``address`` is that
    line ``a.l``), :attr:`IN_USE` (a device or another additional address of the line has it,
    ``address`` is the ``a.l.d`` concerned) or :attr:`NO_LINE` (the device is on no line)."""

    NOT_ENOUGH = "not_enough"
    INVALID = "invalid"
    OTHER_LINE = "other_line"
    IN_USE = "in_use"
    NO_LINE = "no_line"

    def __init__(self, kind: str, message: str, address: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.address = address


def _device_line(device: Device) -> Line:
    """The line of the device; raises :class:`AdditionalAddressError` (``NO_LINE``) when it is
    on none."""
    if device.segment is None:
        raise AdditionalAddressError(
            AdditionalAddressError.NO_LINE, "The device is on no line."
        )
    return device.segment.line


def _empty_additional_entry() -> dict[str, Any]:
    return {"address": None, "name": "", "description": "", "comment": ""}


def _fill_additional_entries(entries: list[dict[str, Any]], used: set[int]) -> None:
    """Give each entry without an address the highest free device number of the line, counting
    down from 255 and skipping ``used``. Raises :class:`AdditionalAddressError` when the line has
    too few free numbers (the entries are left unchanged then)."""
    missing = [entry for entry in entries if entry.get("address") is None]
    if not missing:
        return
    free = [number for number in range(255, 0, -1) if number not in used]
    if len(free) < len(missing):
        raise AdditionalAddressError(
            AdditionalAddressError.NOT_ENOUGH, NOT_ENOUGH_ADDITIONAL_ADDRESSES
        )
    for entry, number in zip(missing, free, strict=False):
        entry["address"] = number


class GroupAddressSecurityError(ValueError):
    """A link or Security mode would put a device that is not commissioned securely on a
    secured group address."""


class ProjectService:
    def __init__(self) -> None:
        self._projects: dict[str, _Open] = {}

    # --- lifecycle --------------------------------------------------------

    def create(
        self,
        path: Path | str,
        project_id: str | None = None,
        *,
        name: str = "New project",
        group_address_style: GroupAddressStyle = GroupAddressStyle.THREE_LEVEL,
        building_name: str | None = None,
    ) -> str:
        # Project IDs are "P-" + 4 hex digits (spec Project Scheme §4.2.3, e.g. "P-02D7").
        pid = project_id or f"P-{uuid4().hex[:4].upper()}"
        engine = make_engine(url_for(Path(path)))
        session = Session(engine)
        # The default building is named after the project file when none is given.
        seed_new_project(
            session,
            pid,
            name,
            group_address_style,
            building_name if building_name is not None else Path(path).stem,
        )
        self._register(pid, engine, session)
        return pid

    def open(self, path: Path | str, password: str | None = None) -> str:
        """Open the project at ``path``. A password-protected project needs its ``password``:
        :class:`ProjectPasswordRequired` without one, :class:`InvalidProjectPassword` for a wrong
        one. The password is ignored for an unprotected project. A missing or empty file raises
        :class:`FileNotFoundError` and is left as it is, never made into an empty project."""
        if not Path(path).is_file() or Path(path).stat().st_size == 0:
            raise FileNotFoundError(f"{path} is missing or empty, not a project")
        engine = make_engine(url_for(Path(path)))
        session = Session(engine)
        try:
            legacy = _take_legacy_password(session)
            project = session.query(Project).first()
            if project is None:
                raise ValueError(f"{path} is not a project (no project row)")
            cipher: ProjectCipher | None = None
            if project.password_salt:
                if not password:
                    raise ProjectPasswordRequired("the project is password-protected")
                cipher = ProjectCipher.derive(password, project.password_salt)
                if not cipher.verify(project.password_check or ""):
                    raise InvalidProjectPassword("wrong project password")
        except Exception:
            session.close()
            engine.dispose()
            raise
        self._register(
            project.id, engine, session, cipher, password if cipher else None
        )
        if legacy and cipher is None:
            # A project of an earlier build kept its password in the clear: protect it with it.
            self.set_project_password(project.id, legacy)
        return project.id

    def close(self, project_id: str) -> None:
        state = self._projects.pop(project_id)
        state.session.close()
        state.engine.dispose()

    def list(self) -> list[str]:
        return list(self._projects)

    # --- commands (one public method each) --------------------------------

    def add_installation(self, project_id: str, name: str) -> int:
        state = self._state(project_id)
        highest = state.session.query(func.max(Installation.index)).scalar()
        index = highest + 1 if highest is not None else 0
        state.store.append(AddInstallation(index=index, name=name))
        return index

    def create_area(
        self,
        project_id: str,
        installation: int,
        address: int,
        name: str,
        *,
        medium_type: str | None = None,
    ) -> int:
        """Add the area ``address``; with ``medium_type`` also its main line (``x.0``) of that
        medium, in the same undo step. Raises ``ValueError`` for an address in use,
        ``MediumError`` for a medium the rules refuse (nothing is added then)."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        clash = (
            state.session.query(Area)
            .filter_by(installation_id=inst.id, address=address)
            .first()
        )
        if clash is not None:
            raise ValueError(
                f"Area address {address} already used in this installation by area {clash.id}"
            )
        if medium_type is not None:
            self.check_new_line(project_id, installation, address, 0, medium_type)
        start = state.store.cursor
        event = CreateArea(
            installation_id=inst.id,
            address=address,
            name=name,
        )
        state.store.append(event)
        assert event.area_id is not None
        if medium_type is not None:
            self.create_line(project_id, event.area_id, 0, "", medium_type=medium_type)
            state.store.group_since(start)
        return event.area_id

    def create_line(
        self,
        project_id: str,
        area_id: int,
        address: int,
        name: str,
        *,
        medium_type: str | None = None,
    ) -> int:
        """Add the line ``address`` to an area, of ``medium_type`` (by default the medium a new
        line there gets, see :func:`_new_line_medium`), in one undo step. Raises ``ValueError``
        for an address in use, ``MediumError`` for a medium the rules refuse."""
        state = self._state(project_id)
        clash = (
            state.session.query(Line)
            .filter_by(area_id=area_id, address=address)
            .first()
        )
        if clash is not None:
            raise ValueError(
                f"Line address {address} already used in this area by line {clash.id}"
            )
        medium, domain_address = _new_line_medium(state.session, area_id, address)
        if medium_type is not None and medium_type != medium:
            area = state.session.get(Area, area_id)
            if area is None:
                raise KeyError(f"No area with id {area_id}")
            self.check_new_line(
                project_id, area.installation.index, area.address, address, medium_type
            )
        start = state.store.cursor
        event = CreateLine(
            area_id=area_id,
            address=address,
            name=name,
            medium_type=medium,
            domain_address=domain_address,
        )
        state.store.append(event)
        assert event.line_id is not None and event.segment_id is not None
        if medium_type is not None and medium_type != medium:
            self.set_segment_medium(project_id, event.segment_id, medium_type)
            state.store.group_since(start)
        return event.line_id

    def new_line_media(
        self, project_id: str, installation: int, area_address: int, line_address: int
    ) -> list[str]:
        """The media an empty new line ``line_address`` of the area ``area_address`` can have
        as the medium rules allow them (line 0 of an area not there yet: the main line of a new
        area), in the order lines offer them."""
        allowed: list[str] = []
        for medium in MEDIUM_NAMES:
            try:
                self.check_new_line(
                    project_id, installation, area_address, line_address, medium
                )
            except MediumError:
                continue
            allowed.append(medium)
        return allowed

    def check_new_line(
        self,
        project_id: str,
        installation: int,
        area_address: int,
        line_address: int,
        medium: str,
    ) -> None:
        """Raise a ``MediumError`` unless an empty new line (see :meth:`new_line_media`) can
        have ``medium``."""
        state = self._state(project_id)
        lines = [
            view
            for view in _line_views(self._installation(state, installation))
            if (view.area, view.address) != (area_address, line_address)
        ]
        trial = LineView(
            id=-1,
            area=area_address,
            address=line_address,
            name="",
            segments=(SegmentView(id=-1, number=0, medium=MEDIUM_TP),),
        )
        check_medium_change([*lines, trial], -1, medium)

    def bus_loads(
        self, project_id: str, hardware: Mapping[str, Hardware]
    ) -> tuple[dict[int, BusLoad], dict[int, BusLoad]]:
        """The devices and the bus current of every line and every segment, by line id and
        by segment id (see :mod:`.bus_load`). ``hardware`` is what the catalog tells about a
        device's hardware program (``hardware2program_ref_id``)."""
        state = self._state(project_id)
        lines: dict[int, BusLoad] = {}
        segments: dict[int, BusLoad] = {}
        for line in state.session.query(Line).options(
            selectinload(Line.segments).selectinload(Segment.devices)
        ):
            if not line.segments:
                continue
            main = _main_segment(line)
            for segment in line.segments:
                parts = [
                    device_current(
                        hardware.get(device.hardware2program_ref_id or ""),
                        has_address=device.address is not None,
                        estimate=needs_estimate(
                            hardware.get(device.hardware2program_ref_id or ""),
                            segment.medium_type,
                            main.medium_type,
                        ),
                    )
                    for device in segment.devices
                ]
                segments[segment.id] = bus_total(parts, len(segment.devices))
            main_load = segments[main.id]
            lines[line.id] = BusLoad(
                devices=sum(segments[s.id].devices for s in line.segments),
                current=main_load.current,
                estimated=main_load.estimated,
            )
        return lines, segments

    def check_coupler(
        self,
        project_id: str,
        segment_id: int,
        device: CouplerDevice,
        label: str,
        *,
        ip_routers: Collection[int] = (),
        moving: Collection[int] = (),
        count: int = 1,
        line_names: Mapping[int, str] | None = None,
    ) -> None:
        """Raise a ``MediumError`` unless the coupler or KNXnet/IP router ``device`` (named
        ``label``) may go to the segment (see :func:`.coupler_rules.check_coupler`). Raises
        ``KeyError`` for an unknown segment."""
        segment = self._state(project_id).session.get(Segment, segment_id)
        if segment is None:
            raise KeyError(f"No segment with id {segment_id}")
        lines = _line_views(segment.line.area.installation, line_names)
        check_coupler(
            lines,
            segment_id,
            device,
            label,
            ip_routers=ip_routers,
            moving=moving,
            count=count,
        )

    def device_bus_current(
        self, project_id: str, device_id: int, hardware: Hardware | None
    ) -> tuple[float | None, bool]:
        """A device's own bus current as its details show it (see
        :func:`.bus_load.shown_current`): its hardware's, else the estimate; None for neither.
        A device on no line is estimated as on TP. Raises ``KeyError`` for an unknown device."""
        device = self._state(project_id).session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        segment = device.segment
        if segment is None:
            estimate = needs_estimate(hardware, MEDIUM_TP, MEDIUM_TP)
        else:
            estimate = needs_estimate(
                hardware, segment.medium_type, _main_segment(segment.line).medium_type
            )
        return shown_current(hardware, estimate=estimate)

    def add_segment(
        self,
        project_id: str,
        line_id: int,
        *,
        medium_type: str | None = None,
        name: str = "",
    ) -> int:
        """Append a sub-segment to a line, numbering it after the line's existing segments. It
        gets ``medium_type``, by default the line's own when a sub-segment can have it, else TP.
        Raises ``MediumError`` when the line allows no such sub-segment or has the most segments
        a line can have, ``KeyError`` for an unknown line."""
        state = self._state(project_id)
        line = state.session.get(Line, line_id)
        if line is None:
            raise KeyError(f"No line with id {line_id}")
        if not line.segments:
            raise KeyError(f"Line {line_id} has no segment")
        main = _main_segment(line).medium_type
        default = main if main in sub_segment_media(main) else MEDIUM_TP
        if medium_type is None:
            medium_type = default
        check_new_sub_segment(main, medium_type)
        if len(line.segments) >= MAX_SEGMENTS:
            raise MediumError("too_many_segments")
        number = max(s.number for s in line.segments) + 1
        start = state.store.cursor
        event = CreateSegment(
            line_id=line_id, number=number, medium_type=default, name=name
        )
        state.store.append(event)
        assert event.segment_id is not None
        if medium_type != default:
            # Another medium as a change of it, which gives its domain address.
            self.set_segment_medium(project_id, event.segment_id, medium_type)
            state.store.group_since(start)
        return event.segment_id

    def device_segments(self, project_id: str) -> dict[int, int]:
        """Every device's segment: ``device id -> segment id`` (not the devices on no line)."""
        session = self._state(project_id).session
        return {
            device_id: segment_id
            for device_id, segment_id in session.query(Device.id, Device.segment_id)
            if segment_id is not None
        }

    def device_programs(self, project_id: str) -> dict[int, str]:
        """Every device's hardware program: ``device id -> hardware2program ref id`` (devices
        without one are left out)."""
        session = self._state(project_id).session
        return {
            device_id: program
            for device_id, program in session.query(
                Device.id, Device.hardware2program_ref_id
            ).all()
            if program
        }

    def device_placement(self, project_id: str, device_id: int) -> DevicePlacement:
        """Where a device is, and its hardware program. Raises ``KeyError`` for an unknown
        device."""
        device = self._state(project_id).session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        return DevicePlacement(
            segment_id=device.segment_id,
            line_id=device.segment.line_id if device.segment is not None else None,
            address=device.address,
            hardware2program_ref_id=device.hardware2program_ref_id,
        )

    def rename_segment(self, project_id: str, segment_id: int, name: str) -> None:
        self._state(project_id).store.append(
            RenameSegment(segment_id=segment_id, name=name)
        )

    def set_domain_address(
        self, project_id: str, segment_id: int, address: int | None = None
    ) -> int:
        """Give an RF, PL or IoT segment the domain ``address``, or a new one (None: the lowest
        free PL one, a random unused RF or IoT one). The main segment of a main line takes the
        segments of its area with the same medium along (see
        :func:`.medium.domain_address_changes`); one undo step. Returns the address. Raises
        ``MediumError`` for an address the rules refuse, ``KeyError`` for an unknown
        segment."""
        state = self._state(project_id)
        segment = state.session.get(Segment, segment_id)
        if segment is None:
            raise KeyError(f"No segment with id {segment_id}")
        lines = _line_views(segment.line.area.installation)
        if address is None:
            used = {
                s.domain_address
                for ln in lines
                for s in ln.segments
                if s.domain_address is not None
            }
            address = new_domain_address(segment.medium_type, used)
            if address is None:
                raise MediumError("domain_invalid")
        changes = domain_address_changes(lines, segment_id, address)
        events: list[Event] = [
            SetSegmentDomainAddress(segment_id=sid, domain_address=value)
            for sid, value in changes.items()
            if value != state.session.get(Segment, sid).domain_address  # type: ignore[union-attr]
        ]
        if events:
            state.store.append(
                events[0] if len(events) == 1 else CompositeEvent(events)
            )
        return address

    def set_segment_medium(
        self,
        project_id: str,
        segment_id: int,
        medium_type: str,
        *,
        couplers: Collection[int] = (),
        ip_routers: Collection[int] = (),
        line_names: Mapping[int, str] | None = None,
    ) -> None:
        """Change a segment's medium; the main segment's is the line's. The rules of
        :func:`.medium.check_medium_change` decide, with the devices that are couplers and
        KNXnet/IP routers (``couplers``, ``ip_routers``: device ids, from the catalog); their
        reasons call a line without a name as ``line_names`` (by line id) does. The domain
        addresses change with it (:func:`.medium.domain_addresses`), and a backbone that
        becomes IP no longer forces its security on; all in one undo step. Raises
        ``MediumError`` when the rules refuse it, ``KeyError`` for an unknown segment."""
        state = self._state(project_id)
        segment = state.session.get(Segment, segment_id)
        if segment is None:
            raise KeyError(f"No segment with id {segment_id}")
        if medium_type not in MEDIUM_NAMES:
            raise MediumError("unknown_medium", medium=medium_type)
        if segment.medium_type == medium_type:
            return
        installation = segment.line.area.installation
        lines = _line_views(installation, line_names)
        check_medium_change(lines, segment_id, medium_type, couplers, ip_routers)
        domains = domain_addresses(lines, segment_id, medium_type)
        events: list[Event] = [
            SetSegmentMedium(
                segment_id=segment_id,
                medium_type=medium_type,
                domain_address=domains.pop(segment_id, None),
            ),
            *(
                SetSegmentDomainAddress(segment_id=other, domain_address=address)
                for other, address in domains.items()
            ),
        ]
        line = segment.line
        attributes = installation.extra_attributes or {}
        if (
            line.area.address == 0
            and line.address == 0
            and segment.id == _main_segment(line).id
            and medium_type == MEDIUM_IP
            and attributes.get("IPRoutingBackboneSecurity") == "On"
        ):
            events.append(
                SetInstallationAttribute(
                    installation_id=installation.id,
                    name="IPRoutingBackboneSecurity",
                    value=None,
                )
            )
        state.store.append(events[0] if len(events) == 1 else CompositeEvent(events))

    def set_line_medium(
        self,
        project_id: str,
        line_id: int,
        medium_type: str,
        *,
        couplers: Collection[int] = (),
        ip_routers: Collection[int] = (),
        line_names: Mapping[int, str] | None = None,
    ) -> None:
        """Change a line's medium: its main segment's (see :meth:`set_segment_medium`)."""
        line = self._state(project_id).session.get(Line, line_id)
        if line is None or not line.segments:
            raise KeyError(f"No line with id {line_id}")
        self.set_segment_medium(
            project_id,
            _main_segment(line).id,
            medium_type,
            couplers=couplers,
            ip_routers=ip_routers,
            line_names=line_names,
        )

    def add_device(
        self,
        project_id: str,
        segment_id: int | None,
        product_ref_id: str,
        *,
        address: int | None = None,
        name: str = "",
        hardware2program_ref_id: str | None = None,
        application_program_ref_id: str | None = None,
        parameters: list[tuple[str, str]] | None = None,
        com_objects: list[tuple[str, str | None]] | None = None,
        module_instances: list[tuple[str, str]] | None = None,
        product_name: str = "",
        hardware_name: str = "",
        order_number: str = "",
        manufacturer_name: str = "",
        secure_commissioning: bool = False,
        additional_addresses: int = 0,
        space_id: int | None = None,
    ) -> int:
        """Add a device, to the space ``space_id`` when given, on no line (and without an
        individual address) for ``segment_id`` None. ``product_ref_id`` names the catalog product; ``hardware2program_ref_id`` is
        the loaded program the application resolves through. Being ref-only, this package never
        reads the catalog, so the caller expands the application and supplies ``parameters``
        (``(ref_id, value)``), ``com_objects`` (``(ref_id, channel_id)``), ``module_instances``
        (``(instance_id, ref_id)``) and the product's display fields. ``secure_commissioning``
        activates it for a device that supports KNX Data Secure, in the same undo step (see
        :meth:`set_secure_commissioning`). ``additional_addresses`` entries (the application's
        additional individual addresses) are created with the addresses
        :meth:`assign_additional_addresses` picks, in the same undo step; too few free numbers in
        the line raise :class:`AdditionalAddressError`."""
        state = self._state(project_id)
        if segment_id is None:
            address = None
        elif address is not None:
            self._check_unique_address(state, segment_id, address)
        if space_id is not None:
            space = state.session.get(Space, space_id)
            if space is None:
                raise KeyError(f"No space with id {space_id}")
            if not holds_devices(space.space_type):
                raise SpaceRuleError("no_devices")
        entries = [_empty_additional_entry() for _ in range(additional_addresses)]
        if entries and segment_id is not None:
            segment = state.session.get(Segment, segment_id)
            if segment is None:
                raise KeyError(f"No segment with id {segment_id}")
            used = self._line_numbers_in_use(state, segment.line_id)
            if address is not None:
                used.add(address)
            _fill_additional_entries(entries, used)
        tool_key: str | None = None
        if secure_commissioning:
            if state.cipher is None:
                raise ProjectPasswordRequired(
                    "a Tool Key is stored with the project password"
                )
            tool_key = state.cipher.seal(
                base64.b64encode(secrets.token_bytes(16)).decode("ascii")
            )
        event = AddDevice(
            segment_id=segment_id,
            address=address,
            name=name,
            product_ref_id=product_ref_id,
            hardware2program_ref_id=hardware2program_ref_id,
            application_program_ref_id=application_program_ref_id,
            parameters=[[ref, value] for ref, value in (parameters or [])],
            com_objects=[[ref, channel] for ref, channel in (com_objects or [])],
            module_instances=[[iid, rid] for iid, rid in (module_instances or [])],
            product_name=product_name,
            hardware_name=hardware_name,
            order_number=order_number,
            manufacturer_name=manufacturer_name,
            tool_key=tool_key,
            additional_addresses=entries,
            space_id=space_id,
        )
        state.store.append(event)
        assert event.device_id is not None
        return event.device_id

    def set_parameter(
        self, project_id: str, device_id: int, ref_id: str, value: str
    ) -> None:
        self._state(project_id).store.append(
            SetParameter(device_id=device_id, ref_id=ref_id, value=value)
        )

    def create_group_address(
        self, project_id: str, installation: int, address: int, name: str
    ) -> int:
        state = self._state(project_id)
        levels = ranges_for(address, self._style(state))
        event = CreateGroupAddress(
            installation_id=self._installation(state, installation).id,
            address=address,
            name=name,
            levels=[[start, end, range_name] for start, end, range_name in levels],
        )
        state.store.append(event)
        assert event.ga_id is not None
        return event.ga_id

    def create_group_range(
        self,
        project_id: str,
        installation: int,
        parent_id: int | None,
        name: str,
        number: int | None = None,
    ) -> int | None:
        """Create an empty group-range folder (a main or middle group) and return its id.

        ``parent_id`` ``None`` adds a main group (the next free main number); a main group's id adds
        a middle group under it (the next free middle, ThreeLevel only); ``number`` asks for that
        main or middle number. Returns ``None`` when the style has no such folders (Free, or a
        middle group in TwoLevel), the level is full or ``number`` is taken."""
        state = self._state(project_id)
        style = self._style(state)
        if style is GroupAddressStyle.FREE:
            return None
        inst = self._installation(state, installation)
        if parent_id is None:
            # Main group: lowest unused main number (0-31), range = main << 11 .. + 0x7FF.
            used = {
                rs >> 11
                for (rs,) in state.session.query(GroupRange.range_start).filter(
                    GroupRange.installation_id == inst.id,
                    GroupRange.parent_id.is_(None),
                )
            }
            number = _free_number(used, 32, number)
            if number is None:
                return None
            base = number << 11
            start, end = max(1, base), base + 0x7FF
        else:
            # Middle group under a main (ThreeLevel only): lowest unused middle (0-7).
            if style is not GroupAddressStyle.THREE_LEVEL:
                return None
            parent = state.session.get(GroupRange, parent_id)
            if parent is None:
                return None
            main = parent.range_start >> 11
            used = {
                (rs >> 8) & 0x7
                for (rs,) in state.session.query(GroupRange.range_start).filter(
                    GroupRange.installation_id == inst.id,
                    GroupRange.parent_id == parent_id,
                )
            }
            number = _free_number(used, 8, number)
            if number is None:
                return None
            base = (main << 11) + (number << 8)
            start, end = max(1, base), base + 0xFF
        event = CreateGroupRange(
            installation_id=inst.id,
            parent_id=parent_id,
            range_start=start,
            range_end=end,
            name=name,
        )
        state.store.append(event)
        return event.range_id

    def create_free_group_range(
        self,
        project_id: str,
        installation: int,
        parent_id: int | None,
        name: str,
        start: int,
        end: int,
    ) -> int:
        """Create a group range of the Free style spanning ``start`` to ``end``, at the top
        (``parent_id`` None, within 1 to 65535) or in the range ``parent_id`` (within it), and
        return its id. Raises ``ValueError`` in another style, for a span outside its place or
        one that overlaps a range beside it; ``KeyError`` for an unknown parent."""
        state = self._state(project_id)
        if self._style(state) is not GroupAddressStyle.FREE:
            raise ValueError("only the Free style has group ranges of any span")
        inst = self._installation(state, installation)
        low, high = 1, 0xFFFF
        if parent_id is not None:
            parent = state.session.get(GroupRange, parent_id)
            if parent is None:
                raise KeyError(f"No group range {parent_id}")
            low, high = parent.range_start, parent.range_end
        if not low <= start <= end <= high:
            raise ValueError(f"the range {start}-{end} is not within {low}-{high}")
        siblings = state.session.query(
            GroupRange.range_start, GroupRange.range_end
        ).filter(
            GroupRange.installation_id == inst.id,
            GroupRange.parent_id.is_(None)
            if parent_id is None
            else GroupRange.parent_id == parent_id,
        )
        if any(not (end < s or start > e) for s, e in siblings):
            raise ValueError(f"the range {start}-{end} overlaps another one")
        event = CreateGroupRange(
            installation_id=inst.id,
            parent_id=parent_id,
            range_start=start,
            range_end=end,
            name=name,
        )
        state.store.append(event)
        assert event.range_id is not None
        return event.range_id

    def create_group_address_in(
        self, project_id: str, range_id: int, address: int, name: str
    ) -> int:
        """Create the group address ``address`` in the group range ``range_id`` (which must hold
        it) and return its id. Raises ``KeyError`` for an unknown range, ``ValueError`` when the
        address is outside it."""
        state = self._state(project_id)
        group_range = state.session.get(GroupRange, range_id)
        if group_range is None:
            raise KeyError(f"No group range with id {range_id}")
        if not group_range.range_start <= address <= group_range.range_end:
            raise ValueError(f"Group address {address} is outside range {range_id}")
        chain: list[GroupRange] = []
        node: GroupRange | None = group_range
        while node is not None:
            chain.insert(0, node)
            node = node.parent
        event = CreateGroupAddress(
            installation_id=group_range.installation_id,
            address=address,
            name=name,
            levels=[[r.range_start, r.range_end, r.name] for r in chain],
        )
        state.store.append(event)
        assert event.ga_id is not None
        return event.ga_id

    def used_group_addresses(self, project_id: str, installation: int) -> set[int]:
        """The group addresses the installation has, as numbers."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        return {
            address
            for (address,) in state.session.query(GroupAddress.address)
            .join(GroupRange, GroupAddress.group_range_id == GroupRange.id)
            .filter(GroupRange.installation_id == inst.id)
        }

    def root_group_ranges(self, project_id: str, installation: int) -> list[GroupRange]:
        """The installation's top group ranges (its main groups), by their start."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        return (
            state.session.query(GroupRange)
            .filter(
                GroupRange.installation_id == inst.id, GroupRange.parent_id.is_(None)
            )
            .order_by(GroupRange.range_start)
            .all()
        )

    def set_fields(self, project_id: str, rows: list[list[Any]]) -> None:
        """Set stored fields of several rows in one step (see :class:`.SetFields`)."""
        if rows:
            self._state(project_id).store.append(SetFields(rows=rows))

    def row[T: Base](self, project_id: str, model: type[T], row_id: int) -> T | None:
        """The stored row ``row_id`` of ``model``, None when there is none."""
        return self._state(project_id).session.get(model, row_id)

    def rename_group_range(self, project_id: str, range_id: int, name: str) -> None:
        self._state(project_id).store.append(
            RenameGroupRange(range_id=range_id, name=name)
        )

    def remove_group_range(self, project_id: str, range_id: int) -> None:
        self._state(project_id).store.append(RemoveGroupRange(target_id=range_id))

    def import_group_addresses(
        self,
        project_id: str,
        installation: int,
        export: GaExport,
        label: str | None = None,
    ) -> ImportGroupAddresses:
        """Import ``export`` (a parsed group address file) into the project as one undo step.

        Folders and addresses are added; existing ones get the file's names and values (empty
        values keep what is there). An address outside every folder gets the default folders a new
        address would get. Raises ``ValueError`` when the file uses another address style."""
        state = self._state(project_id)
        style = self._style(state)
        if export.style is not style:
            raise GroupAddressStyleMismatch(export.style, style)
        inst = self._installation(state, installation)
        ranges: list[list[Any]] = []

        def add_range(gr: GaRange, parent_start: int | None) -> None:
            ranges.append(
                [gr.range_start, gr.range_end, gr.name, parent_start, gr.security]
            )
            for child in gr.children:
                add_range(child, gr.range_start)

        for gr in export.ranges:
            add_range(gr, None)
        windows = [(entry[0], entry[1]) for entry in ranges] + [
            (start, end)
            for start, end in state.session.query(
                GroupRange.range_start, GroupRange.range_end
            ).filter(GroupRange.installation_id == inst.id)
        ]
        for addr in export.addresses:
            if any(start <= addr.address <= end for start, end in windows):
                continue
            parent_start: int | None = None
            for start, end, name in ranges_for(addr.address, style):
                if (start, end) not in windows:
                    ranges.append([start, end, name, parent_start])
                    windows.append((start, end))
                parent_start = start

        event = ImportGroupAddresses(
            installation_id=inst.id,
            ranges=ranges,
            addresses=[
                {
                    "address": addr.address,
                    "name": addr.name or None,
                    "datapoint_type": addr.datapoint_type,
                    "description": addr.description or None,
                    "security_mode": addr.security,
                    "central": addr.central,
                    "unfiltered": addr.unfiltered,
                }
                for addr in export.addresses
            ],
            label=label,
        )
        state.store.append(event)
        return event

    def link_com_object(
        self,
        project_id: str,
        com_object_id: int,
        group_address_id: int,
        *,
        sending: bool = False,
        derive_datapoint_type: str | None = None,
    ) -> int:
        """Attach a com-object to a group address; ``sending`` makes it the transmitting link. Use
        :meth:`set_com_object_sending` to move the sender among a com-object's links.

        When ``derive_datapoint_type`` is given, the link and the resulting group-address
        datapoint type are recorded as one undo step, so typing a group address
        from the object it is linked to stays one action."""
        state = self._state(project_id)
        self._check_secure_link(state, com_object_id, group_address_id)
        event = LinkComObject(
            com_object_id=com_object_id,
            group_address_id=group_address_id,
            is_sending=sending,
        )
        if derive_datapoint_type:
            state.store.append(
                CompositeEvent(
                    events=[
                        event,
                        SetGroupAddressDatapointType(
                            group_address_id=group_address_id,
                            datapoint_type=normalize_datapoint_type(
                                derive_datapoint_type
                            ),
                        ),
                    ]
                )
            )
        else:
            state.store.append(event)
        assert event.link_id is not None
        return event.link_id

    @staticmethod
    def _check_secure_link(
        state: _Open, com_object_id: int, group_address_id: int
    ) -> None:
        """Refuse linking a device that is not commissioned securely to a group address with
        Security ``On`` (``GroupAddressSecurityError``)."""
        ga = state.session.get(GroupAddress, group_address_id)
        com_object = state.session.get(ComObject, com_object_id)
        if ga is None or com_object is None or effective_mode(ga) != "On":
            return
        device = state.session.get(Device, com_object.device_id)
        if device is not None and not secure_commissioned(device):
            raise GroupAddressSecurityError(
                "a secured group address can only be linked to devices commissioned securely"
            )

    def set_com_object_sending(self, project_id: str, link_id: int) -> None:
        """Set this link as the com-object's sender, clearing the bit on its siblings."""
        self._state(project_id).store.append(SetComObjectSending(link_id=link_id))

    def set_com_object_flag(
        self, project_id: str, com_object_id: int, flag: str, value: bool | None
    ) -> None:
        """Force a com-object flag (``value`` overrides; ``None`` restores the product default).

        ``flag`` must be one of :data:`~xknxeditor.proj.core.events.COM_OBJECT_FLAGS`."""
        if flag not in COM_OBJECT_FLAGS:
            raise ValueError(f"Unknown com-object flag {flag!r}")
        self._state(project_id).store.append(
            SetComObjectFlag(com_object_id=com_object_id, flag=flag, value=value)
        )

    def set_com_object_datapoint_type(
        self, project_id: str, com_object_id: int, datapoint_type: str | None
    ) -> None:
        """Pin (or clear with ``None``) the per-instance DatapointType of a group object. The value
        is normalized to ETS token form (``DPST-5-1`` / ``DPT-5``); an unparseable value raises
        ``ValueError`` rather than being stored."""
        self._state(project_id).store.append(
            SetComObjectDatapointType(
                com_object_id=com_object_id,
                datapoint_type=normalize_datapoint_type(datapoint_type),
            )
        )

    def set_com_object_priority(
        self, project_id: str, com_object_id: int, priority: str | None
    ) -> None:
        """Set (or clear with ``None``, inherit the default) the per-instance Priority of a group
        object. ``priority`` must be one of
        :data:`~xknxeditor.proj.core.events.COM_OBJECT_PRIORITIES`."""
        if priority is not None and priority not in COM_OBJECT_PRIORITIES:
            raise ValueError(f"Unknown com-object priority {priority!r}")
        self._state(project_id).store.append(
            SetComObjectPriority(com_object_id=com_object_id, priority=priority)
        )

    def set_com_object_description(
        self, project_id: str, com_object_id: int, description: str | None
    ) -> None:
        """Set (or clear with an empty/``None`` value, inherit the default) the per-instance
        Description of a group object."""
        self._state(project_id).store.append(
            SetComObjectDescription(
                com_object_id=com_object_id, description=description or None
            )
        )

    def set_group_address_datapoint_type(
        self, project_id: str, group_address_id: int, datapoint_type: str | None
    ) -> None:
        """Set (or clear with ``None``) a group address' datapoint type.

        The value is normalized to ETS token form (``DPST-1-1`` / ``DPT-5``); a value
        that cannot be parsed raises :class:`ValueError` rather than being stored, so a
        dotted ``1.001`` never reaches the exported archive (ETS fails to open it)."""
        self._state(project_id).store.append(
            SetGroupAddressDatapointType(
                group_address_id=group_address_id,
                datapoint_type=normalize_datapoint_type(datapoint_type),
            )
        )

    def set_group_address_security(
        self, project_id: str, group_address_id: int, mode: str | None
    ) -> None:
        """Set a group address's ``@Security`` mode (``"Auto"``/``"On"``/``"Off"``, ``None`` =
        default; see :mod:`.ga_security`). Securing one for the first time generates a random AES
        key, stored encrypted, so the project needs a password (:class:`ProjectPasswordRequired`
        otherwise); the key is kept once made, so toggling the mode does not change it across the
        installation. ``On`` is refused (``GroupAddressSecurityError``) while a linked device is
        not commissioned securely. Below a group that forces its Security, the mode is kept for
        when the group no longer does, and changes nothing until then."""
        if mode not in AUTO_MODES and mode not in FORCED_MODES:
            raise ValueError(f"Unknown group address security mode {mode!r}")
        state = self._state(project_id)
        ga = state.session.get(GroupAddress, group_address_id)
        if ga is None:
            raise KeyError(f"No group address with id {group_address_id}")
        forcing = forcing_range(ga.group_range)
        effective = mode if forcing is None else forcing.security_mode
        devices = linked_devices(state.session).get(ga.id, set())
        commissioned = commissioned_devices(state.session)
        if forcing is None and mode == "On" and not devices <= commissioned:
            raise GroupAddressSecurityError(
                "a secured group address can only be linked to devices commissioned securely"
            )
        secured = is_secured(effective, devices, commissioned)
        key = ga.security_key
        if secured and key is None:
            key = self._new_group_key(state)
        state.store.append(
            SetGroupAddressSecurity(
                group_address_id=group_address_id,
                security_mode=mode,
                data_secure=secured,
                security_key=key,
            )
        )

    def set_group_range_security(
        self, project_id: str, group_range_id: int, mode: str | None
    ) -> None:
        """Set a main or middle group's ``@Security``: ``"On"``/``"Off"`` force it on every group
        address and group below it, ``"Auto"`` or ``None`` leave them their own (see
        :mod:`.ga_security`). Forcing ``On`` is refused (``GroupAddressSecurityError``) while a
        device linked below it is not commissioned securely; a group address it secures gets
        its key in the same undo step, so the project then needs a password
        (:class:`ProjectPasswordRequired`)."""
        if mode not in AUTO_MODES and mode not in FORCED_MODES:
            raise ValueError(f"Unknown group range security mode {mode!r}")
        state = self._state(project_id)
        session = state.session
        group_range = session.get(GroupRange, group_range_id)
        if group_range is None:
            raise KeyError(f"No group range with id {group_range_id}")
        if (group_range.security_mode or "Auto") == (mode or "Auto"):
            return
        addresses = addresses_below(group_range)
        # The modes that count once the group has ``mode``: tried on the group in memory only.
        old_mode = group_range.security_mode
        with session.no_autoflush:
            group_range.security_mode = mode
            after = {ga.id: effective_mode(ga) for ga in addresses}
            group_range.security_mode = old_mode
        linked = linked_devices(session)
        commissioned = commissioned_devices(session)
        if any(
            after[ga.id] == "On" and not linked.get(ga.id, set()) <= commissioned
            for ga in addresses
        ):
            raise GroupAddressSecurityError(
                "a secured group address can only be linked to devices commissioned securely"
            )
        keys = {
            ga.id: self._new_group_key(state)
            for ga in addresses
            if ga.security_key is None
            and is_secured(after[ga.id], linked.get(ga.id, ()), commissioned)
        }
        state.store.append(
            SetGroupRangeSecurity(
                group_range_id=group_range_id, security_mode=mode, keys=keys
            )
        )

    def set_group_address_central(
        self, project_id: str, group_address_id: int, central: bool
    ) -> None:
        """Mark a group address as central or not. Raises ``KeyError`` for an unknown one."""
        state = self._state(project_id)
        ga = state.session.get(GroupAddress, group_address_id)
        if ga is None:
            raise KeyError(f"No group address with id {group_address_id}")
        if ga.central == central:
            return
        state.store.append(
            SetGroupAddressCentral(group_address_id=group_address_id, central=central)
        )

    @staticmethod
    def _new_group_key(state: _Open) -> str:
        """A new random group address key, sealed with the project password."""
        if state.cipher is None:
            raise ProjectPasswordRequired(
                "a group address key is stored with the project password"
            )
        return state.cipher.seal(base64.b64encode(secrets.token_bytes(16)).decode())

    # --- building functions -----------------------------------------------

    def create_function(
        self, project_id: str, space_id: int, function_type: str, name: str
    ) -> int:
        """Create a building function in a space; returns the new function id."""
        state = self._state(project_id)
        event = CreateFunction(
            space_id=space_id, function_type=function_type, name=name
        )
        state.store.append(event)
        assert event.function_id is not None
        return event.function_id

    def remove_function(self, project_id: str, function_id: int) -> None:
        self._state(project_id).store.append(RemoveFunction(target_id=function_id))

    def move_function(self, project_id: str, function_id: int, space_id: int) -> None:
        """Put a building function into another space, after its functions; undoable. Nothing
        changes when it is in that space already. Raises ``KeyError`` for an unknown function or
        space."""
        state = self._state(project_id)
        function = state.session.get(Function, function_id)
        space = state.session.get(Space, space_id)
        if function is None or space is None:
            raise KeyError(f"No function {function_id} or space {space_id}")
        if function.space_id == space_id:
            return
        order = max((f.order for f in space.functions), default=-1) + 1
        state.store.append(
            MoveFunction(function_id=function_id, space_id=space_id, new_order=order)
        )

    def rename_function(self, project_id: str, function_id: int, name: str) -> None:
        self._state(project_id).store.append(
            RenameFunction(function_id=function_id, name=name)
        )

    def set_function_type(
        self, project_id: str, function_id: int, function_type: str
    ) -> None:
        self._state(project_id).store.append(
            SetFunctionType(function_id=function_id, function_type=function_type)
        )

    def set_function_description(
        self, project_id: str, function_id: int, description: str
    ) -> None:
        self._state(project_id).store.append(
            SetFunctionDescription(function_id=function_id, description=description)
        )

    def add_function_group_address(
        self, project_id: str, function_id: int, group_address_id: int, role: str = ""
    ) -> int:
        """Assign a group address to a function under ``role``; returns the new link id."""
        state = self._state(project_id)
        event = AddFunctionGroupAddress(
            function_id=function_id, group_address_id=group_address_id, role=role
        )
        state.store.append(event)
        assert event.link_id is not None
        return event.link_id

    def remove_function_group_address(self, project_id: str, link_id: int) -> None:
        self._state(project_id).store.append(
            RemoveFunctionGroupAddress(target_id=link_id)
        )

    def function_candidates(
        self, project_id: str, installation: int
    ) -> list[FunctionCandidate]:
        """Suggest building functions inferred from device-channel group-address wiring.

        Each actuator output channel of a device placed in a room becomes a candidate when its
        linked group addresses match a known shape (switchable/dimmable light or blind). To keep the
        suggestions clean on real projects:

        - Channel-independent objects are ignored. A load lives on a named channel; the
          channel-independent block holds device-wide and central objects (time, day/night, …),
          and sensors dump many unrelated functions there.
        - Central/shared group addresses (linked across many devices, e.g. time/date/day-night/
          weather) are dropped before classifying and naming, so they neither pollute a load's
          address set nor end up as its name.
        - The name comes from the channel's sending object.

        Channels whose remaining group addresses already belong to a function, and devices not
        placed in a room, are skipped; identical group-address sets are suggested only once."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        style = self._style(state)

        assigned = {
            gid
            for (gid,) in state.session.query(FunctionGroupAddress.group_address_id)
            .join(Function, FunctionGroupAddress.function_id == Function.id)
            .join(Space, Function.space_id == Space.id)
            .filter(Space.installation_id == inst.id)
            .all()
        }

        rows = (
            state.session.query(ComObjectLink, ComObject, GroupAddress)
            .join(ComObject, ComObjectLink.com_object_id == ComObject.id)
            .join(GroupAddress, ComObjectLink.group_address_id == GroupAddress.id)
            .join(Device, ComObject.device_id == Device.id)
            .join(Segment, Device.segment_id == Segment.id)
            .join(Line, Segment.line_id == Line.id)
            .join(Area, Line.area_id == Area.id)
            .filter(Area.installation_id == inst.id, Device.space_id.isnot(None))
            .all()
        )

        # A GA linked across many devices is a central/shared function (time, date, day/night,
        # weather), not part of a single load; drop it from every candidate.
        devices_per_ga: dict[int, set[int]] = {}
        for _link, com_object, ga in rows:
            devices_per_ga.setdefault(ga.id, set()).add(com_object.device_id)

        # Group the wired GAs by actuator channel, preserving first-seen order per GA, and remember
        # which GAs the channel sends on (for naming). Channel-independent objects are skipped.
        groups: dict[tuple[int, str], dict[int, GroupAddress]] = {}
        sending: dict[tuple[int, str], set[int]] = {}
        for link, com_object, ga in rows:
            if com_object.channel_id is None:
                continue
            key = (com_object.device_id, com_object.channel_id)
            groups.setdefault(key, {})[ga.id] = ga
            if link.is_sending:
                sending.setdefault(key, set()).add(ga.id)

        candidates: list[FunctionCandidate] = []
        seen_sets: set[frozenset[int]] = set()
        for (device_id, channel_id), gas in groups.items():
            members = [
                ga
                for gid, ga in gas.items()
                if len(devices_per_ga.get(gid, ())) < _CENTRAL_GA_MIN_DEVICES
            ]
            ga_ids = [ga.id for ga in members]
            if not ga_ids or any(gid in assigned for gid in ga_ids):
                continue
            key = frozenset(ga_ids)
            if key in seen_sets:
                continue
            function_type = classify_function([ga.datapoint_type for ga in members])
            if function_type is None:
                continue
            seen_sets.add(key)
            device = state.session.get(Device, device_id)
            if device is None or device.space_id is None:
                continue
            texts = [
                f"{ga.name} ({format_ga(ga.address, style)})"
                if ga.name
                else format_ga(ga.address, style)
                for ga in members
            ]
            send_ids = sending.get((device_id, channel_id), set())
            name = (
                next((ga.name for ga in members if ga.id in send_ids and ga.name), "")
                or next((ga.name for ga in members if ga.name), "")
                or device.name
            )
            candidates.append(
                FunctionCandidate(
                    device_id=device_id,
                    channel_id=channel_id,
                    name=name,
                    function_type=function_type,
                    space_id=device.space_id,
                    space_name=device.space.name if device.space is not None else "",
                    device_name=device.name,
                    group_address_ids=ga_ids,
                    group_address_texts=texts,
                )
            )
        candidates.sort(key=lambda c: (c.device_name, c.channel_id or "", c.name))
        return candidates

    def accept_function_candidate(
        self, project_id: str, candidate: FunctionCandidate
    ) -> int:
        """Create the suggested function and link its group addresses as one undoable step."""
        state = self._state(project_id)
        event = CreateFunctionWithGroupAddresses(
            space_id=candidate.space_id,
            function_type=candidate.function_type,
            name=candidate.name,
            group_address_ids=list(candidate.group_address_ids),
        )
        state.store.append(event)
        assert event.function_id is not None
        return event.function_id

    # --- building spaces (location tree) ----------------------------------

    def create_space(
        self,
        project_id: str,
        installation: int,
        space_type: str,
        name: str,
        parent_id: int | None = None,
        usage: str = "",
        usage_text: str = "",
    ) -> int:
        """Create a building space (building/floor/room/…) under ``parent_id`` (or at the top level
        when ``None``), a room with its ``usage`` (a SpaceUsage id) and its text. New siblings are
        appended after the existing ones; returns the new id."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        parent = state.session.get(Space, parent_id) if parent_id is not None else None
        if parent_id is not None and parent is None:
            raise KeyError(f"No space with id {parent_id}")
        check_child(parent.space_type if parent is not None else None, space_type)
        highest = (
            state.session.query(func.max(Space.order))
            .filter(Space.installation_id == inst.id, Space.parent_id == parent_id)
            .scalar()
        )
        order = highest + 1 if highest is not None else 0
        event = CreateSpace(
            installation_id=inst.id,
            space_type=space_type,
            name=name,
            parent_id=parent_id,
            order=order,
            usage=usage,
            usage_text=usage_text,
        )
        state.store.append(event)
        assert event.space_id is not None
        return event.space_id

    def rename_space(self, project_id: str, space_id: int, name: str) -> None:
        self._state(project_id).store.append(RenameSpace(space_id=space_id, name=name))

    def set_space_number(self, project_id: str, space_id: int, number: str) -> None:
        """Set the number of a space, a free text."""
        self._state(project_id).store.append(
            SetSpaceNumber(space_id=space_id, number=number)
        )

    def set_space_usage(
        self, project_id: str, space_id: int, usage: str, usage_text: str = ""
    ) -> None:
        """Set a room's use: a SpaceUsage id of the master data, or "" for none, with its text."""
        self._state(project_id).store.append(
            SetSpaceUsage(space_id=space_id, usage=usage, usage_text=usage_text)
        )

    def set_space_default_line(
        self, project_id: str, space_id: int, line_id: int | None
    ) -> None:
        """Set a space's Current Line, or clear it with ``line_id`` None. Raises ``ValueError``
        for an unknown space, or a line that is not in the space's installation."""
        state = self._state(project_id)
        space = state.session.get(Space, space_id)
        if space is None:
            raise ValueError(f"unknown space {space_id}")
        if line_id is not None:
            line = state.session.get(Line, line_id)
            if line is None or line.area.installation_id != space.installation_id:
                raise ValueError(f"unknown line {line_id}")
        if space.default_line_id == line_id:
            return
        state.store.append(SetSpaceDefaultLine(space_id=space_id, line_id=line_id))

    def current_line(self, project_id: str, space_id: int) -> CurrentLine | None:
        """The Current Line of a space: its own, else that of the nearest space above it that has
        one; None when none has. ``CurrentLine.space_id`` is the space that sets it."""
        space = self._state(project_id).session.get(Space, space_id)
        while space is not None:
            if space.default_line_id is not None:
                return CurrentLine(line_id=space.default_line_id, space_id=space.id)
            space = space.parent
        return None

    def current_line_segment(self, project_id: str, space_id: int) -> int | None:
        """The main segment of the space's Current Line (see :meth:`current_line`), where a device
        added to the space is placed; None when the space has no Current Line."""
        current = self.current_line(project_id, space_id)
        if current is None:
            return None
        line = self._state(project_id).session.get(Line, current.line_id)
        if line is None or not line.segments:
            return None
        return min(line.segments, key=lambda s: (s.number, s.id)).id

    def set_space_description(
        self, project_id: str, space_id: int, description: str
    ) -> None:
        self._state(project_id).store.append(
            SetSpaceDescription(space_id=space_id, description=description)
        )

    def move_space(
        self, project_id: str, space_id: int, new_parent_id: int | None
    ) -> None:
        """Put a space below another one (``new_parent_id`` None: at the top level). Raises
        ``SpaceRuleError`` for a target that cannot hold its type (see
        :func:`.space_rules.check_child`) or that is the space itself or below it, ``KeyError``
        for an unknown space."""
        state = self._state(project_id)
        space = state.session.get(Space, space_id)
        target = (
            state.session.get(Space, new_parent_id)
            if new_parent_id is not None
            else None
        )
        if space is None or (new_parent_id is not None and target is None):
            raise KeyError(
                f"No space with id {space_id if space is None else new_parent_id}"
            )
        ancestor = target
        while ancestor is not None:
            if ancestor.id == space_id:
                raise SpaceRuleError("target_is_child")
            ancestor = ancestor.parent
        check_child(target.space_type if target is not None else None, space.space_type)
        if space.parent_id == new_parent_id:
            return
        state.store.append(MoveSpace(space_id=space_id, new_parent_id=new_parent_id))

    def remove_space(self, project_id: str, space_id: int) -> None:
        self._state(project_id).store.append(RemoveSpace(target_id=space_id))

    # --- trades ---------------------------------------------------------------------------

    def trade_tree(self, project_id: str, installation: int) -> list[TradeInfo]:
        """The installation's trades (top level first, each with its sub-trades and devices)."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        roots = (
            state.session.query(Trade)
            .filter(Trade.installation_id == inst.id, Trade.parent_id.is_(None))
            .order_by(Trade.order, Trade.id)
            .all()
        )
        return [self._trade_info(trade) for trade in roots]

    def _trade_info(self, trade: Trade) -> TradeInfo:
        return TradeInfo(
            id=trade.id,
            name=trade.name,
            number=trade.number,
            description=trade.description,
            children=[
                self._trade_info(child)
                for child in sorted(trade.children, key=lambda t: (t.order, t.id))
            ],
            devices=[
                TradeDeviceInfo(link.id, self._space_device_info(link.device))
                for link in sorted(trade.devices, key=lambda d: (d.order, d.id))
            ],
        )

    def create_trade(
        self,
        project_id: str,
        installation: int,
        name: str,
        parent_id: int | None = None,
    ) -> int:
        """Create a trade below ``parent_id`` (at the top level for None), after its siblings;
        returns its id. Raises ``KeyError`` for an unknown parent."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        if parent_id is not None and state.session.get(Trade, parent_id) is None:
            raise KeyError(f"No trade with id {parent_id}")
        event = CreateTrade(
            installation_id=inst.id,
            name=name,
            parent_id=parent_id,
            order=self._next_trade_order(state, inst.id, parent_id),
        )
        state.store.append(event)
        assert event.trade_id is not None
        return event.trade_id

    @staticmethod
    def _next_trade_order(
        state: _Open, installation_id: int, parent_id: int | None
    ) -> int:
        highest = (
            state.session.query(func.max(Trade.order))
            .filter(
                Trade.installation_id == installation_id, Trade.parent_id == parent_id
            )
            .scalar()
        )
        return highest + 1 if highest is not None else 0

    def set_trade_field(
        self, project_id: str, trade_id: int, field: str, value: str
    ) -> None:
        """Set a trade's ``field`` ("name", "number" or "description"); one undo step, none when
        it does not change. Raises ``KeyError`` for an unknown trade, ``ValueError`` for another
        field."""
        if field not in TRADE_FIELDS:
            raise ValueError(f"Unknown trade field {field!r}")
        state = self._state(project_id)
        trade = state.session.get(Trade, trade_id)
        if trade is None:
            raise KeyError(f"No trade with id {trade_id}")
        if getattr(trade, field) != value:
            state.store.append(
                SetTradeField(trade_id=trade_id, field=field, value=value)
            )

    def move_trade(
        self, project_id: str, trade_id: int, new_parent_id: int | None
    ) -> None:
        """Put a trade below another one (None: at the top level), after its new siblings.
        Raises ``ValueError`` for a move into itself or a trade below it, ``KeyError`` for an
        unknown trade."""
        state = self._state(project_id)
        trade = state.session.get(Trade, trade_id)
        if trade is None:
            raise KeyError(f"No trade with id {trade_id}")
        ancestor = (
            state.session.get(Trade, new_parent_id)
            if new_parent_id is not None
            else None
        )
        if new_parent_id is not None and ancestor is None:
            raise KeyError(f"No trade with id {new_parent_id}")
        while ancestor is not None:
            if ancestor.id == trade_id:
                raise ValueError("cannot move a trade into itself or a trade below it")
            ancestor = ancestor.parent
        if trade.parent_id == new_parent_id:
            return
        state.store.append(
            MoveTrade(
                trade_id=trade_id,
                new_parent_id=new_parent_id,
                new_order=self._next_trade_order(
                    state, trade.installation_id, new_parent_id
                ),
            )
        )

    def remove_trade(self, project_id: str, trade_id: int) -> None:
        """Delete a trade with its sub-trades; their devices stay in the project."""
        self._state(project_id).store.append(RemoveTrade(target_id=trade_id))

    def add_trade_device(self, project_id: str, trade_id: int, device_id: int) -> None:
        """Put a device into a trade, out of the one it was in (a device is in one trade at
        most); one undo step, nothing when it is in that trade already. Raises ``KeyError``
        for an unknown trade or device."""
        state = self._state(project_id)
        trade = state.session.get(Trade, trade_id)
        if trade is None:
            raise KeyError(f"No trade with id {trade_id}")
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        if any(link.device_id == device_id for link in trade.devices):
            return
        order = max((link.order for link in trade.devices), default=-1) + 1
        events: list[Event] = [
            RemoveTradeDevice(target_id=link.id) for link in device.trade_links
        ]
        events.append(
            AddTradeDevice(trade_id=trade_id, device_id=device_id, order=order)
        )
        state.store.append(events[0] if len(events) == 1 else CompositeEvent(events))

    def remove_trade_device(
        self, project_id: str, trade_id: int, device_id: int
    ) -> None:
        """Take a device out of a trade (the device stays in the project)."""
        state = self._state(project_id)
        trade = state.session.get(Trade, trade_id)
        link = next(
            (
                ln
                for ln in (trade.devices if trade else [])
                if ln.device_id == device_id
            ),
            None,
        )
        if link is not None:
            state.store.append(RemoveTradeDevice(target_id=link.id))

    def set_device_space(
        self, project_id: str, device_id: int, space_id: int | None
    ) -> None:
        """Assign a device to a space (a stairway, room, corridor or cabinet), or unassign it
        (``space_id`` ``None``). Raises ``SpaceRuleError`` for a space that holds no devices,
        ``KeyError`` for an unknown space."""
        state = self._state(project_id)
        if space_id is not None:
            space = state.session.get(Space, space_id)
            if space is None:
                raise KeyError(f"No space with id {space_id}")
            if not holds_devices(space.space_type):
                raise SpaceRuleError("no_devices")
        state.store.append(SetDeviceSpace(device_id=device_id, space_id=space_id))

    def set_device_commissioning(
        self,
        project_id: str,
        device_id: int,
        *,
        serial_number: str | None = None,
        last_download: str | None = None,
        individual_address_loaded: bool | None = None,
        application_program_loaded: bool | None = None,
        communication_part_loaded: bool | None = None,
        medium_config_loaded: bool | None = None,
        parameters_loaded: bool | None = None,
        mac_address: str | None = None,
        download: bool = False,
        ip_address: str | None = None,
    ) -> None:
        """Update a device's commissioning state (loaded ticks, serial, last download, the MAC
        address read from an IP device and the IP address it was found at).

        Each field is optional; ``None`` leaves it unchanged. Typically called after programming to
        record what was loaded (``download``: then its "Last Modified" stays), and by the
        importer's round-trip (the ``.knxproj`` carries it)."""
        self._state(project_id).store.append(
            SetDeviceCommissioning(
                device_id=device_id,
                serial_number=serial_number,
                last_download=last_download,
                individual_address_loaded=individual_address_loaded,
                application_program_loaded=application_program_loaded,
                communication_part_loaded=communication_part_loaded,
                medium_config_loaded=medium_config_loaded,
                parameters_loaded=parameters_loaded,
                mac_address=mac_address,
                download=download,
                ip_address=ip_address,
            )
        )

    # --- remove / rename / move -------------------------------------------

    def set_device_ip_config(
        self,
        project_id: str,
        device_id: int,
        *,
        assign: str | None = None,
        ip_address: str | None = None,
        subnet_mask: str | None = None,
        default_gateway: str | None = None,
        tool_managed: bool = True,
        label: str | None = None,
    ) -> bool:
        """Change the IP settings of an IP device in one undo step; ``None`` leaves a value.

        ``assign`` is "Auto" or "Fixed"; the addresses are read by
        :func:`~xknxeditor.proj.core.ip_settings.parse_ipv4` and stored dotted. Changing
        ``assign`` keeps the addresses. When the application leaves its IP configuration to the
        tool (``tool_managed``), a fixed assignment marks the IP configuration as not
        downloaded. Returns whether anything changed. Raises ``ValueError`` for an unknown
        assignment or an address that cannot be read."""
        state = self._state(project_id)
        device = self._require_device(state, device_id)
        current = device.ip_config or {}
        if assign is not None and assign not in ASSIGNMENTS:
            raise ValueError(f"unknown IP assignment: {assign!r}")
        old_assign = current.get("Assign") or ASSIGN_AUTO
        new_assign = assign or old_assign
        addresses: dict[str, str | None] = {}
        for key, value in (
            ("IPAddress", ip_address),
            ("SubnetMask", subnet_mask),
            ("DefaultGateway", default_gateway),
        ):
            addresses[key] = current.get(key) if value is None else parse_ipv4(value)
        if new_assign == old_assign and all(
            addresses[key] == current.get(key) for key in addresses
        ):
            return False
        state.store.append(
            SetDeviceIpConfig(
                device_id=device_id,
                assign=new_assign,
                ip_address=addresses["IPAddress"],
                subnet_mask=addresses["SubnetMask"],
                default_gateway=addresses["DefaultGateway"],
                clear_medium_config=tool_managed and new_assign == ASSIGN_FIXED,
                label=label,
            )
        )
        return True

    def remove_device(self, project_id: str, device_id: int) -> None:
        self._state(project_id).store.append(RemoveDevice(target_id=device_id))

    def remove_area(self, project_id: str, area_id: int) -> None:
        self._state(project_id).store.append(RemoveArea(target_id=area_id))

    def remove_line(self, project_id: str, line_id: int) -> None:
        self._state(project_id).store.append(RemoveLine(target_id=line_id))

    def remove_segment(self, project_id: str, segment_id: int) -> None:
        """Remove a sub-segment with its devices. Raises ``MediumError`` for a line's main
        segment."""
        state = self._state(project_id)
        segment = state.session.get(Segment, segment_id)
        if segment is not None and _main_segment(segment.line).id == segment.id:
            raise MediumError("main_segment")
        state.store.append(RemoveSegment(target_id=segment_id))

    def remove_group_address(self, project_id: str, group_address_id: int) -> None:
        self._state(project_id).store.append(
            RemoveGroupAddress(target_id=group_address_id)
        )

    def unlink_com_object(self, project_id: str, link_id: int) -> None:
        self._state(project_id).store.append(UnlinkComObject(target_id=link_id))

    def rename_area(self, project_id: str, area_id: int, name: str) -> None:
        self._state(project_id).store.append(RenameArea(area_id=area_id, name=name))

    def set_project_name(self, project_id: str, name: str) -> None:
        self._state(project_id).store.append(
            SetProjectName(project_id=project_id, name=name)
        )

    def set_project_password(self, project_id: str, password: str | None) -> None:
        """Set or change the project password, or remove it with ``None``. Every secret of the
        project, the undo history included, is encrypted again with the new password (or stored in
        the clear without one), so this is not an undoable step. A project with KNX Secure data
        keeps a password: removing it raises :class:`ValueError`."""
        state = self._state(project_id)
        if not password and has_secure_data(state.session):
            raise ValueError("a project with KNX Secure data needs a password")
        salt = new_salt() if password else None
        cipher = ProjectCipher.derive(password, salt) if password and salt else None
        try:
            recrypt(state.session, state.cipher, cipher)
            project = state.session.query(Project).one()
            project.password_salt = salt
            project.password_check = cipher.check_value() if cipher else None
            state.session.commit()
        except Exception:
            state.session.rollback()
            raise
        state.cipher = cipher
        state.password = password or None

    def is_password_protected(self, project_id: str) -> bool:
        return self._state(project_id).cipher is not None

    def project_password(self, project_id: str) -> str | None:
        """The password of the open project, ``None`` when it is not protected."""
        return self._state(project_id).password

    def needs_password(self, project_id: str) -> bool:
        """Whether the project holds KNX Secure data but no password protects it yet."""
        state = self._state(project_id)
        return state.cipher is None and has_secure_data(state.session)

    def reveal(self, project_id: str, data: bytes) -> bytes:
        """Serialized XML of the project with its encrypted secrets in the clear."""
        cipher = self._state(project_id).cipher
        return cipher.reveal(data) if cipher is not None else data

    def has_secure_data(self, project_id: str) -> bool:
        """Whether the project holds KNX Secure data (see :func:`project_secrets.has_secure_data`)."""
        return has_secure_data(self._state(project_id).session)

    def device_certificates(self, project_id: str) -> dict[str, Fdsk]:
        """The project's device certificates by serial number (base64, as on the devices)."""
        state = self._state(project_id)
        project = state.session.query(Project).one()
        raw = (project.info_subtrees or {}).get("DeviceCertificates")
        if not raw:
            return {}
        result: dict[str, Fdsk] = {}
        for element in ET.fromstring(raw).iter("DeviceCertificate"):
            serial, code = element.get("SerialNumber"), element.get("FDSK")
            if not serial or not code:
                continue
            if state.cipher is not None:
                code = state.cipher.value(code) or ""
            try:
                result[serial] = parse_fdsk(code)
            except InvalidFdskError:
                continue
        return result

    def remove_device_certificate(self, project_id: str, serial_number: bytes) -> None:
        """Remove the device certificate of ``serial_number`` (one undo step)."""
        state = self._state(project_id)
        project = state.session.query(Project).one()
        raw = (project.info_subtrees or {}).get("DeviceCertificates")
        if not raw:
            return
        root = ET.fromstring(raw)
        serial = base64.b64encode(serial_number).decode("ascii")
        removed = [e for e in root if e.get("SerialNumber") == serial]
        if not removed:
            return
        for element in removed:
            root.remove(element)
        state.store.append(
            SetDeviceCertificates(
                project_id=project.id,
                certificates=ET.tostring(root, encoding="unicode"),
            )
        )

    def add_device_certificate(self, project_id: str, code: str) -> Fdsk:
        """Add (or replace) the certificate of the device with the serial number in the FDSK label
        ``code``; the FDSK is stored encrypted, so the project needs a password
        (:class:`ProjectPasswordRequired` otherwise). Raises ``InvalidFdskError`` for a bad code."""
        state = self._state(project_id)
        fdsk = parse_fdsk(code)
        if state.cipher is None:
            raise ProjectPasswordRequired("an FDSK is stored with the project password")
        project = state.session.query(Project).one()
        raw = (project.info_subtrees or {}).get("DeviceCertificates")
        root = ET.fromstring(raw) if raw else ET.Element("DeviceCertificates")
        serial = base64.b64encode(fdsk.serial_number).decode("ascii")
        for element in list(root):
            if element.get("SerialNumber") == serial:
                root.remove(element)
        ET.SubElement(
            root,
            "DeviceCertificate",
            SerialNumber=serial,
            FDSK=state.cipher.seal(fdsk.label),
        )
        state.store.append(
            SetDeviceCertificates(
                project_id=project.id,
                certificates=ET.tostring(root, encoding="unicode"),
            )
        )
        return fdsk

    def installation_attributes(self, project_id: str) -> dict[str, str]:
        """The installation's attributes kept from the import (e.g. its IP routing settings)."""
        installation = self._state(project_id).session.query(Installation).first()
        return dict(installation.extra_attributes or {}) if installation else {}

    def set_ip_routing(
        self,
        project_id: str,
        *,
        latency: int | None = None,
        multicast_address: str | None = None,
    ) -> None:
        """Set the IP routing of the installation's backbone: the network ``latency`` tolerance
        (milliseconds, ``IPRoutingLatencyTolerance``) and the routing ``multicast_address``
        (``IPRoutingMulticastAddress``, a multicast IPv4 address). Each is an undo step; the
        default multicast address is stored as no address. Raises ``ValueError`` for a latency
        that is not positive or an address that is not a multicast one."""
        state = self._state(project_id)
        installation = state.session.query(Installation).first()
        if installation is None:
            raise KeyError("The project has no installation")
        attributes = installation.extra_attributes or {}
        changes: list[tuple[str, str | None]] = []
        if latency is not None:
            if not 0 < latency <= 0xFFFF:
                raise ValueError(f"Invalid network latency {latency}")
            changes.append(("IPRoutingLatencyTolerance", str(latency)))
        if multicast_address is not None:
            try:
                parsed = ipaddress.IPv4Address(multicast_address.strip())
            except ValueError as exc:
                raise ValueError(
                    f"Invalid multicast address {multicast_address!r}"
                ) from exc
            if not parsed.is_multicast:
                raise ValueError(f"Invalid multicast address {multicast_address!r}")
            value = str(parsed)
            changes.append(
                (
                    "IPRoutingMulticastAddress",
                    None if value == DEFAULT_MULTICAST_ADDRESS else value,
                )
            )
        for name, value in changes:
            if attributes.get(name) != value:
                state.store.append(
                    SetInstallationAttribute(
                        installation_id=installation.id, name=name, value=value
                    )
                )

    def set_segment_bus_access(
        self, project_id: str, segment_id: int, parameter: str | None
    ) -> None:
        """Set the bus connection of a segment (the main segment's is its line's): the
        ``Parameter`` of its ``<BusAccess>`` ("Type=IpTunneling;HostAddress=…"), None for
        "Automatic". One undo step; nothing when it does not change. Raises ``KeyError`` for an
        unknown segment."""
        state = self._state(project_id)
        segment = state.session.get(Segment, segment_id)
        if segment is None:
            raise KeyError(f"No segment with id {segment_id}")
        bus_access = None if not parameter else {"Name": "", "Parameter": parameter}
        if (segment.bus_access or None) == bus_access:
            return
        state.store.append(
            SetSegmentBusAccess(segment_id=segment_id, bus_access=bus_access)
        )

    def backbone_security(self, project_id: str) -> str | None:
        """The installation's KNX IP Secure routing setting (``@IPRoutingBackboneSecurity``:
        ``"On"``/``"Off"``, ``None`` = Auto, secured once it has a backbone key)."""
        return self.installation_attributes(project_id).get("IPRoutingBackboneSecurity")

    def set_backbone_security(self, project_id: str, mode: str | None) -> None:
        """Set the installation's KNX IP Secure routing (``"On"``/``"Off"``/``None`` = Auto). On
        gives it a backbone key when it has none (see :meth:`ensure_backbone_key`). Stored directly:
        devices may hold the key."""
        if mode not in (None, "Auto", "On", "Off"):
            raise ValueError(f"Unknown backbone security {mode!r}")
        state = self._state(project_id)
        installation = state.session.query(Installation).first()
        if installation is None:
            raise KeyError("The project has no installation")
        attributes = dict(installation.extra_attributes or {})
        if mode in (None, "Auto"):
            attributes.pop("IPRoutingBackboneSecurity", None)
        else:
            attributes["IPRoutingBackboneSecurity"] = mode
        installation.extra_attributes = attributes
        state.session.commit()
        if mode == "On":
            self.ensure_backbone_key(project_id)

    def ensure_backbone_key(self, project_id: str) -> bool:
        """Give the installation a random KNX IP Secure backbone key when it has none and its
        routing security is not switched off; stored encrypted (the project needs a password).
        ``True`` when one was made."""
        state = self._state(project_id)
        installation = state.session.query(Installation).first()
        if installation is None:
            return False
        attributes = dict(installation.extra_attributes or {})
        if attributes.get("IPRoutingBackboneKey"):
            return False
        if attributes.get("IPRoutingBackboneSecurity") == "Off":
            return False
        if state.cipher is None:
            raise ProjectPasswordRequired(
                "a backbone key is stored with the project password"
            )
        attributes["IPRoutingBackboneKey"] = state.cipher.seal(
            base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        )
        installation.extra_attributes = attributes
        state.session.commit()
        return True

    def ip_backbone_key(self, project_id: str) -> bytes | None:
        """The installation's KNX IP Secure routing backbone key (16 octets), decrypted, or
        ``None`` when the project has none."""
        state = self._state(project_id)
        installation = state.session.query(Installation).first()
        attrs = installation.extra_attributes if installation is not None else None
        value = (attrs or {}).get("IPRoutingBackboneKey")
        if value and state.cipher is not None:
            value = state.cipher.value(value)
        return _key(value)

    def ip_secrets(self, project_id: str, device_id: int) -> IpSecrets:
        """The device's KNX IP Secure passwords from its ``<Security>`` and ``<BusInterfaces>``."""
        state = self._state(project_id)
        device = state.session.get(Device, device_id)
        if device is None:
            return IpSecrets()

        def clear(value: str | None) -> str | None:
            if value and state.cipher is not None:
                value = state.cipher.value(value)
            return value or None

        attributes = security_attributes(device)
        bus_interfaces = [
            IpBusInterface(
                ref_id=element.get("RefId", ""),
                password=clear(element.get("Password")),
                secure=element.get("IsSecureEnabled", "true").lower()
                not in ("false", "0"),
            )
            for element in ip_secure.bus_interfaces(
                (device.extra_subtrees or {}).get("BusInterfaces")
            ).iter("BusInterface")
            if element.get("RefId")
        ]
        return IpSecrets(
            device_authentication_code=clear(
                attributes.get("DeviceAuthenticationCode")
            ),
            management_password=clear(attributes.get("DeviceManagementPassword")),
            bus_interfaces=bus_interfaces,
        )

    def secured_group_keys(self, project_id: str) -> dict[int, bytes]:
        """The key of every secured group address (raw address -> 16 octets); one without a key
        yet gets one, stored directly (see :meth:`group_security`)."""
        state = self._state(project_id)
        secured = secured_group_addresses(state.session)
        keys: dict[int, bytes] = {}
        created = False
        for ga in state.session.query(GroupAddress).filter(
            GroupAddress.id.in_(secured)
        ):
            if ga.security_key is None:
                ga.security_key = self._new_group_key(state)
                ga.data_secure = True
                created = True
            value = ga.security_key
            if state.cipher is not None:
                value = state.cipher.value(value)
            key = _key(value)
            if key is not None:
                keys[ga.address] = key
        if created:
            state.session.commit()
        return keys

    def device_sequence_number(self, project_id: str, device_id: int) -> int | None:
        """The device's next Sequence Number Sending as last read (``None`` when unknown)."""
        device = self._state(project_id).session.get(Device, device_id)
        if device is None:
            return None
        value = security_attributes(device).get("SequenceNumber")
        try:
            return int(value) if value else None
        except ValueError:
            return None

    def secure_senders(
        self, project_id: str, can_send: Callable[[int], bool] | None = None
    ) -> dict[int, int]:
        """Every device that can send on a secured group address (raw individual address) with
        its last valid Sequence Number (see :meth:`group_security`)."""
        session = self._state(project_id).session
        secured = secured_group_addresses(session)
        rows = (
            session.query(Device, ComObject.id)
            .join(ComObject, ComObject.device_id == Device.id)
            .join(ComObjectLink, ComObjectLink.com_object_id == ComObject.id)
            .filter(ComObjectLink.group_address_id.in_(secured))
            .all()
        )
        senders: dict[int, int] = {}
        for device, com_object_id in rows:
            if device.address is None or device.segment is None:
                continue
            if can_send is not None and not can_send(com_object_id):
                continue
            line = device.segment.line
            address = (line.area.address << 12) | (line.address << 8) | device.address
            senders[address] = _last_valid_sequence_number(device)
        return senders

    def group_address_senders(
        self,
        project_id: str,
        address: int,
        can_send: Callable[[int], bool] | None = None,
    ) -> list[int]:
        """The individual addresses (raw) of the devices that can send on a group address (see
        :meth:`group_security`), sorted."""
        session = self._state(project_id).session
        rows = (
            session.query(Device, ComObject.id)
            .join(ComObject, ComObject.device_id == Device.id)
            .join(ComObjectLink, ComObjectLink.com_object_id == ComObject.id)
            .join(GroupAddress, GroupAddress.id == ComObjectLink.group_address_id)
            .filter(GroupAddress.address == address)
            .all()
        )
        senders: set[int] = set()
        for device, com_object_id in rows:
            if device.address is None or device.segment is None:
                continue
            if can_send is not None and not can_send(com_object_id):
                continue
            line = device.segment.line
            senders.add(
                (line.area.address << 12) | (line.address << 8) | device.address
            )
        return sorted(senders)

    def bus_interface_group_addresses(
        self, project_id: str, device_id: int
    ) -> dict[str, list[int]]:
        """The group addresses (raw) assigned to each of the device's bus interfaces (its
        ``<Connector>`` elements), by the bus interface's ``@RefId``."""
        state = self._state(project_id)
        device = state.session.get(Device, device_id)
        if device is None:
            return {}
        by_source = {
            ga.source_id.rsplit("_", 1)[-1]: ga.address
            for ga in state.session.query(GroupAddress)
            if ga.source_id
        }
        result: dict[str, list[int]] = {}
        root = ip_secure.bus_interfaces(
            (device.extra_subtrees or {}).get("BusInterfaces")
        )
        for element in root.iter("BusInterface"):
            addresses = [
                by_source[ref.rsplit("_", 1)[-1]]
                for connector in element.iter("Connector")
                if (ref := connector.get("GroupAddressRefId"))
                and ref.rsplit("_", 1)[-1] in by_source
            ]
            if addresses:
                result[element.get("RefId", "")] = addresses
        return result

    def device_additional_addresses(self, project_id: str, device_id: int) -> list[int]:
        """The last octet of each additional individual address the device holds (its tunnelling
        addresses), in order."""
        device = self._state(project_id).session.get(Device, device_id)
        if device is None:
            return []
        return [a.address for a in device.additional_addresses if a.address is not None]

    def additional_addresses(
        self, project_id: str, device_id: int
    ) -> list[AdditionalAddressInfo]:
        """The device's additional individual addresses, every entry (with or without an
        address) in order."""
        state = self._state(project_id)
        return self._additional_infos(self._require_device(state, device_id))

    def assign_additional_addresses(
        self,
        project_id: str,
        device_id: int,
        count: int | None = None,
        *,
        label: str | None = None,
    ) -> list[AdditionalAddressInfo]:
        """Give each additional address of the device without one an address, after adding
        entries up to ``count``: the highest free device numbers of its line, counting down from
        255 and skipping the numbers its devices and their additional addresses use. One undo step
        (``label`` names it in the history); nothing is recorded when nothing changes. Raises
        :class:`AdditionalAddressError` when the line has too few free numbers."""
        state = self._state(project_id)
        device = self._require_device(state, device_id)
        entries = additional_address_entries(device)
        before = [dict(entry) for entry in entries]
        while len(entries) < (count or 0):
            entries.append(_empty_additional_entry())
        _fill_additional_entries(
            entries, self._line_numbers_in_use(state, _device_line(device).id)
        )
        if entries != before:
            state.store.append(
                SetDeviceAdditionalAddresses(
                    device_id=device_id, entries=entries, label=label
                )
            )
        return self._additional_infos(device)

    def set_additional_address(
        self,
        project_id: str,
        device_id: int,
        index: int,
        address: int | str | None,
        *,
        label: str | None = None,
    ) -> None:
        """Set the address of the device's additional address ``index`` (1-based; missing entries
        up to it are added), undoable. ``address`` is the device number (1-255) or the full
        ``a.l.d`` in the device's line; ``None`` or ``""`` clears it. Raises
        :class:`AdditionalAddressError` for a number outside 1-255, another line, or a number a
        device or another additional address of the line uses (the device's own included)."""
        state = self._state(project_id)
        device = self._require_device(state, device_id)
        line = _device_line(device)
        number = self._additional_number(line, address)
        entries = self._entries_up_to(device, index)
        entry = entries[index - 1]
        if entry["address"] == number and len(entries) == len(
            device.additional_addresses
        ):
            return
        if number is not None:
            used = self._line_numbers_in_use(state, line.id, without_device=device_id)
            used |= {
                other["address"]
                for position, other in enumerate(entries, start=1)
                if position != index and other["address"] is not None
            }
            if number in used:
                full = format_ia(line.area.address, line.address, number)
                raise AdditionalAddressError(
                    AdditionalAddressError.IN_USE,
                    f"The individual address {full} is already used in this line.",
                    full,
                )
        entry["address"] = number
        state.store.append(
            SetDeviceAdditionalAddresses(
                device_id=device_id, entries=entries, label=label
            )
        )

    def additional_address_text(
        self, project_id: str, device_id: int, address: int | str | None
    ) -> str | None:
        """The full ``a.l.d`` that ``address`` (a device number or a full address) names as an
        additional address of the device, ``None`` for no address. Raises
        :class:`AdditionalAddressError` like :meth:`set_additional_address` for a number outside
        1-255 or another line (whether the address is free is not checked)."""
        state = self._state(project_id)
        line = _device_line(self._require_device(state, device_id))
        number = self._additional_number(line, address)
        if number is None:
            return None
        return format_ia(line.area.address, line.address, number)

    def set_additional_address_name(
        self,
        project_id: str,
        device_id: int,
        index: int,
        name: str,
        *,
        label: str | None = None,
    ) -> None:
        """Set the name of the device's additional address ``index`` (1-based), undoable."""
        self._set_additional_text(project_id, device_id, index, "name", name, label)

    def set_additional_address_description(
        self,
        project_id: str,
        device_id: int,
        index: int,
        description: str,
        *,
        label: str | None = None,
    ) -> None:
        """Set the description of the device's additional address ``index`` (1-based),
        undoable."""
        self._set_additional_text(
            project_id, device_id, index, "description", description, label
        )

    def _set_additional_text(
        self,
        project_id: str,
        device_id: int,
        index: int,
        key: str,
        value: str,
        label: str | None,
    ) -> None:
        state = self._state(project_id)
        device = self._require_device(state, device_id)
        entries = self._entries_up_to(device, index)
        if entries[index - 1][key] == value and len(entries) == len(
            device.additional_addresses
        ):
            return
        entries[index - 1][key] = value
        state.store.append(
            SetDeviceAdditionalAddresses(
                device_id=device_id, entries=entries, label=label
            )
        )

    @staticmethod
    def _entries_up_to(device: Device, index: int) -> list[dict[str, Any]]:
        """The device's additional-address entries, with empty ones added up to ``index``."""
        if not 1 <= index <= 255:
            raise IndexError(f"No additional address {index}")
        entries = additional_address_entries(device)
        while len(entries) < index:
            entries.append(_empty_additional_entry())
        return entries

    @staticmethod
    def _additional_number(line: Line, address: int | str | None) -> int | None:
        """The device number ``address`` names in ``line``, ``None`` for no address."""
        number: int | None
        if isinstance(address, str):
            text = address.strip()
            if not text:
                return None
            own = f"{line.area.address}.{line.address}"
            try:
                if "." in text:
                    area_no, line_no, number = parse_ia(text)
                else:
                    area_no, line_no, number = (
                        line.area.address,
                        line.address,
                        int(text),
                    )
            except ValueError as exc:
                raise AdditionalAddressError(
                    AdditionalAddressError.INVALID,
                    f"{text!r} is not an individual address.",
                    text,
                ) from exc
            if (area_no, line_no) != (line.area.address, line.address):
                raise AdditionalAddressError(
                    AdditionalAddressError.OTHER_LINE,
                    f"An additional address must be in the device's line {own}.",
                    own,
                )
        else:
            number = address
        if number is None:
            return None
        if not 1 <= number <= 255:
            raise AdditionalAddressError(
                AdditionalAddressError.INVALID,
                "An additional address takes a device number from 1 to 255.",
                str(number),
            )
        return number

    def ensure_ip_secrets(
        self, project_id: str, device_id: int, bus_interface_refs: Sequence[str]
    ) -> bool:
        """Give a secure IP device the KNX IP Secure passwords it has none of yet: a device
        authentication code, a commissioning password and a password for each of
        ``bus_interface_refs`` (its application's tunnelling and routing bus interfaces, in the
        application's order), random, with their hashes. Stored directly, encrypted (so the
        project needs a password): once made they are kept, as the device may hold them.
        ``True`` when anything was added."""
        state = self._state(project_id)
        if state.cipher is None:
            raise ProjectPasswordRequired(
                "KNX IP Secure passwords are stored with the project password"
            )
        cipher = state.cipher
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        security = self._security_element(state, device_id)
        changed = False
        for name, derive in (
            ("DeviceAuthenticationCode", ip_secure.authentication_code_hash),
            ("DeviceManagementPassword", ip_secure.password_hash),
        ):
            if not security.get(name):
                value = ip_secure.new_password()
                security.set(name, cipher.seal_attribute(value))
                security.set(
                    f"{name}Hash", cipher.seal(ip_secure.encoded(derive(value)))
                )
                changed = True
        root = ip_secure.bus_interfaces(
            (device.extra_subtrees or {}).get("BusInterfaces")
        )
        ip_secure.add_bus_interfaces(root, bus_interface_refs)
        for ref in bus_interface_refs:
            element = ip_secure.bus_interface(root, ref)
            if element is not None and not element.get("Password"):
                value = ip_secure.new_password()
                element.set("Password", cipher.seal_attribute(value))
                element.set(
                    "PasswordHash",
                    cipher.seal(ip_secure.encoded(ip_secure.password_hash(value))),
                )
                changed = True
        if changed:
            subtrees = dict(device.extra_subtrees or {})
            subtrees["BusInterfaces"] = ET.tostring(root, encoding="unicode")
            device.extra_subtrees = subtrees
            self._store_security(state, device_id, security)
        return changed

    def set_ip_secrets(
        self,
        project_id: str,
        device_id: int,
        *,
        authentication_code: str | None = None,
        management_password: str | None = None,
        bus_interface_passwords: Mapping[str, str] | None = None,
        secure_tunnelling: bool | None = None,
    ) -> None:
        """Change a secure IP device's KNX IP Secure settings (``None`` keeps one): its device
        authentication code, its commissioning password, the passwords of bus interfaces (by
        ``@RefId``) and whether its tunnels are secured. Stored directly like the generated ones;
        a download writes them. ``ValueError`` for a password the device cannot take."""
        state = self._state(project_id)
        if state.cipher is None:
            raise ProjectPasswordRequired(
                "KNX IP Secure passwords are stored with the project password"
            )
        cipher = state.cipher
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        for value in (
            authentication_code,
            management_password,
            *(bus_interface_passwords or {}).values(),
        ):
            if value is not None:
                ip_secure.check_password(value)
        security = self._security_element(state, device_id)
        for name, value, derive in (
            (
                "DeviceAuthenticationCode",
                authentication_code,
                ip_secure.authentication_code_hash,
            ),
            ("DeviceManagementPassword", management_password, ip_secure.password_hash),
        ):
            if value is not None:
                security.set(name, cipher.seal_attribute(value))
                security.set(
                    f"{name}Hash", cipher.seal(ip_secure.encoded(derive(value)))
                )
        root = ip_secure.bus_interfaces(
            (device.extra_subtrees or {}).get("BusInterfaces")
        )
        for ref, value in (bus_interface_passwords or {}).items():
            ip_secure.add_bus_interfaces(root, [ref])
            element = ip_secure.bus_interface(root, ref)
            assert element is not None
            element.set("Password", cipher.seal_attribute(value))
            element.set(
                "PasswordHash",
                cipher.seal(ip_secure.encoded(ip_secure.password_hash(value))),
            )
        if secure_tunnelling is not None:
            for element in root.iter("BusInterface"):
                if secure_tunnelling:
                    element.attrib.pop("IsSecureEnabled", None)
                else:
                    element.set("IsSecureEnabled", "false")
        subtrees = dict(device.extra_subtrees or {})
        if len(root):
            subtrees["BusInterfaces"] = ET.tostring(root, encoding="unicode")
        device.extra_subtrees = subtrees
        self._store_security(state, device_id, security)

    def set_ip_secure_loaded(
        self,
        project_id: str,
        device_id: int,
        *,
        backbone_key: bytes | None = None,
        authentication_code_hash: bytes | None = None,
        management_password_hash: bytes | None = None,
    ) -> None:
        """Record the KNX IP Secure settings a secure download wrote: the backbone key
        (``@LoadedIPRoutingBackboneKey``) and the hashes of the device authentication code and the
        management password, all encrypted (a hash is what a client authenticates with). Stored
        directly, like the Tool Keys."""
        state = self._state(project_id)
        if state.cipher is None:
            raise ProjectPasswordRequired(
                "KNX IP Secure keys are stored with the project password"
            )
        security = self._security_element(state, device_id)
        for name, value in (
            ("LoadedIPRoutingBackboneKey", backbone_key),
            ("LoadedDeviceAuthenticationCodeHash", authentication_code_hash),
            ("LoadedDeviceManagementPasswordHash", management_password_hash),
        ):
            if value is not None:
                security.set(
                    name, state.cipher.seal(base64.b64encode(value).decode("ascii"))
                )
        self._store_security(state, device_id, security)

    def secure_commissioning(self, project_id: str, device_id: int) -> bool:
        """Whether the device is commissioned securely (the project holds a Tool Key for it)."""
        device = self._state(project_id).session.get(Device, device_id)
        return device is not None and secure_commissioned(device)

    def secure_commissioned_devices(self, project_id: str) -> set[int]:
        """The ids of the devices commissioned securely (KNX Data Secure)."""
        return commissioned_devices(self._state(project_id).session)

    def secure_commissioning_impact(
        self,
        project_id: str,
        device_id: int,
        requires_security: Callable[[int], bool] | None = None,
    ) -> SecureCommissioningImpact:
        """What :meth:`set_secure_commissioning` with ``False`` would change for the device. A
        link is removed when it may not communicate in the clear: to a group address with Security
        ``On``, of a group object that requires security, or to a group address one of whose group
        objects requires it (``requires_security(com object id)``, from the application)."""
        session = self._state(project_id).session
        secured = secured_group_addresses(session)
        rows = (
            session.query(ComObjectLink.id, ComObjectLink.com_object_id, GroupAddress)
            .join(GroupAddress, GroupAddress.id == ComObjectLink.group_address_id)
            .join(ComObject, ComObject.id == ComObjectLink.com_object_id)
            .filter(ComObject.device_id == device_id)
            .order_by(GroupAddress.address, ComObjectLink.id)
            .all()
        )

        def must_be_secured(com_object_id: int, ga: GroupAddress) -> bool:
            mode = effective_mode(ga)
            if mode == "On":
                return True
            if requires_security is None or mode == "Off":
                return False
            return requires_security(com_object_id) or any(
                requires_security(link.com_object_id) for link in ga.links
            )

        unsecured: list[int] = []
        removed: list[int] = []
        removed_from: list[int] = []
        for link_id, com_object_id, ga in rows:
            if must_be_secured(com_object_id, ga):
                removed.append(link_id)
                if ga.id not in removed_from:
                    removed_from.append(ga.id)
            elif ga.id in secured and ga.id not in unsecured:
                unsecured.append(ga.id)
        return SecureCommissioningImpact(
            unsecured=unsecured, removed_links=removed, removed_from=removed_from
        )

    def set_secure_commissioning(
        self,
        project_id: str,
        device_id: int,
        activated: bool,
        requires_security: Callable[[int], bool] | None = None,
    ) -> None:
        """Activate or deactivate the device's secure commissioning, as one undo step.

        Activating gives the device a new random Tool Key, stored encrypted, so the project needs
        a password (:class:`ProjectPasswordRequired` otherwise). Deactivating removes it; the
        device's links that may not communicate in the clear are removed with it (see
        :meth:`secure_commissioning_impact`), and its other ``Auto`` group addresses communicate in
        the clear. The key the device holds is kept: the next download switches the device to plain
        communication with it."""
        state = self._state(project_id)
        if self.secure_commissioning(project_id, device_id) == activated:
            return
        if activated:
            if state.cipher is None:
                raise ProjectPasswordRequired(
                    "a Tool Key is stored with the project password"
                )
            key = state.cipher.seal(
                base64.b64encode(secrets.token_bytes(16)).decode("ascii")
            )
            state.store.append(
                SetDeviceSecureCommissioning(device_id=device_id, tool_key=key)
            )
            return
        impact = self.secure_commissioning_impact(
            project_id, device_id, requires_security
        )
        events: list[Event] = [
            UnlinkComObject(target_id=link_id) for link_id in impact.removed_links
        ]
        events.append(SetDeviceSecureCommissioning(device_id=device_id, tool_key=None))
        state.store.append(
            events[0] if len(events) == 1 else CompositeEvent(events=events)
        )

    def set_tool_key_loaded(self, project_id: str, device_id: int) -> None:
        """Record that the device uses its Tool Key now (``LoadedToolKey`` = ``ToolKey``)."""
        state = self._state(project_id)
        security = self._security_element(state, device_id)
        tool_key = security.get("ToolKey")
        if not tool_key or security.get("LoadedToolKey") == tool_key:
            return
        security.set("LoadedToolKey", tool_key)
        self._store_security(state, device_id, security)

    def unicast_broadcast_blocking(self, project_id: str, device_id: int) -> str | None:
        """A coupler's blocking of point-to-point and broadcast messages
        (``<Security @UnicastBroadcastBlocking>``: ``"On"``/``"Off"``, ``None`` = Auto)."""
        device = self._state(project_id).session.get(Device, device_id)
        if device is None:
            return None
        value = security_attributes(device).get("UnicastBroadcastBlocking")
        return None if value in (None, "Auto") else value

    def set_unicast_broadcast_blocking(
        self, project_id: str, device_id: int, mode: str | None
    ) -> None:
        """Set a coupler's blocking of point-to-point and broadcast messages (``"On"``/``"Off"``,
        ``None`` = Auto); the next download writes it."""
        if mode not in (None, "Auto", "On", "Off"):
            raise ValueError(f"Unknown blocking {mode!r}")
        state = self._state(project_id)
        security = self._security_element(state, device_id)
        if mode in (None, "Auto"):
            security.attrib.pop("UnicastBroadcastBlocking", None)
        else:
            security.set("UnicastBroadcastBlocking", mode)
        self._store_security(state, device_id, security)

    def set_tool_key_unloaded(self, project_id: str, device_id: int) -> None:
        """Record that the device holds no Tool Key any more (a factory reset): its
        ``LoadedToolKey`` goes; its Tool Key stays for the next secure commissioning."""
        state = self._state(project_id)
        security = self._security_element(state, device_id)
        if "LoadedToolKey" not in security.attrib:
            return
        del security.attrib["LoadedToolKey"]
        self._store_security(state, device_id, security)

    @staticmethod
    def _store_security(state: _Open, device_id: int, security: ET.Element) -> None:
        """Store the device's ``<Security>`` directly, not as an undo step: what a download left in
        the device (the Tool Key it uses, its Sequence Number) must survive an undo, or the project
        would be locked out of the device."""
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        subtrees = {
            k: v for k, v in (device.extra_subtrees or {}).items() if k != "Security"
        }
        subtrees["Security"] = ET.tostring(security, encoding="unicode")
        device.extra_subtrees = subtrees
        state.session.commit()

    @staticmethod
    def _security_element(state: _Open, device_id: int) -> ET.Element:
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        raw = (device.extra_subtrees or {}).get("Security")
        return ET.fromstring(raw) if raw else ET.Element("Security")

    def group_security(
        self,
        project_id: str,
        device_id: int,
        can_send: Callable[[int], bool] | None = None,
        tunnel_senders: Mapping[int, Sequence[int]] | None = None,
    ) -> tuple[dict[int, bytes], list[tuple[int, int]]]:
        """The keys of the secured group addresses the device is linked to (raw address -> key)
        and the devices that may send on them: ``(individual address (raw), last valid sequence
        number)`` of every other addressed device with a group object on one of them that can send
        (``can_send(com object id)``: its flags let it transmit, answer read requests or request
        a read; every linked object without it), and the tunnels the group address is assigned to
        (``tunnel_senders``: raw group address -> raw tunnelling addresses, sequence number 0). A
        secured group address without a key yet gets one, stored directly: devices may hold it
        from now on, so it is not an undo step."""
        state = self._state(project_id)
        session = state.session
        linked = (
            session.query(GroupAddress)
            .join(ComObjectLink, ComObjectLink.group_address_id == GroupAddress.id)
            .join(ComObject, ComObject.id == ComObjectLink.com_object_id)
            .filter(ComObject.device_id == device_id)
            .distinct()
            .all()
        )
        secured = secured_group_addresses(session)
        keys: dict[int, bytes] = {}
        created = False
        for ga in linked:
            if ga.id not in secured:
                continue
            if ga.security_key is None:
                ga.security_key = self._new_group_key(state)
                ga.data_secure = True
                created = True
            value = ga.security_key
            if state.cipher is not None:
                value = state.cipher.value(value)
            key = _key(value)
            if key is not None:
                keys[ga.address] = key
        if created:
            session.commit()
        senders: dict[int, int] = {}
        if keys:
            rows = (
                session.query(Device, ComObject.id)
                .join(ComObject, ComObject.device_id == Device.id)
                .join(ComObjectLink, ComObjectLink.com_object_id == ComObject.id)
                .join(GroupAddress, GroupAddress.id == ComObjectLink.group_address_id)
                .filter(GroupAddress.address.in_(keys), Device.id != device_id)
                .all()
            )
            for other, com_object_id in rows:
                if other.address is None or other.segment is None:
                    continue
                if can_send is not None and not can_send(com_object_id):
                    continue
                line = other.segment.line
                address = (
                    (line.area.address << 12) | (line.address << 8) | other.address
                )
                senders[address] = _last_valid_sequence_number(other)
            for group_address in keys:
                for tunnel in (tunnel_senders or {}).get(group_address, ()):
                    senders.setdefault(tunnel, 0)
        return keys, sorted(senders.items())

    def security_table_sizes(
        self,
        project_id: str,
        device_ids: Sequence[int],
        can_send: Callable[[int], bool] | None = None,
        with_link: tuple[int, int] | None = None,
    ) -> dict[int, tuple[int, int]]:
        """How many group keys and senders the devices' security tables hold (see
        :meth:`group_security`), with the link ``(com object id, group address id)`` counted in
        as well (it is not stored)."""
        session = self._state(project_id).session
        nested = session.begin_nested() if with_link is not None else None
        try:
            if with_link is not None:
                session.add(
                    ComObjectLink(
                        com_object_id=with_link[0], group_address_id=with_link[1]
                    )
                )
                session.flush()
            secured = secured_group_addresses(session)
            sizes: dict[int, tuple[int, int]] = {}
            for device_id in device_ids:
                group_addresses = {
                    ga_id
                    for (ga_id,) in session.query(ComObjectLink.group_address_id)
                    .join(ComObject, ComObject.id == ComObjectLink.com_object_id)
                    .filter(ComObject.device_id == device_id)
                    if ga_id in secured
                }
                senders: set[int] = set()
                if group_addresses:
                    rows = (
                        session.query(Device.id, ComObject.id)
                        .join(ComObject, ComObject.device_id == Device.id)
                        .join(
                            ComObjectLink, ComObjectLink.com_object_id == ComObject.id
                        )
                        .filter(
                            ComObjectLink.group_address_id.in_(group_addresses),
                            Device.id != device_id,
                        )
                    )
                    senders = {
                        other
                        for other, com_object_id in rows
                        if can_send is None or can_send(com_object_id)
                    }
                sizes[device_id] = (len(group_addresses), len(senders))
            return sizes
        finally:
            if nested is not None:
                nested.rollback()

    def set_device_sequence_number(
        self, project_id: str, device_id: int, sequence_number: int
    ) -> None:
        """Record the device's next Sequence Number Sending, read at the end of a secured
        download (``<Security @SequenceNumber>`` with its timestamp). Stored directly, like the
        Tool Keys: it describes the device, not an edit."""
        state = self._state(project_id)
        security = self._security_element(state, device_id)
        security.set("SequenceNumber", str(sequence_number))
        security.set("SequenceNumberTimestamp", _sequence_timestamp())
        self._store_security(state, device_id, security)

    def p2p_security(
        self,
        project_id: str,
        device_id: int,
        role_masks: Mapping[str, int] | None = None,
    ) -> list[tuple[int, bytes, int]]:
        """The device's secure point-to-point / broadcast partners as ``(partner individual
        address (raw), 16-octet key, roles)``, read from the installation's ``<P2PLinks>``.
        ``roles`` are the roles the partner has on this device (3/5/1 6.3.6.1): the masks
        (``role_masks``, this device's application ``SecurityRole @Id`` -> ``@Mask``) of the roles
        the partner's endpoint lists, ORed; roles of other applications are not this device's.
        Endpoints resolve by the device's source ``@Id``; a link whose partner cannot be resolved
        is skipped."""
        state = self._state(project_id)
        device = state.session.get(Device, device_id)
        if device is None or not device.source_id:
            return []
        owner_of = {
            other.source_id: other
            for other in state.session.query(Device)
            if other.source_id
        }
        masks = role_masks or {}
        result: list[tuple[int, bytes, int]] = []
        for installation in state.session.query(Installation):
            for link_key, endpoints in _p2p_links(installation.p2p_links_xml):
                resolved = [(owner_of.get(ref), roles) for ref, roles in endpoints]
                if not any(d is device for d, _ in resolved):
                    continue
                no_partner: tuple[Device | None, list[str]] = (None, [])
                partner, partner_roles = next(
                    (
                        (d, roles)
                        for d, roles in resolved
                        if d is not None and d.id != device_id
                    ),
                    no_partner,
                )
                if (
                    partner is None
                    or partner.address is None
                    or partner.segment is None
                ):
                    continue
                value = link_key
                if value and state.cipher is not None:
                    value = state.cipher.value(value)
                key = _key(value)
                if key is None:
                    continue
                line = partner.segment.line
                address = (
                    (line.area.address << 12) | (line.address << 8) | partner.address
                )
                roles = 0
                for role in partner_roles:
                    roles |= masks.get(role, 0)
                result.append((address, key, roles & 0xFFFF))
        return result

    def device_keys(self, project_id: str, device_id: int) -> DeviceKeys | None:
        """The device's tool keys from its ``<Security>`` and its FDSK from the device certificate
        of its serial number, or ``None`` when it has none of them."""
        state = self._state(project_id)
        device = state.session.get(Device, device_id)
        if device is None:
            return None
        certificate = self.device_certificates(project_id).get(device.serial_number)
        fdsk = certificate.key if certificate is not None else None
        raw = (device.extra_subtrees or {}).get("Security")
        try:
            security = ET.fromstring(raw) if raw else ET.Element("Security")
        except ET.ParseError:
            security = ET.Element("Security")

        def key(name: str) -> bytes | None:
            value = security.get(name)
            if value and state.cipher is not None:
                value = state.cipher.value(value)
            return _key(value)

        keys = DeviceKeys(
            tool_key=key("ToolKey"), loaded_tool_key=key("LoadedToolKey"), fdsk=fdsk
        )
        return keys if keys.tool_key or keys.loaded_tool_key or keys.fdsk else None

    def rename_line(self, project_id: str, line_id: int, name: str) -> None:
        self._state(project_id).store.append(RenameLine(line_id=line_id, name=name))

    def rename_group_address(
        self, project_id: str, group_address_id: int, name: str
    ) -> None:
        self._state(project_id).store.append(
            RenameGroupAddress(group_address_id=group_address_id, name=name)
        )

    def set_group_address_address(
        self, project_id: str, group_address_id: int, new_address: int
    ) -> None:
        """Renumber a group address to ``new_address``, moving it (and its com-object links) under
        the range chain for the new address. Mirrors ETS's "change address": links are preserved
        because they reference the group address by id. Raises ``ValueError`` if the group address
        is unknown, ``new_address`` is outside the valid 1..65535 range, or another group address in
        the same installation already uses it.
        """
        state = self._state(project_id)
        ga = state.session.get(GroupAddress, group_address_id)
        if ga is None:
            raise ValueError(f"unknown group address {group_address_id}")
        if new_address == ga.address:
            return  # no-op: changing nothing must not append an event (would drop the redo branch)
        if not (1 <= new_address <= 0xFFFF):
            raise ValueError(
                f"address {new_address} is outside the valid range 1..65535"
            )
        installation_id = ga.group_range.installation_id
        clash = (
            state.session.query(GroupAddress.id)
            .join(GroupRange, GroupAddress.group_range_id == GroupRange.id)
            .filter(
                GroupRange.installation_id == installation_id,
                GroupAddress.address == new_address,
                GroupAddress.id != group_address_id,
            )
            .first()
        )
        if clash is not None:
            raise ValueError(f"address {new_address} is already in use")
        # Free style has no address-derived folder hierarchy: keep the group address under its
        # current range (empty levels) rather than forcing it into the catch-all range, which the
        # range-start match cannot place correctly against imported non-canonical Free ranges.
        style = self._style(state)
        levels = (
            [] if style is GroupAddressStyle.FREE else ranges_for(new_address, style)
        )
        state.store.append(
            SetGroupAddressAddress(
                group_address_id=group_address_id,
                installation_id=installation_id,
                address=new_address,
                levels=[[start, end, name] for start, end, name in levels],
            )
        )

    def set_device_name(self, project_id: str, device_id: int, name: str) -> None:
        self._state(project_id).store.append(
            SetDeviceName(device_id=device_id, name=name)
        )

    def set_device_description(
        self, project_id: str, device_id: int, description: str
    ) -> None:
        self._state(project_id).store.append(
            SetDeviceDescription(device_id=device_id, description=description)
        )

    def set_topology_description(
        self, project_id: str, kind: str, element_id: int, description: str
    ) -> None:
        """The description of an area, a line or a segment (``kind`` "area", "line" or
        "segment"); undoable."""
        self._state(project_id).store.append(
            SetTopologyDescription(
                kind=kind, element_id=element_id, description=description
            )
        )

    def set_topology_address(
        self,
        project_id: str,
        kind: str,
        element_id: int,
        address: int,
        *,
        couplers: Collection[int] = (),
    ) -> None:
        """The address of an area or a line (``kind`` "area" or "line"), 0 to 15 and not that of
        another one beside it; its devices' individual addresses change with it and are no
        longer the downloaded ones (see :func:`_address_change_events`). One undo step.

        Raises ``ValueError`` for an address out of range or in use, ``KeyError`` for an unknown
        element."""
        state = self._state(project_id)
        if not 0 <= address <= 15:
            raise ValueError(f"address {address} is not between 0 and 15")
        if kind == "area":
            area = state.session.get(Area, element_id)
            if area is None:
                raise KeyError(f"No area {element_id}")
            siblings = [
                a.address
                for a in state.session.query(Area).filter(
                    Area.installation_id == area.installation_id, Area.id != area.id
                )
            ]
            lines = list(area.lines)
        elif kind == "line":
            line = state.session.get(Line, element_id)
            if line is None:
                raise KeyError(f"No line {element_id}")
            siblings = [
                ln.address
                for ln in state.session.query(Line).filter(
                    Line.area_id == line.area_id, Line.id != line.id
                )
            ]
            lines = [line]
        else:
            raise KeyError(f"No {kind} {element_id}")
        if address in siblings:
            raise ValueError(f"address {address} is in use")
        events: list[Event] = [
            SetTopologyAddress(kind=kind, element_id=element_id, address=address),
            *_address_change_events(
                (d for ln in lines for s in ln.segments for d in s.devices), couplers
            ),
        ]
        state.store.append(events[0] if len(events) == 1 else CompositeEvent(events))

    def set_group_description(
        self, project_id: str, kind: str, element_id: int, description: str
    ) -> None:
        """The description of a group range or a group address (``kind`` "group_range" or
        "group_address"); undoable."""
        self._state(project_id).store.append(
            SetGroupDescription(
                kind=kind, element_id=element_id, description=description
            )
        )

    def set_group_range_number(
        self, project_id: str, range_id: int, number: int
    ) -> None:
        """Give a main group (0 to 31) or a middle group (0 to 7) another number; its group
        addresses move with it. Undoable.

        Raises ``ValueError`` for a number out of range, one in use by another group beside it, or
        a project in the Free style (its groups have no number); ``KeyError`` for an unknown
        range."""
        state = self._state(project_id)
        group_range = state.session.get(GroupRange, range_id)
        if group_range is None:
            raise KeyError(f"No group range {range_id}")
        style = self._style(state)
        if style is GroupAddressStyle.FREE:
            raise ValueError("groups of the Free style have no number")
        parent = group_range.parent
        if parent is None:
            if not 0 <= number <= 31:
                raise ValueError(f"main group {number} is not between 0 and 31")
            start = number << 11
        else:
            if not 0 <= number <= 7:
                raise ValueError(f"middle group {number} is not between 0 and 7")
            start = (parent.range_start & ~0x7FF) | (number << 8)
        offset = start - group_range.range_start
        if offset == 0:
            return
        end = group_range.range_end + offset
        siblings = state.session.query(GroupRange).filter(
            GroupRange.installation_id == group_range.installation_id,
            GroupRange.parent_id == group_range.parent_id,
            GroupRange.id != group_range.id,
        )
        if any(s.range_start <= end and start <= s.range_end for s in siblings):
            raise ValueError(f"group {number} is in use")
        state.store.append(SetGroupRangeAddress(range_id=range_id, offset=offset))

    def move_group_range(
        self, project_id: str, range_id: int, main_group_id: int | None
    ) -> None:
        """Put a middle group below another main group (three-level style), with its group
        addresses: it keeps its number when that is free there, else it gets the lowest free
        one. In the Free style a range goes into any other range, or to the top with
        ``main_group_id`` None (see :meth:`_move_free_range`). Undoable.

        Raises ``ValueError`` when it is no middle group, the target no main group, or no middle
        group is free there; ``KeyError`` for an unknown range."""
        state = self._state(project_id)
        if self._style(state) is GroupAddressStyle.FREE:
            self._move_free_range(state, range_id, main_group_id)
            return
        group_range = state.session.get(GroupRange, range_id)
        target = (
            state.session.get(GroupRange, main_group_id)
            if main_group_id is not None
            else None
        )
        if group_range is None or target is None:
            raise KeyError(f"No group range {range_id} or {main_group_id}")
        if self._style(state) is not GroupAddressStyle.THREE_LEVEL:
            raise ValueError("only the three-level style has middle groups")
        if group_range.parent is None or target.parent is not None:
            raise ValueError("only a middle group goes below a main group")
        if group_range.parent_id == target.id:
            return
        used = {(child.range_start >> 8) & 7 for child in target.children}
        own = (group_range.range_start >> 8) & 7
        free = [own] if own not in used else [n for n in range(8) if n not in used]
        if not free:
            raise ValueError("the main group has no free middle group")
        start = (target.range_start & ~0x7FF) | (free[0] << 8)
        state.store.append(
            MoveGroupRange(
                range_id=range_id,
                new_parent_id=target.id,
                offset=start - group_range.range_start,
            )
        )

    def _move_free_range(
        self, state: _Open, range_id: int, parent_id: int | None
    ) -> None:
        """Put a Free style range into the range ``parent_id`` (None: to the top), with the
        ranges and group addresses in it: it keeps its addresses when they fit there (within
        the new parent, beside its ranges), else it goes to the next place it fits (the lowest
        one when there is none after), its group addresses moving with it.

        Raises ``ValueError`` when it would go into itself or a range in it, or fits nowhere
        there, or a group address it moves to is taken; ``KeyError`` for an unknown range."""
        group_range = state.session.get(GroupRange, range_id)
        parent = (
            state.session.get(GroupRange, parent_id) if parent_id is not None else None
        )
        if group_range is None or (parent_id is not None and parent is None):
            raise KeyError(f"No group range {range_id} or {parent_id}")
        if group_range.parent_id == parent_id:
            return
        node = parent
        while node is not None:
            if node.id == range_id:
                raise ValueError("a group range cannot go into itself")
            node = node.parent
        low, high = (
            (parent.range_start, parent.range_end)
            if parent is not None
            else (1, 0xFFFF)
        )
        siblings = state.session.query(
            GroupRange.range_start, GroupRange.range_end
        ).filter(
            GroupRange.installation_id == group_range.installation_id,
            GroupRange.parent_id.is_(None)
            if parent_id is None
            else GroupRange.parent_id == parent_id,
        )
        size = group_range.range_end - group_range.range_start + 1
        taken = list(siblings)
        start = place_range(
            low,
            high,
            taken,
            size,
            Allocation.START_WITH,
            group_range.range_start,
        )
        if start is None:
            raise ValueError("the group range does not fit there")
        offset = start - group_range.range_start
        if offset:
            moving: dict[int, int] = {}
            nodes = [group_range]
            while nodes:
                each = nodes.pop()
                moving.update((ga.id, ga.address) for ga in each.group_addresses)
                nodes.extend(each.children)
            used = {
                address
                for (ga_id, address) in state.session.query(
                    GroupAddress.id, GroupAddress.address
                )
                .join(GroupRange, GroupAddress.group_range_id == GroupRange.id)
                .filter(GroupRange.installation_id == group_range.installation_id)
                if ga_id not in moving
            }
            if any(address + offset in used for address in moving.values()):
                raise ValueError("a group address it moves to is taken")
        state.store.append(
            MoveGroupRange(range_id=range_id, new_parent_id=parent_id, offset=offset)
        )

    def move_group_address(
        self, project_id: str, group_address_id: int, range_id: int
    ) -> None:
        """Put a group address into another group (a middle group, or a main group in the
        two-level style): it keeps its number within the group when that is free there, else it
        gets the lowest free one; its links move with it. In the Free style it goes into any
        range and keeps its address when the range spans it. Undoable.

        Raises ``ValueError`` when the group holds no group addresses or has none free;
        ``KeyError`` for an unknown group address or range."""
        state = self._state(project_id)
        ga = state.session.get(GroupAddress, group_address_id)
        target = state.session.get(GroupRange, range_id)
        if ga is None or target is None:
            raise KeyError(f"No group address {group_address_id} or range {range_id}")
        style = self._style(state)
        if style is GroupAddressStyle.FREE:
            self._move_free_group_address(state, ga, target)
            return
        holds = (
            style is GroupAddressStyle.THREE_LEVEL and target.parent is not None
        ) or (style is GroupAddressStyle.TWO_LEVEL and target.parent is None)
        if not holds:
            raise ValueError("this group holds no group addresses")
        if ga.group_range_id == target.id:
            return
        used = {
            address
            for (address,) in state.session.query(GroupAddress.address)
            .join(GroupRange, GroupAddress.group_range_id == GroupRange.id)
            .filter(GroupRange.installation_id == target.installation_id)
        }
        own = target.range_start + (ga.address - ga.group_range.range_start)
        candidates = [own] if target.range_start <= own <= target.range_end else []
        candidates += range(max(target.range_start, 1), target.range_end + 1)
        address = next((a for a in candidates if a not in used), None)
        if address is None:
            raise ValueError("the group has no free group address")
        self.set_group_address_address(project_id, group_address_id, address)

    def _move_free_group_address(
        self, state: _Open, ga: GroupAddress, target: GroupRange
    ) -> None:
        """A group address into the Free style range ``target``: at its address when the range
        spans it, else at the range's lowest free one."""
        if ga.group_range_id == target.id:
            return
        used = {
            address
            for (address,) in state.session.query(GroupAddress.address)
            .join(GroupRange, GroupAddress.group_range_id == GroupRange.id)
            .filter(
                GroupRange.installation_id == target.installation_id,
                GroupAddress.id != ga.id,
            )
        }
        candidates = (
            [ga.address] if target.range_start <= ga.address <= target.range_end else []
        )
        candidates += range(max(target.range_start, 1), target.range_end + 1)
        address = next((a for a in candidates if a not in used), None)
        if address is None:
            raise ValueError("the group has no free group address")
        state.store.append(
            SetGroupAddressAddress(
                group_address_id=ga.id,
                installation_id=target.installation_id,
                address=address,
                group_range_id=target.id,
            )
        )

    def comment(
        self, project_id: str, kind: str, element_id: int, field: str = "comment"
    ) -> str | None:
        """An element's comment (RTF as stored), or a device's installation hints for ``field``
        "installation_hints"; ``kind`` is a ``COMMENT_KINDS`` name. None without such an element."""
        return stored_comment(self._state(project_id).session, kind, element_id, field)

    def set_comment(
        self,
        project_id: str,
        kind: str,
        element_id: int,
        comment: str,
        field: str = "comment",
    ) -> None:
        """Set an element's comment, or a device's installation hints (both RTF); undoable."""
        self._state(project_id).store.append(
            SetComment(kind=kind, element_id=element_id, comment=comment, field=field)
        )

    def completion_status(
        self, project_id: str, kind: str, element_id: int
    ) -> str | None:
        """An element's "Status" (``CompletionStatus``); ``kind`` is a ``STATUS_KINDS`` name. None
        without such an element."""
        return stored_completion_status(
            self._state(project_id).session, kind, element_id
        )

    def set_completion_status(
        self, project_id: str, kind: str, element_id: int, status: str
    ) -> None:
        """Set an element's "Status" to one of ``COMPLETION_STATUSES``; undoable."""
        if status not in COMPLETION_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        if kind not in STATUS_KINDS:
            raise ValueError(f"no status for {kind!r}")
        self._state(project_id).store.append(
            SetCompletionStatus(kind=kind, element_id=element_id, status=status)
        )

    def sync_device_com_objects(
        self,
        project_id: str,
        device_id: int,
        target: list[tuple[str, str | None]],
        app_program_id: str = "",
    ) -> None:
        """Reconcile a device's com-object rows to ``target`` (the parameter-driven should-exist set
        of ``(ref_id, channel_id)``): add missing refs, remove obsolete ones (with links), keep
        survivors. Undoable. The caller computes ``target`` (the project package is ref-only).
        ``app_program_id`` resolves stored rows to the qualified per-instance ref the target speaks."""
        self._state(project_id).store.append(
            SyncDeviceComObjects(
                device_id=device_id,
                target=[[ref_id, channel_id] for ref_id, channel_id in target],
                app_program_id=app_program_id,
            )
        )

    def upsert_device_binary_data(
        self,
        project_id: str,
        device_id: int,
        name: str,
        data: bytes,
        *,
        ref_id: str = "",
        do_not_copy: bool = False,
        label: str | None = None,
    ) -> None:
        """Upsert a device's ``BinaryData`` row (keyed by ``name``) as one undo step.

        Used by DALI DCA write-backs that persist a channel's state blob into the project so
        a later .knxproj export carries what the user programmed. Replaces an existing row
        with the same ``name`` (preserving its id so an undo restores the previous content);
        creates a new row when none exists yet.
        """
        event = UpsertDeviceBinaryData(
            device_id=device_id,
            name=name,
            data=data,
            ref_id=ref_id,
            do_not_copy=do_not_copy,
        )
        store = self._state(project_id).store
        if label is None:
            store.append(event)
        else:
            store.append(CompositeEvent(events=[event], label=label))

    def set_parameters(
        self,
        project_id: str,
        device_id: int,
        changes: Sequence[tuple[str, str]],
        com_object_target: list[tuple[str, str | None]] | None = None,
        app_program_id: str = "",
        label: str | None = None,
        module_instances: Sequence[tuple[str, str]] | None = None,
        removed_module_instances: Sequence[str] | None = None,
    ) -> None:
        """Persist several parameter values, optionally with a com-object reconcile and the module
        instances the change created (``(instance_id, ref_id)``) and ended (saved instance ids), as
        one undo step."""
        events: list[Event] = [
            SetParameter(device_id=device_id, ref_id=ref_id, value=value)
            for ref_id, value in changes
        ]
        if module_instances or removed_module_instances:
            events.append(
                SyncDeviceModuleInstances(
                    device_id=device_id,
                    instances=[[i, r] for i, r in module_instances or ()],
                    remove=list(removed_module_instances or ()),
                )
            )
        if com_object_target is not None:
            events.append(
                SyncDeviceComObjects(
                    device_id=device_id,
                    target=[[r, c] for r, c in com_object_target],
                    app_program_id=app_program_id,
                )
            )
        if not events:
            return
        store = self._state(project_id).store
        if len(events) == 1 and label is None:
            store.append(events[0])
        else:
            store.append(CompositeEvent(events=events, label=label))

    def set_parameter_and_sync_com_objects(
        self,
        project_id: str,
        device_id: int,
        ref_id: str,
        value: str,
        target: list[tuple[str, str | None]],
        app_program_id: str = "",
    ) -> None:
        """Set a parameter and reconcile the device's com-objects to ``target`` as ONE undo step (a
        function/mode change and the objects it activates/deactivates must revert together)."""
        self._state(project_id).store.append(
            CompositeEvent(
                events=[
                    SetParameter(device_id=device_id, ref_id=ref_id, value=value),
                    SyncDeviceComObjects(
                        device_id=device_id,
                        target=[[r, c] for r, c in target],
                        app_program_id=app_program_id,
                    ),
                ]
            )
        )

    def update_device_application(
        self,
        project_id: str,
        device_id: int,
        *,
        product_ref_id: str,
        hardware2program_ref_id: str | None,
        old_app_id: str,
        new_app_id: str,
        valid_ref_ids: list[str],
        order_number: str | None = None,
        hardware_name: str | None = None,
        product_name: str | None = None,
        manufacturer_name: str | None = None,
        description: str | None = None,
        additional_addresses: int = 0,
    ) -> tuple[int, int]:
        """Update a device to a newer version of the same application, keeping parameter values and
        group-address links (see :class:`UpdateDeviceApplication`). Returns ``(kept, dropped)``: how
        many parameter/com-object rows carried over and how many were dropped as incompatible.
        The device gets the additional addresses the new version has more of
        (``additional_addresses``) in the same undo step, see
        :meth:`_more_additional_addresses`."""
        state = self._state(project_id)
        event = UpdateDeviceApplication(
            device_id=device_id,
            new_product_ref_id=product_ref_id,
            new_hardware2program_ref_id=hardware2program_ref_id,
            old_app_id=old_app_id,
            new_app_id=new_app_id,
            valid_ref_ids=valid_ref_ids,
            new_order_number=order_number,
            new_hardware_name=hardware_name,
            new_product_name=product_name,
            new_manufacturer_name=manufacturer_name,
            new_description=description,
        )
        state.store.append(
            self._with_more_additional_addresses(
                state, device_id, event, additional_addresses
            )
        )
        dropped = len(event.deleted_params) + len(event.deleted_com_objects)
        device = state.session.get(Device, device_id)
        kept = (len(device.parameters) + len(device.com_objects)) if device else 0
        return kept, dropped

    def change_device_application(
        self,
        project_id: str,
        device_id: int,
        *,
        hardware2program_ref_id: str,
        new_app_id: str,
        com_objects: list[tuple[str, str | None]],
        module_instances: list[tuple[str, str]] | None = None,
        parameters: list[tuple[str, str]] | None = None,
        com_object_data: list[dict[str, Any] | None] | None = None,
        additional_addresses: int = 0,
    ) -> None:
        """Switch a device to another application program of its hardware, starting from that
        application's defaults plus ``parameters`` and ``com_object_data`` (see
        :class:`ChangeDeviceApplication`). The device gets the additional addresses the new
        application has more of (``additional_addresses``) in the same undo step, see
        :meth:`_more_additional_addresses`."""
        state = self._state(project_id)
        state.store.append(
            self._with_more_additional_addresses(
                state,
                device_id,
                ChangeDeviceApplication(
                    device_id=device_id,
                    new_hardware2program_ref_id=hardware2program_ref_id,
                    new_app_id=new_app_id,
                    com_objects=[[ref, channel] for ref, channel in com_objects],
                    module_instances=[list(row) for row in module_instances or []],
                    parameters=[list(row) for row in parameters or []],
                    com_object_data=list(com_object_data or []),
                ),
                additional_addresses,
            )
        )

    def _with_more_additional_addresses(
        self, state: _Open, device_id: int, event: Event, count: int
    ) -> Event:
        """``event``, together with the additional addresses that take the device to ``count``
        entries when it has fewer (see :meth:`_more_additional_addresses`)."""
        more = self._more_additional_addresses(state, device_id, count)
        return event if more is None else CompositeEvent(events=[event, more])

    def _more_additional_addresses(
        self, state: _Open, device_id: int, count: int
    ) -> SetDeviceAdditionalAddresses | None:
        """The entries that take the device to ``count`` additional addresses, the new ones with
        addresses as :meth:`assign_additional_addresses` picks them; ``None`` when it has as many
        already. When the line has too few free numbers the new entries stay without an address
        (assigning them reports it), so the application change itself still goes through."""
        device = self._require_device(state, device_id)
        entries = additional_address_entries(device)
        if len(entries) >= count:
            return None
        added = [_empty_additional_entry() for _ in range(count - len(entries))]
        if device.segment is not None:
            with contextlib.suppress(AdditionalAddressError):
                _fill_additional_entries(
                    added, self._line_numbers_in_use(state, device.segment.line_id)
                )
        return SetDeviceAdditionalAddresses(
            device_id=device_id, entries=entries + added
        )

    def move_device(
        self,
        project_id: str,
        device_id: int,
        segment_id: int,
        address: int | None,
        *,
        couplers: Collection[int] = (),
    ) -> None:
        """Move a device to ``segment_id`` with the device number ``address``. Moved to another
        line, its additional addresses are assigned again in the new line, in the same undo step;
        too few free numbers there raise :class:`AdditionalAddressError`. A new individual
        address is no longer the downloaded one (see :meth:`_address_change_events`)."""
        state = self._state(project_id)
        if address is not None:
            self._check_unique_address(state, segment_id, address, exclude=device_id)
        events: list[Event] = [
            MoveDevice(device_id=device_id, segment_id=segment_id, address=address)
        ]
        device = state.session.get(Device, device_id)
        target = state.session.get(Segment, segment_id)
        if device is not None and target is not None:
            other_line = (
                device.segment is None or device.segment.line_id != target.line_id
            )
            if device.additional_addresses and other_line:
                entries = additional_address_entries(device)
                for entry in entries:
                    entry["address"] = None
                used = self._line_numbers_in_use(
                    state, target.line_id, without_device=device_id
                )
                if address is not None:
                    used.add(address)
                _fill_additional_entries(entries, used)
                events.append(
                    SetDeviceAdditionalAddresses(device_id=device_id, entries=entries)
                )
            if other_line or device.address != address:
                events.extend(_address_change_events([device], couplers))
        state.store.append(events[0] if len(events) == 1 else CompositeEvent(events))

    def unlink_device(
        self, project_id: str, device_id: int, *, couplers: Collection[int] = ()
    ) -> None:
        """Take a device off its line ("Unlink" in the topology): it is on no line then and has
        no individual address, nor addresses for its additional addresses (the entries stay).
        One undo step; its individual address is no longer the downloaded one (see
        :meth:`_address_change_events`). Nothing happens for a device on no line. Raises
        ``KeyError`` for an unknown device."""
        state = self._state(project_id)
        device = self._require_device(state, device_id)
        if device.segment_id is None:
            return
        events: list[Event] = [
            MoveDevice(device_id=device_id, segment_id=None, address=None)
        ]
        if device.additional_addresses:
            entries = additional_address_entries(device)
            for entry in entries:
                entry["address"] = None
            events.append(
                SetDeviceAdditionalAddresses(device_id=device_id, entries=entries)
            )
        events.extend(_address_change_events([device], couplers))
        state.store.append(events[0] if len(events) == 1 else CompositeEvent(events))

    def park_device(
        self, project_id: str, device_id: int, *, couplers: Collection[int] = ()
    ) -> None:
        """Clear a device's device number ("Park" in the topology): it stays on its line and
        segment ("1.5.-"), its additional addresses keep theirs. One undo step; its individual
        address is no longer the downloaded one (see :meth:`_address_change_events`). Nothing
        happens for a device without a device number or on no line. Raises ``KeyError`` for an
        unknown device."""
        state = self._state(project_id)
        device = self._require_device(state, device_id)
        if device.segment_id is None or device.address is None:
            return
        events: list[Event] = [
            MoveDevice(device_id=device_id, segment_id=device.segment_id, address=None)
        ]
        events.extend(_address_change_events([device], couplers))
        state.store.append(events[0] if len(events) == 1 else CompositeEvent(events))

    def move_line(
        self,
        project_id: str,
        line_id: int,
        area_id: int,
        *,
        coupler_of: CouplerOf | None = None,
        ip_routers: Collection[int] = (),
        line_names: Mapping[int, str] | None = None,
        couplers: Collection[int] = (),
    ) -> int:
        """Put a sub line into another area, one undo step; its devices' individual addresses are
        no longer the downloaded ones (``couplers``: see :func:`_address_change_events`). It keeps its number when that is
        free there, else gets the lowest free one; its devices keep their device numbers. Its
        main segment takes the domain address of the area's main line when it has its medium,
        none on TP or IP, else a new one. The couplers on the line (``coupler_of``: a device's
        coupler data and name, None for no coupler) are checked where they end up. Returns the
        line's number. Raises ``MediumError`` for a move the rules refuse, ``KeyError`` for an
        unknown line or area."""
        state = self._state(project_id)
        line = state.session.get(Line, line_id)
        area = state.session.get(Area, area_id)
        if line is None or area is None:
            raise KeyError(f"No line {line_id} or area {area_id}")
        lines = _line_views(area.installation, line_names)
        view = next(v for v in lines if v.id == line_id)
        target_lines = [v for v in lines if v.area == area.address]
        check_line_move(view, target_lines, area.address)
        used = {v.address for v in target_lines}
        number = (
            line.address
            if line.address not in used
            else min(n for n in range(1, MAX_LINES) if n not in used)
        )
        moved = replace(view, area=area.address, address=number)
        after = [moved if v.id == line_id else v for v in lines]
        if coupler_of is not None:
            for segment in line.segments:
                for device in segment.devices:
                    self._check_moved_coupler(
                        after, segment.id, device, coupler_of, ip_routers
                    )
        events: list[Event] = [
            MoveLine(line_id=line_id, area_id=area_id, address=number)
        ]
        main = _main_segment(line)
        target_main = next((v for v in target_lines if v.address == 0), None)
        domain = _moved_line_domain(
            main.medium_type,
            target_main.main if target_main is not None else None,
            {
                s.domain_address
                for v in lines
                for s in v.segments
                if s.domain_address is not None and s.id != main.id
            },
        )
        if domain != main.domain_address:
            events.append(
                SetSegmentDomainAddress(segment_id=main.id, domain_address=domain)
            )
        events.extend(
            _address_change_events(
                (d for s in line.segments for d in s.devices), couplers
            )
        )
        state.store.append(events[0] if len(events) == 1 else CompositeEvent(events))
        return number

    def move_segment(
        self,
        project_id: str,
        segment_id: int,
        line_id: int,
        *,
        coupler_of: CouplerOf | None = None,
        ip_routers: Collection[int] = (),
        line_names: Mapping[int, str] | None = None,
        couplers: Collection[int] = (),
    ) -> int:
        """Put a sub-segment onto another line, one undo step; it gets the line's lowest free
        segment number, and its devices' individual addresses are no longer the downloaded ones
        (``couplers``: see :func:`_address_change_events`). Its devices keep their device numbers unless one of them is taken in
        the line: then each whose number is taken gets the line's lowest free one (none when
        no number is left). Devices with additional addresses get them again in the line. The
        segment couplers on it (``coupler_of``) are checked where they end up. Returns the
        segment's number; nothing changes when it is on the line already. Raises
        ``MediumError`` for a move the rules refuse, :class:`AdditionalAddressError` when the
        line has too few free numbers for additional addresses, ``KeyError`` for an unknown
        segment or line."""
        state = self._state(project_id)
        segment = state.session.get(Segment, segment_id)
        line = state.session.get(Line, line_id)
        if segment is None or line is None:
            raise KeyError(f"No segment {segment_id} or line {line_id}")
        lines = _line_views(line.area.installation, line_names)
        source = next(v for v in lines if v.id == segment.line_id)
        target = next(v for v in lines if v.id == line_id)
        view = next(s for s in source.segments if s.id == segment_id)
        is_main = view.number == source.main.number
        if segment.line_id == line_id and not is_main:
            return segment.number
        check_segment_move(view, is_main, target)
        taken_numbers = {s.number for s in target.segments}
        number = min(n for n in range(1, MAX_SEGMENTS) if n not in taken_numbers)
        moved = replace(view, number=number)
        after = [
            replace(v, segments=(*v.segments, moved))
            if v.id == line_id
            else replace(v, segments=tuple(s for s in v.segments if s.id != segment_id))
            if v.id == source.id
            else v
            for v in lines
        ]
        devices = sorted(
            segment.devices, key=lambda d: (d.address is None, d.address, d.id)
        )
        if coupler_of is not None:
            for device in devices:
                self._check_moved_coupler(
                    after, segment_id, device, coupler_of, ip_routers
                )
        events: list[Event] = [
            MoveSegment(segment_id=segment_id, line_id=line_id, number=number)
        ]
        taken = {n for s in target.segments for _id, n in s.devices if n is not None}
        numbers = [d.address for d in devices]
        used = self._line_numbers_in_use(state, line_id) | {
            n for n in numbers if n is not None and n not in taken
        }
        renumber = (
            bool(taken)
            and any(n in taken for n in numbers if n is not None)
            and len(numbers) == len(set(numbers))
        )
        for device in devices:
            if device.address is None:
                continue
            if renumber and device.address in taken:
                new = next((n for n in range(1, 256) if n not in used), None)
                if new is not None:
                    used.add(new)
                events.append(
                    MoveDevice(device_id=device.id, segment_id=segment_id, address=new)
                )
            else:
                taken.add(device.address)
        for device in devices:
            if not device.additional_addresses:
                continue
            entries = additional_address_entries(device)
            for entry in entries:
                entry["address"] = None
            _fill_additional_entries(entries, used)
            used.update(cast(int, entry["address"]) for entry in entries)
            events.append(
                SetDeviceAdditionalAddresses(device_id=device.id, entries=entries)
            )
        events.extend(_address_change_events(devices, couplers))
        state.store.append(events[0] if len(events) == 1 else CompositeEvent(events))
        return number

    def _check_moved_coupler(
        self,
        lines: Sequence[LineView],
        segment_id: int,
        device: Device,
        coupler_of: CouplerOf,
        ip_routers: Collection[int],
    ) -> None:
        """Raise a ``MediumError`` when ``device`` is a coupler that may not be on the segment
        ``segment_id`` of the topology ``lines`` (as it is after a move)."""
        found = coupler_of(device.id, device.hardware2program_ref_id)
        if found is None:
            return
        coupler, label = found
        check_coupler(
            lines,
            segment_id,
            coupler,
            label,
            ip_routers=ip_routers,
            moving={device.id},
        )

    # --- undo / redo / history --------------------------------------------

    def undo(self, project_id: str) -> bool:
        return self._state(project_id).store.undo()

    def redo(self, project_id: str) -> bool:
        return self._state(project_id).store.redo()

    def peek_undo(self, project_id: str) -> tuple[str, dict[str, Any]] | None:
        """(type, data) of the event ``undo`` would revert next — lets a caller pick a cheaper
        refresh (e.g. an in-place parameter update) instead of a full rebuild."""
        return self._state(project_id).store.peek_undo()

    def peek_redo(self, project_id: str) -> tuple[str, dict[str, Any]] | None:
        return self._state(project_id).store.peek_redo()

    def can_undo(self, project_id: str) -> bool:
        return self._state(project_id).store.can_undo()

    def can_redo(self, project_id: str) -> bool:
        return self._state(project_id).store.can_redo()

    def cursor(self, project_id: str) -> int:
        return self._state(project_id).store.cursor

    def group_since(
        self, project_id: str, start: int, label: str | None = None
    ) -> None:
        """Make the edits after the history position ``start`` (a :meth:`cursor`) one undo step,
        ``label`` naming it (see :meth:`.EventStore.group_since`)."""
        self._state(project_id).store.group_since(start, label)

    def jump_to(self, project_id: str, event_id: int) -> None:
        self._state(project_id).store.jump_to(event_id)

    def history(self, project_id: str) -> list[HistoryEntry]:
        return self._state(project_id).store.history()

    # --- maintenance --------------------------------------------------------

    def analyse_compaction(
        self, project_id: str, progress: ProgressCallback | None = None
    ) -> CompactResult:
        """Dry run of :meth:`compact`: what would be removed and the estimated file size."""
        state = self._state(project_id)
        return analyse_compaction(state.session, self._path(state), progress)

    def compact(
        self,
        project_id: str,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> CompactResult:
        """Remove stored product data no device uses and ``VACUUM`` the file (not undoable)."""
        state = self._state(project_id)
        return compact_project(
            state.session, state.engine, self._path(state), progress, cancelled
        )

    # --- reads ------------------------------------------------------------

    def project(self, project_id: str) -> Project:
        project = self._state(project_id).session.get(Project, project_id)
        assert project is not None
        return project

    def project_traces(self, project_id: str) -> list[tuple[str, str, str]]:
        """The project log entries as ``(date, user_name, comment)``, in document order: a project
        can have thousands, read without building an object for each."""
        rows = (
            self._state(project_id)
            .session.query(
                ProjectTrace.date, ProjectTrace.user_name, ProjectTrace.comment
            )
            .filter(ProjectTrace.project_id == project_id)
            .order_by(ProjectTrace.id)
            .all()
        )
        return [(date, user, comment) for date, user, comment in rows]

    def installations(self, project_id: str) -> list[Installation]:
        return (
            self._state(project_id)
            .session.query(Installation)
            .order_by(Installation.index)
            .all()
        )

    def topology(self, project_id: str, installation: int) -> Installation:
        return self._installation(self._state(project_id), installation)

    def devices(self, project_id: str, *, with_contents: bool = False) -> list[Device]:
        """The project's devices; ``with_contents`` also loads their parameters, module instances,
        group objects and line in the same go (for building every device)."""
        query = self._state(project_id).session.query(Device).order_by(Device.id)
        if with_contents:
            query = query.options(
                selectinload(Device.parameters),
                selectinload(Device.module_instances),
                selectinload(Device.com_objects),
                selectinload(Device.segment)
                .selectinload(Segment.line)
                .selectinload(Line.area),
            )
        return query.all()

    def device_snapshots(self, project_id: str) -> dict[int, tuple[str, str]]:
        """The stored device snapshots: ``device id -> (key, data)``."""
        session = self._state(project_id).session
        return {
            device_id: (key, data)
            for device_id, key, data in session.query(
                DeviceSnapshot.device_id, DeviceSnapshot.key, DeviceSnapshot.data
            )
        }

    def store_device_snapshots(
        self, project_id: str, snapshots: Mapping[int, tuple[str, str]]
    ) -> None:
        """Store ``device id -> (key, data)`` snapshots, replacing a device's previous one. Not an
        undoable step: a snapshot only caches what the device's application derives."""
        if not snapshots:
            return
        session = self._state(project_id).session
        existing = {device_id for (device_id,) in session.query(Device.id)}
        rows = [
            {"device_id": device_id, "key": key, "data": data}
            for device_id, (key, data) in snapshots.items()
            if device_id in existing
        ]
        if not rows:
            return
        statement = sqlite_insert(DeviceSnapshot)
        try:
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[DeviceSnapshot.device_id],
                    set_={
                        "key": statement.excluded.key,
                        "data": statement.excluded.data,
                    },
                ),
                rows,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

    def device(self, project_id: str, device_id: int) -> DeviceInfo:
        """A display-ready device (assembled individual address plus catalog/program refs)."""
        state = self._state(project_id)
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        return DeviceInfo(
            id=device.id,
            name=device.name,
            individual_address=self._compose_ia(device),
            product_ref_id=device.product_ref_id,
            hardware2program_ref_id=device.hardware2program_ref_id,
            description=device.description,
            order_number=device.order_number,
            hardware_name=device.hardware_name,
            product_name=device.product_name,
            manufacturer_name=device.manufacturer_name,
            ip_config=device.ip_config,
            serial_number=device.serial_number,
            last_download=device.last_download,
            individual_address_loaded=device.individual_address_loaded,
            application_program_loaded=device.application_program_loaded,
            communication_part_loaded=device.communication_part_loaded,
            medium_config_loaded=device.medium_config_loaded,
            parameters_loaded=device.parameters_loaded,
            additional_addresses=tuple(self._additional_infos(device)),
            last_modified=device.last_modified,
        )

    def com_object_links(self, project_id: str, com_object_id: int) -> list[LinkInfo]:
        """A com-object's group-address links, sender flagged."""
        return self._links(
            self._state(project_id)
            .session.query(ComObjectLink)
            .filter_by(com_object_id=com_object_id)
            .order_by(ComObjectLink.id)
            .all()
        )

    def group_address_links(
        self, project_id: str, group_address_id: int
    ) -> list[LinkInfo]:
        """Every com-object assigned to a group address, sender flagged."""
        return self._links(
            self._state(project_id)
            .session.query(ComObjectLink)
            .filter_by(group_address_id=group_address_id)
            .order_by(ComObjectLink.id)
            .all()
        )

    def device_links(self, project_id: str) -> list[DeviceLinkInfo]:
        """Every group-address link of the project with its device and flags, in one query."""
        rows = (
            self._state(project_id)
            .session.query(
                ComObject.device_id,
                ComObjectLink.group_address_id,
                ComObjectLink.is_sending,
                ComObject.communication_flag,
                ComObject.read_flag,
                ComObject.write_flag,
                ComObject.transmit_flag,
                ComObject.update_flag,
            )
            .join(ComObjectLink, ComObjectLink.com_object_id == ComObject.id)
            .order_by(ComObjectLink.id)
            .all()
        )
        return [
            DeviceLinkInfo(
                device_id=device_id,
                group_address_id=ga_id,
                is_sending=bool(sending),
                communication=bool(communication),
                read=bool(read),
                write=bool(write),
                transmit=bool(transmit),
                update=bool(update),
            )
            for device_id, ga_id, sending, communication, read, write, transmit, update in rows
        ]

    def group_addresses(self, project_id: str) -> list[GroupAddressInfo]:
        state = self._state(project_id)
        style = self._style(state)
        rows = state.session.query(GroupAddress).order_by(GroupAddress.id).all()
        secured = secured_group_addresses(state.session)
        return [self._ga_info(row, style, secured) for row in rows]

    def group_ranges(self, project_id: str, installation: int) -> list[GroupRangeInfo]:
        """The installation's group-address range tree (roots → children), resolved for display."""
        state = self._state(project_id)
        style = self._style(state)
        inst = self._installation(state, installation)
        # Every range with its children, group addresses and their links in a few queries, instead of
        # one query per range and group address while the tree is resolved. Held until then: the
        # session keeps loaded rows only while something references them.
        loaded = (
            state.session.query(GroupRange)
            .filter(GroupRange.installation_id == inst.id)
            .options(
                selectinload(GroupRange.children),
                selectinload(GroupRange.group_addresses).selectinload(
                    GroupAddress.links
                ),
            )
            .all()
        )
        roots = (
            state.session.query(GroupRange)
            .filter(
                GroupRange.installation_id == inst.id,
                GroupRange.parent_id.is_(None),
            )
            .order_by(GroupRange.range_start)
            .all()
        )
        secured = secured_group_addresses(state.session)
        tree = [self._range_info(r, style, secured) for r in roots]
        del loaded
        return tree

    def space_tree(self, project_id: str, installation: int) -> list[SpaceInfo]:
        """The installation's building/location tree (roots → children), with devices and functions
        resolved for display."""
        state = self._state(project_id)
        style = self._style(state)
        inst = self._installation(state, installation)
        roots = (
            state.session.query(Space)
            .filter(Space.installation_id == inst.id, Space.parent_id.is_(None))
            .order_by(Space.order, Space.id)
            .all()
        )
        return [self._space_info(s, style) for s in roots]

    def unassigned_devices(
        self, project_id: str, installation: int
    ) -> list[SpaceDeviceInfo]:
        """Devices in the installation that are not placed in any space (``space_id IS NULL``)."""
        return self.space_devices(project_id, installation, unassigned_only=True)

    def space_devices(
        self, project_id: str, installation: int, *, unassigned_only: bool = False
    ) -> list[SpaceDeviceInfo]:
        """The installation's devices in address order, as the Buildings view lists them;
        only those placed in no space with ``unassigned_only``."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        query = (
            state.session.query(Device)
            .join(Segment, Device.segment_id == Segment.id)
            .join(Line, Segment.line_id == Line.id)
            .join(Area, Line.area_id == Area.id)
            .filter(Area.installation_id == inst.id)
        )
        if unassigned_only:
            query = query.filter(Device.space_id.is_(None))
        return [
            self._space_device_info(d) for d in sorted(query.all(), key=_address_order)
        ]

    def coupler_pass_through(
        self, project_id: str, coupler_address: int
    ) -> tuple[list[int], list[int]]:
        """The pass-through group addresses for the coupler at raw ``coupler_address``.

        Returns ``(unfiltered, additional)`` as raw 16-bit group-address values, ready for
        :func:`xknxeditor.download.filter_table.compute_coupler_filter_table`. Without these a
        computed filter table blocks addresses the project says must always cross the coupler.

        - ``unfiltered`` - addresses flagged ``Unfiltered`` (KNX PR #651), either on the address
          itself or on any group range that contains it. The flag is inherited down the range tree:
          marking a range unfiltered is how a whole span (e.g. central functions) is passed through,
          so a child range or address inside it inherits it.
        - ``additional`` - the ``AdditionalGroupAddresses`` configured on the coupler's own line.
          A coupler at ``a.l.0`` is the coupler of line ``l`` in area ``a``, which is where the KNX
          schema carries this list (an area/backbone coupler ``a.0.0`` is line 0 of its area, so the
          same lookup applies).
        """
        state = self._state(project_id)
        session = state.session

        unfiltered_ranges = {
            row.id: row.parent_id
            for row in session.query(GroupRange).all()
            if row.unfiltered
        }
        parents = {row.id: row.parent_id for row in session.query(GroupRange).all()}

        def _range_unfiltered(range_id: int | None) -> bool:
            seen: set[int] = set()
            while range_id is not None and range_id not in seen:
                if range_id in unfiltered_ranges:
                    return True
                seen.add(range_id)  # a malformed cycle must not hang the download
                range_id = parents.get(range_id)
            return False

        unfiltered = sorted(
            {
                ga.address
                for ga in session.query(GroupAddress).all()
                if ga.unfiltered or _range_unfiltered(ga.group_range_id)
            }
        )

        area_address = (coupler_address >> 12) & 0xF
        line_address = (coupler_address >> 8) & 0xF
        # Deterministic + defensive: even though import now de-duplicates topology, never let a
        # stray duplicate area/line make this raise (MultipleResultsFound) mid-download; take the
        # lowest-id match.
        line = (
            session.query(Line)
            .join(Area, Line.area_id == Area.id)
            .filter(Area.address == area_address, Line.address == line_address)
            .order_by(Line.id)
            .first()
        )
        additional: set[int] = set()
        if line is not None:
            for raw in line.additional_group_addresses.split(","):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    value = int(raw)
                except ValueError:  # hand-edited project data must not break a download
                    continue
                if 0 <= value <= 0xFFFF:
                    additional.add(value)
        return unfiltered, sorted(additional)

    def group_address(self, project_id: str, group_address_id: int) -> GroupAddressInfo:
        state = self._state(project_id)
        ga = state.session.get(GroupAddress, group_address_id)
        if ga is None:
            raise KeyError(f"No group address with id {group_address_id}")
        return self._ga_info(
            ga, self._style(state), secured_group_addresses(state.session)
        )

    def next_free_group_address(
        self, project_id: str, installation: int, *, start: int = 1
    ) -> int:
        """Lowest free group-address value (>= ``start``) in the installation. Since address 0 is
        reserved, allocation starts at 1."""
        state = self._state(project_id)
        inst = self._installation(state, installation)
        used = {
            address
            for (address,) in state.session.query(GroupAddress.address)
            .join(GroupRange, GroupAddress.group_range_id == GroupRange.id)
            .filter(GroupRange.installation_id == inst.id)
        }
        address = max(1, start)
        while address in used:
            address += 1
        if address > 0xFFFF:
            raise ValueError("no free group address available")
        return address

    # --- individual-address helpers ---------------------------------------

    def individual_address(self, project_id: str, device_id: int) -> str | None:
        """The device's ``area.line.device`` string, or ``None`` when its octet is unset."""
        state = self._state(project_id)
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        return self._compose_ia(device)

    def line_numbers_in_use(self, project_id: str, line_id: int) -> set[int]:
        """The device numbers the line's devices and their additional addresses use."""
        return self._line_numbers_in_use(self._state(project_id), line_id)

    def next_free_individual_address(self, project_id: str, line_id: int) -> int:
        """Lowest free device octet (1-255) over all the line's segments, skipping the numbers its
        devices and their additional addresses use."""
        state = self._state(project_id)
        if state.session.get(Line, line_id) is None:
            raise KeyError(f"No line with id {line_id}")
        used = self._line_numbers_in_use(state, line_id)
        octet = 1
        while octet in used:
            octet += 1
        if octet > 255:
            raise ValueError("no free individual address on this line")
        return octet

    def next_free_individual_address_for_segment(
        self, project_id: str, segment_id: int
    ) -> int:
        """The lowest unused device octet (1-255) on the line the segment belongs to."""
        state = self._state(project_id)
        segment = state.session.get(Segment, segment_id)
        if segment is None:
            raise KeyError(f"No segment with id {segment_id}")
        return self.next_free_individual_address(project_id, segment.line_id)

    def set_individual_address(
        self,
        project_id: str,
        device_id: int,
        address: str,
        *,
        couplers: Collection[int] = (),
    ) -> None:
        """Assign a device an ``area.line.device`` address inside its installation.

        Looks up the line by its area/line numbers. A device staying in its line stays on its
        segment; one moving to another line goes on the line's main segment (a segment is a media
        grouping, not part of the address). Raises when no such line exists. The new address is
        no longer the downloaded one (``couplers``: see :func:`_address_change_events`)."""
        state = self._state(project_id)
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        area_no, line_no, octet = parse_ia(address)
        installation_id = (
            device.segment.line.area.installation_id
            if device.segment is not None
            else self._installation(state, 0).id
        )
        line = (
            state.session.query(Line)
            .join(Area, Line.area_id == Area.id)
            .filter(
                Area.installation_id == installation_id,
                Area.address == area_no,
                Line.address == line_no,
            )
            .first()
        )
        if line is None:
            raise KeyError(f"No line {area_no}.{line_no} in this installation")
        segment = (
            device.segment
            if device.segment is not None and device.segment.line_id == line.id
            else self._first_segment(state, line)
        )
        self.move_device(project_id, device_id, segment.id, octet, couplers=couplers)

    # --- internals --------------------------------------------------------

    def _links(self, rows: list[ComObjectLink]) -> list[LinkInfo]:
        return [
            LinkInfo(
                id=r.id,
                com_object_id=r.com_object_id,
                group_address_id=r.group_address_id,
                is_sending=r.is_sending,
            )
            for r in rows
        ]

    def _ga_info(
        self, ga: GroupAddress, style: GroupAddressStyle, secured: set[int]
    ) -> GroupAddressInfo:
        return GroupAddressInfo(
            id=ga.id,
            address=ga.address,
            text=format_ga(ga.address, style),
            name=ga.name,
            datapoint_type=ga.datapoint_type,
            links=[link.com_object_id for link in ga.links],
            description=ga.description,
            comment=ga.comment,
            data_secure=ga.id in secured,
            security_mode=ga.security_mode,
            effective_security_mode=effective_mode(ga),
            forced_by=_forced_by(ga.group_range),
            central=ga.central,
        )

    def _range_info(
        self,
        group_range: GroupRange,
        style: GroupAddressStyle,
        secured: set[int],
        level: int = 0,
    ) -> GroupRangeInfo:
        return GroupRangeInfo(
            id=group_range.id,
            name=group_range.name,
            description=group_range.description,
            range_start=group_range.range_start,
            range_end=group_range.range_end,
            children=[
                self._range_info(child, style, secured, level + 1)
                for child in sorted(group_range.children, key=lambda c: c.range_start)
            ],
            group_addresses=[
                self._ga_info(ga, style, secured)
                for ga in sorted(group_range.group_addresses, key=lambda g: g.address)
            ],
            address=format_group_range(group_range.range_start, style, level),
            security_mode=group_range.security_mode,
            forced_by=_forced_by(group_range.parent),
        )

    def _space_info(self, space: Space, style: GroupAddressStyle) -> SpaceInfo:
        return SpaceInfo(
            id=space.id,
            name=space.name,
            space_type=space.space_type,
            number=space.number,
            usage_text=space.usage_text,
            description=space.description,
            children=[
                self._space_info(child, style)
                for child in sorted(space.children, key=lambda c: (c.order, c.id))
            ],
            devices=[
                self._space_device_info(device)
                for device in sorted(space.devices, key=_address_order)
            ],
            functions=[
                self._function_info(fn, style)
                for fn in sorted(space.functions, key=lambda f: (f.order, f.id))
            ],
            usage=space.usage,
            default_line_id=space.default_line_id,
        )

    def _space_device_info(self, device: Device) -> SpaceDeviceInfo:
        return SpaceDeviceInfo(
            id=device.id,
            name=device.name,
            individual_address=self._compose_ia(device),
            description=device.description,
            product_name=device.product_name,
            hardware_name=device.hardware_name,
            manufacturer_name=device.manufacturer_name,
        )

    def _function_info(
        self, function: Function, style: GroupAddressStyle
    ) -> FunctionInfo:
        return FunctionInfo(
            id=function.id,
            name=function.name,
            function_type=function.function_type,
            usage_text=function.usage_text,
            group_addresses=[
                FunctionGroupAddressInfo(
                    id=link.id,
                    group_address_id=link.group_address_id,
                    text=format_ga(link.group_address.address, style),
                    role=link.role,
                    name=link.group_address.name or "",
                )
                for link in function.group_addresses
            ],
            description=function.description,
        )

    def _register(
        self,
        project_id: str,
        engine: Engine,
        session: Session,
        cipher: ProjectCipher | None = None,
        password: str | None = None,
    ) -> None:
        self._projects[project_id] = _Open(
            engine, session, EventStore(session), cipher, password
        )

    @staticmethod
    def _path(state: _Open) -> Path:
        database = state.engine.url.database
        assert database is not None
        return Path(database)

    def _state(self, project_id: str) -> _Open:
        return self._projects[project_id]

    def _installation(self, state: _Open, index: int) -> Installation:
        inst = state.session.query(Installation).filter_by(index=index).first()
        if inst is None:
            raise KeyError(f"No installation with index {index}")
        return inst

    def _style(self, state: _Open) -> GroupAddressStyle:
        # Only the style column: the project row also holds the product archives (tens of MB).
        style = state.session.query(Project.group_address_style).limit(1).scalar()
        assert style is not None
        return GroupAddressStyle(style)

    def _compose_ia(self, device: Device) -> str | None:
        if device.address is None or device.segment is None:
            return None
        line = device.segment.line
        return format_ia(line.area.address, line.address, device.address)

    @staticmethod
    def _require_device(state: _Open, device_id: int) -> Device:
        device = state.session.get(Device, device_id)
        if device is None:
            raise KeyError(f"No device with id {device_id}")
        return device

    @staticmethod
    def _additional_infos(device: Device) -> list[AdditionalAddressInfo]:
        line = device.segment.line if device.segment is not None else None
        return [
            AdditionalAddressInfo(
                index=index,
                address=format_ia(line.area.address, line.address, row.address)
                if row.address is not None and line is not None
                else None,
                octet=row.address,
                name=row.name,
                description=row.description,
            )
            for index, row in enumerate(device.additional_addresses, start=1)
        ]

    @staticmethod
    def _line_numbers_in_use(
        state: _Open, line_id: int, *, without_device: int | None = None
    ) -> set[int]:
        """The device numbers the line's devices and their additional addresses use (the
        additional addresses of ``without_device`` left out)."""
        devices = (
            state.session.query(Device.address)
            .join(Segment, Device.segment_id == Segment.id)
            .filter(Segment.line_id == line_id, Device.address.is_not(None))
        )
        additional = (
            state.session.query(DeviceAdditionalAddress.address)
            .join(Device, DeviceAdditionalAddress.device_id == Device.id)
            .join(Segment, Device.segment_id == Segment.id)
            .filter(
                Segment.line_id == line_id,
                DeviceAdditionalAddress.address.is_not(None),
            )
        )
        if without_device is not None:
            additional = additional.filter(Device.id != without_device)
        return {n for (n,) in devices if n is not None} | {
            n for (n,) in additional if n is not None
        }

    def _first_segment(self, state: _Open, line: Line) -> Segment:
        segment = (
            state.session.query(Segment)
            .filter_by(line_id=line.id)
            .order_by(Segment.number, Segment.id)
            .first()
        )
        if segment is None:
            raise KeyError(f"Line {line.id} has no segment")
        return segment

    def _check_unique_address(
        self,
        state: _Open,
        segment_id: int,
        address: int,
        exclude: int | None = None,
    ) -> None:
        segment = state.session.get(Segment, segment_id)
        if segment is None:
            raise KeyError(f"No segment with id {segment_id}")
        query = (
            state.session.query(Device)
            .join(Segment, Device.segment_id == Segment.id)
            .filter(Segment.line_id == segment.line_id, Device.address == address)
        )
        if exclude is not None:
            query = query.filter(Device.id != exclude)
        clash = query.first()
        if clash is not None:
            raise ValueError(
                f"Individual address {address} already used on this line by device {clash.id}"
            )
        additional = (
            state.session.query(DeviceAdditionalAddress)
            .join(Device, DeviceAdditionalAddress.device_id == Device.id)
            .join(Segment, Device.segment_id == Segment.id)
            .filter(
                Segment.line_id == segment.line_id,
                DeviceAdditionalAddress.address == address,
            )
            .first()
        )
        if additional is not None:
            raise ValueError(
                f"Individual address {address} already used on this line as an additional "
                f"address of device {additional.device_id}"
            )

"""The texts of the monitor columns: names and building structure from the project, DPT and
value decoding, and the cached cells of each record."""

from __future__ import annotations

import enum
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from editor_gui.dpt import lookup_or_make_dpt, transcoder_for
from editor_gui.dpt_formats import BitFormat as BitFormat
from editor_gui.dpt_formats import DptFormat as DptFormat
from editor_gui.dpt_formats import EnumFormat as EnumFormat
from editor_gui.dpt_formats import NumberFormat as NumberFormat
from editor_gui.dpt_formats import master_formats as master_formats
from editor_gui.plugins.monitor.frames import (
    MESSAGE_CODE_NAMES,
    FrameInfo,
    ack_name,
    individual_address_text,
)
from editor_gui.plugins.monitor.records import (
    TICKS_PER_SECOND,
    MonitorRecord,
    RecordKind,
)
from editor_gui.plugins.monitor.strings import S
from editor_gui.widgets.float_format import general_float

if TYPE_CHECKING:
    from xknxeditor.proj.core.service import SpaceInfo


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    # Optional columns are hidden until switched on in the header's context menu.
    optional: bool
    # Initial width in pixels at 100 % scaling.
    width: float

    @property
    def label(self) -> str:
        return getattr(S, f"COL_{self.key.upper()}")


COLUMNS: tuple[Column, ...] = (
    Column("number", False, 48),
    Column("time", False, 54),
    Column("date", False, 84),
    Column("service", False, 60),
    Column("type_short", True, 46),
    Column("flags", False, 36),
    Column("prio", False, 48),
    Column("source_address", False, 56),
    Column("source_name", False, 160),
    Column("destination_address", False, 63),
    Column("destination_name", False, 181),
    Column("building_function", False, 80),
    Column("building_part", False, 80),
    Column("hop_count", False, 30),
    Column("type", False, 127),
    Column("dpt", False, 130),
    Column("info", False, 103),
    Column("iack", True, 40),
    Column("raw_data", True, 220),
    Column("raw_value", True, 90),
    Column("message_code", True, 80),
    Column("repeated", True, 50),
    Column("security", True, 50),
    Column("frame_format", True, 70),
    Column("system_broadcast", True, 60),
    Column("interface", True, 160),
    Column("medium_info", True, 80),
    Column("source_description", True, 160),
    Column("destination_description", True, 160),
)
COLUMN_INDEX = {column.key: index for index, column in enumerate(COLUMNS)}
# The columns of a printout, in this order.
PRINT_COLUMNS = tuple(
    index for index, column in enumerate(COLUMNS) if not column.optional
)
PRINT_WEIGHTS = (
    3.2,
    6.0,
    5.5,
    4.5,
    2.8,
    3.4,
    5.0,
    11.0,
    5.4,
    11.0,
    6.5,
    6.5,
    2.6,
    8.5,
    8.5,
    12.0,
)


# --- project index ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroupAddressEntry:
    address: str
    name: str
    dpt: str | None = None
    building_part: str = ""
    building_function: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class DeviceEntry:
    name: str
    building_part: str = ""
    description: str = ""


# Value texts of common 1-bit types when the master data is not available.
_BIT_TEXTS = {
    1: ("Off", "On"),
    2: ("False", "True"),
    3: ("Disable", "Enable"),
    5: ("No alarm", "Alarm"),
    8: ("Up", "Down"),
    9: ("Open", "Close"),
    10: ("Stop", "Start"),
    11: ("Inactive", "Active"),
    19: ("Closed", "Open"),
}
# Scaled 8-bit types when the master data is not available.
_SCALED = {
    (5, 1): NumberFormat("%", 100 / 255),
    (5, 3): NumberFormat("°", 360 / 255),
}
# Fraction digits of float types.
_FLOAT_DIGITS = {9: 2, 14: 3}


@dataclass(slots=True)
class ProjectIndex:
    """What the monitor shows about the project's addresses; rebuilt when the project changes."""

    revision: object = None
    group_addresses: dict[int, GroupAddressEntry] = field(
        default_factory=dict[int, GroupAddressEntry]
    )
    devices: dict[int, DeviceEntry] = field(default_factory=dict[int, DeviceEntry])
    # Formats a group address of the project's style; ``None`` = three levels.
    ga_style: Any = None
    formats: dict[str, DptFormat] = field(default_factory=dict[str, DptFormat])
    decimal: str = ","

    def group_address_text(self, raw: int) -> str:
        entry = self.group_addresses.get(raw)
        if entry is not None:
            return entry.address
        if self.ga_style is not None:
            from xknxeditor.proj.core.addressing import format_ga

            return format_ga(raw, self.ga_style)
        return f"{raw >> 11}/{(raw >> 8) & 0x07}/{raw & 0xFF}"

    def group_address_raw(self, text: str) -> int | None:
        text = text.strip()
        for raw, entry in self.group_addresses.items():
            if entry.address == text:
                return raw
        parts = text.split("/")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return None
        if len(numbers) == 3:
            main, middle, sub = numbers
            return (main & 0x1F) << 11 | (middle & 0x07) << 8 | sub & 0xFF
        if len(numbers) == 2:
            return (numbers[0] & 0x1F) << 11 | numbers[1] & 0x7FF
        if len(numbers) == 1:
            return numbers[0] & 0xFFFF
        return None


class _GroupAddressLike(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def address(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def datapoint_type(self) -> str | None: ...
    @property
    def raw(self) -> int: ...
    @property
    def description(self) -> str: ...


class _DeviceLike(Protocol):
    @property
    def node_id(self) -> int: ...
    @property
    def individual_address(self) -> str: ...
    @property
    def display_name(self) -> str: ...


def build_index(
    *,
    revision: object,
    group_addresses: Iterable[_GroupAddressLike],
    devices: Iterable[_DeviceLike],
    spaces: Sequence[SpaceInfo],
    ga_style: Any = None,
    formats: dict[str, DptFormat] | None = None,
    decimal: str = ",",
) -> ProjectIndex:
    """Index the project's group addresses and devices with their building part and function."""
    ga_place: dict[int, tuple[str, str]] = {}
    device_place: dict[int, str] = {}

    def walk(nodes: Sequence[SpaceInfo]) -> None:
        for space in nodes:
            for function in space.functions:
                for link in function.group_addresses:
                    ga_place.setdefault(
                        link.group_address_id, (space.name, function.name)
                    )
            for device in space.devices:
                device_place.setdefault(device.id, space.name)
            walk(space.children)

    walk(spaces)
    gas: dict[int, GroupAddressEntry] = {}
    for ga in group_addresses:
        part, function = ga_place.get(ga.id, ("", ""))
        gas[ga.raw] = GroupAddressEntry(
            address=ga.address,
            name=ga.name,
            dpt=ga.datapoint_type,
            building_part=part,
            building_function=function,
            description=ga.description,
        )
    entries: dict[int, DeviceEntry] = {}
    for device in devices:
        raw = parse_individual_address(device.individual_address)
        if raw is None or raw in entries:
            continue
        entries[raw] = DeviceEntry(
            name=device.display_name,
            building_part=device_place.get(device.node_id, ""),
            # Devices whose application could not be loaded have no description.
            description=str(getattr(device, "description", "")),
        )
    return ProjectIndex(
        revision=revision,
        group_addresses=gas,
        devices=entries,
        ga_style=ga_style,
        formats=formats or {},
        decimal=decimal,
    )


def parse_individual_address(text: str | None) -> int | None:
    try:
        area, line, number = (int(part) for part in (text or "").split("."))
    except ValueError:
        return None
    return (area & 0x0F) << 12 | (line & 0x0F) << 8 | number & 0xFF


# --- DPT and values --------------------------------------------------------------------------


def dpt_numbers(token: str | None) -> tuple[int, int | None] | None:
    """``(main, sub)`` of ``DPST-5-1`` / ``DPT-5`` / ``5.001``; ``sub`` is ``None`` without one."""
    if not token:
        return None
    try:
        if token.startswith("DPST-"):
            _, main, sub = token.split("-")[:3]
            return int(main), int(sub)
        if token.startswith("DPT-"):
            return int(token.split("-")[1]), None
        if "." in token:
            main, sub = token.split(".")[:2]
            return int(main), int(sub)
        return int(token), None
    except ValueError:
        return None


def dpt_text(token: str | None) -> str:
    """Number and name of a DPT, e.g. ``1.001 Switch``."""
    numbers = dpt_numbers(token)
    if numbers is None:
        return ""
    main, sub = numbers
    if sub is None:
        dpt = lookup_or_make_dpt(str(main))
        return f"{main}.xxx {dpt.name}".strip()
    dpt = lookup_or_make_dpt(f"{main}.{sub}")
    return f"{dpt.code} {dpt.name}".strip()


def raw_value_text(payload: Any) -> str:
    """``$XX`` for values of up to one octet, space-separated hex octets otherwise."""
    value = getattr(payload, "value", None)
    if isinstance(value, int):
        return f"${value:02X}"
    if isinstance(value, (tuple, list, bytes, bytearray)):
        octets = [int(octet) & 0xFF for octet in cast("Sequence[Any]", value)]
        if len(octets) == 1:
            return f"${octets[0]:02X}"
        return " ".join(f"{octet:02X}" for octet in octets)
    return ""


def hex_text(data: bytes) -> str:
    if len(data) == 1:
        return f"${data[0]:02X}"
    return " ".join(f"{octet:02X}" for octet in data)


def format_number(value: float, digits: int | None, decimal: str) -> str:
    if not math.isfinite(value):
        return str(value)
    text = f"{value:.{digits}f}" if digits is not None else general_float(value)
    return text.replace(".", decimal)


def with_unit(text: str, unit: str) -> str:
    if not unit:
        return text
    return f"{text}{unit}" if unit.startswith("%") else f"{text} {unit}"


def decoded_value_text(
    payload: Any,
    token: str | None,
    formats: dict[str, DptFormat] | None = None,
    decimal: str = ",",
) -> str:
    """The value of ``payload`` in its DPT, with unit; empty when it cannot be decoded."""
    numbers = dpt_numbers(token)
    if numbers is None:
        return ""
    main, sub = numbers
    fmt = (formats or {}).get(f"DPST-{main}-{sub}") if sub is not None else None
    if fmt is None and sub is not None:
        fmt = _SCALED.get((main, sub))
    raw = getattr(payload, "value", None)
    raw_int = _payload_int(raw)
    if isinstance(fmt, BitFormat) and raw_int is not None:
        return fmt.set if raw_int & 0x01 else fmt.cleared
    if isinstance(fmt, EnumFormat) and raw_int is not None:
        return fmt.texts.get(raw_int, str(raw_int))
    if (
        isinstance(fmt, NumberFormat)
        and fmt.coefficient is not None
        and raw_int is not None
    ):
        return with_unit(format_number(raw_int * fmt.coefficient, 1, decimal), fmt.unit)
    transcoder = transcoder_for(
        token if token and token.startswith("DP") else None
    ) or (transcoder_for(f"DPST-{main}-{sub}") if sub is not None else None)
    if transcoder is None:
        transcoder = transcoder_for(f"DPT-{main}")
    if transcoder is None:
        return ""
    try:
        value = transcoder.from_knx(payload)
    except Exception:
        return ""
    unit = (fmt.unit if isinstance(fmt, NumberFormat) else "") or (
        transcoder.unit or ""
    )
    if isinstance(value, bool):
        cleared, set_ = (
            _BIT_TEXTS.get(sub or 0, ("0", "1")) if main == 1 else ("0", "1")
        )
        return set_ if value else cleared
    if isinstance(value, int):
        return with_unit(str(value), unit)
    if isinstance(value, float):
        return with_unit(format_number(value, _FLOAT_DIGITS.get(main), decimal), unit)
    if isinstance(value, enum.Enum):
        return str(value.name).replace("_", " ").title()
    if isinstance(value, str):
        return value
    return str(cast(object, value))


def _payload_int(raw: Any) -> int | None:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, tuple) and 0 < len(cast("tuple[Any, ...]", raw)) <= 4:
        number = 0
        for octet in cast("tuple[int, ...]", raw):
            number = number << 8 | (int(octet) & 0xFF)
        return number
    return None


def info_text(frame: FrameInfo, dpt: str | None, index: ProjectIndex) -> str:
    """Decoded value of a group telegram, e.g. ``On``, falling back to the raw hex value when it
    can't be decoded; the APDU data of other services."""
    payload = frame.value_payload
    if payload is not None:
        decoded = decoded_value_text(payload, dpt, index.formats, index.decimal)
        return decoded or raw_value_text(payload)
    data = frame.apdu_data
    if data and not frame.is_group_value_service:
        return hex_text(data)
    return ""


# --- cells -----------------------------------------------------------------------------------


def time_text(ticks: int) -> str:
    """Local time of day with milliseconds, e.g. ``19:41:18.824``."""
    seconds, fraction = divmod(ticks, TICKS_PER_SECOND)
    moment = datetime.fromtimestamp(seconds)
    return f"{moment:%H:%M:%S}.{fraction // 10_000:03d}"


def date_text(ticks: int) -> str:
    """Local date, e.g. ``2026-10-12``."""
    seconds = ticks // TICKS_PER_SECOND
    return f"{datetime.fromtimestamp(seconds):%Y-%m-%d}"


def medium_info_text(raw: bytes) -> str:
    """The PL or RF medium information of a cEMI frame's additional information."""
    if len(raw) < 2 or not raw[1]:
        return ""
    info = raw[2 : 2 + raw[1]]
    position = 0
    while position + 2 <= len(info):
        kind, length = info[position], info[position + 1]
        data = info[position + 2 : position + 2 + length]
        if kind in (0x01, 0x02):
            return data.hex(" ").upper()
        position += 2 + length
    return ""


def priority_text(priority: int | None) -> str:
    if priority is None:
        return ""
    return (S.PRIO_SYSTEM, S.PRIO_NORMAL, S.PRIO_URGENT, S.PRIO_LOW)[priority]


def short_type_name(name: str) -> str:
    """A compact service name, e.g. ``GroupValueWrite`` -> ``Write``."""
    return name[len("GroupValue") :] if name.startswith("GroupValue") else name


def format_cells(record: MonitorRecord, index: ProjectIndex) -> tuple[str, ...]:
    """The texts of all columns for ``record``, in :data:`COLUMNS` order."""
    idx = COLUMN_INDEX
    cells = [""] * len(COLUMNS)
    cells[idx["number"]] = str(record.number)
    cells[idx["time"]] = time_text(record.ticks)
    cells[idx["date"]] = date_text(record.ticks)
    kind = record.kind
    if kind is RecordKind.START:
        cells[idx["service"]] = S.ROW_START
        connection = record.connection_name or _option(
            record.connection_options, "Name"
        )
        cells[idx["info"]] = S.INFO_STARTED.format(
            host=record.host, connection=connection, mode=record.mode.value
        )
        return tuple(cells)
    if kind is RecordKind.STOP:
        cells[idx["service"]] = S.ROW_STOP
        cells[idx["info"]] = S.INFO_STOPPED
        return tuple(cells)
    if kind is RecordKind.CONNECTION_ESTABLISHED:
        cells[idx["service"]] = S.ROW_CONNECTION
        cells[idx["info"]] = S.INFO_ESTABLISHED
        return tuple(cells)
    if kind is RecordKind.CONNECTION_LOST:
        cells[idx["service"]] = S.ROW_CONNECTION
        cells[idx["info"]] = S.INFO_LOST
        return tuple(cells)
    if kind is RecordKind.COMMENT:
        cells[idx["service"]] = S.ROW_COMMENT
        cells[idx["info"]] = record.text
        return tuple(cells)
    cells[idx["service"]] = S.ROW_FROM_BUS if record.from_bus else S.ROW_TO_BUS
    cells[idx["interface"]] = record.connection_name
    cells[idx["medium_info"]] = medium_info_text(record.raw)
    cells[idx["raw_data"]] = record.raw.hex(" ").upper()
    cells[idx["message_code"]] = MESSAGE_CODE_NAMES.get(
        record.raw[0], f"${record.raw[0]:02X}"
    )
    if record.iack is not None:
        cells[idx["iack"]] = ack_name(record.iack)
    frame = record.frame
    if frame is None:
        return tuple(cells)
    if frame.malformed or frame.checksum_error:
        cells[idx["service"]] = S.ROW_INVALID_FRAME
    cells[idx["flags"]] = frame.link_flags
    cells[idx["type"]] = frame.type_name
    cells[idx["type_short"]] = short_type_name(frame.type_name)
    if not frame.is_data:
        return tuple(cells)
    cells[idx["prio"]] = priority_text(frame.priority)
    source = frame.source or 0
    cells[idx["source_address"]] = individual_address_text(source)
    device = index.devices.get(source)
    if device is not None:
        cells[idx["source_name"]] = device.name
        cells[idx["source_description"]] = device.description
    destination = frame.destination or 0
    dpt: str | None = None
    if frame.group:
        cells[idx["destination_address"]] = index.group_address_text(destination)
        entry = index.group_addresses.get(destination)
        if entry is not None:
            cells[idx["destination_name"]] = entry.name
            cells[idx["building_function"]] = entry.building_function
            cells[idx["building_part"]] = entry.building_part
            cells[idx["destination_description"]] = entry.description
            dpt = entry.dpt
    else:
        cells[idx["destination_address"]] = individual_address_text(destination)
        target = index.devices.get(destination)
        if target is not None:
            cells[idx["destination_name"]] = target.name
            cells[idx["building_part"]] = target.building_part
            cells[idx["destination_description"]] = target.description
    cells[idx["hop_count"]] = str(frame.hop_count)
    if frame.value_payload is not None:
        cells[idx["dpt"]] = dpt_text(dpt)
        cells[idx["raw_value"]] = raw_value_text(frame.value_payload)
    cells[idx["info"]] = info_text(frame, dpt, index)
    if frame.repeated:
        cells[idx["repeated"]] = S.YES
    if frame.data_secure:
        cells[idx["security"]] = S.YES
    cells[idx["frame_format"]] = (
        S.FRAME_STANDARD if frame.standard_frame else S.FRAME_EXTENDED
    )
    if frame.system_broadcast:
        cells[idx["system_broadcast"]] = S.YES
    return tuple(cells)


def record_cells(
    record: MonitorRecord, index: ProjectIndex, key: object
) -> tuple[str, ...]:
    """The cached cells of ``record``, formatted again when ``key`` changed."""
    cells = record.cells
    if cells is None or record.cells_key != key:
        cells = format_cells(record, index)
        record.cells = cells
        record.cells_key = key
        record.haystack = "\n".join(cells).lower()
    return cells


def _option(options: str, name: str) -> str:
    for part in options.split(";"):
        key, _, value = part.partition("=")
        if key == name:
            return value.strip('"')
    return ""

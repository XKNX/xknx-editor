"""Copies of tree elements (Paste Special, a drop with Ctrl held): devices, building parts,
trades, functions, group addresses, middle and main groups, lines and segments, made as the
options of :class:`~xknxeditor.proj.core.paste.PasteOptions` say.

A copy has its source's name and texts. Devices keep their device number in a line where it is
free and get the lowest free one otherwise; the devices of a copied building part are numbered
from the lowest free one of its line; a device copied where there is no line (a trade, a building
part without a Current Line) gets no individual address. Copies of devices and functions link
what the options say (see :class:`_Links`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from editor_gui.plugins.project.medium_text import medium_error_text
from editor_gui.plugins.project.strings import S
from xknxeditor.proj import ProjectService as CoreService
from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga
from xknxeditor.proj.core.medium import MAX_LINES, MediumError
from xknxeditor.proj.core.paste import (
    AddressPool,
    GroupAddressCopy,
    PasteOptions,
    group_base,
    parse_target_range,
    place_in_group,
)
from xknxeditor.proj.models import (
    Area,
    Base,
    ComObject,
    Device,
    Function,
    GroupAddress,
    GroupRange,
    Line,
    Segment,
    Space,
    Trade,
)

# What is pasted, and where (``None``: the top of the Buildings, Trades or Group Addresses).
PasteKind = Literal[
    "device",
    "space",
    "trade",
    "function",
    "group_address",
    "group_range",
    "line",
    "segment",
]
TargetKind = Literal["space", "trade", "segment", "line", "area", "group_range"]

# Makes a device like ``source`` in the segment (None: on no line), with the device number (None:
# none), in the building part; returns its id, None when its application is not known. Raises
# ``MediumError`` when the segment's medium does not suit it.
CopyDevice = Callable[[Device, int | None, int | None, int | None], int | None]

# The fields a copy takes over from its source, per kind.
_DEVICE_FIELDS = (
    "description",
    "comment",
    "completion_status",
    "installation_hints",
)
# Device attributes kept verbatim that a copy takes over (not its UniqueId).
_DEVICE_ATTRIBUTES = ("IsRFRetransmitter", "Context")
_FLAGS = (
    "read_flag",
    "write_flag",
    "communication_flag",
    "transmit_flag",
    "update_flag",
    "read_on_init_flag",
)
_COM_OBJECT_FIELDS = (
    *_FLAGS,
    *(f"{flag}_override" for flag in _FLAGS),
    "text_override",
    "function_text_override",
    "description_override",
    "priority",
    "datapoint_type_override",
)
_SPACE_FIELDS = (
    "number",
    "description",
    "comment",
    "completion_status",
    "default_line_id",
    "context",
)
_FUNCTION_FIELDS = (
    "description",
    "comment",
    "number",
    "completion_status",
    "usage_text",
    "implements",
    "default_group_range",
    "context",
)
_TRADE_FIELDS = ("number", "description", "comment", "completion_status", "context")
_GROUP_ADDRESS_FIELDS = ("comment", "description", "datapoint_type", "central")
_GROUP_RANGE_FIELDS = ("description", "comment", "security_mode")
_LINE_FIELDS = ("description", "comment", "completion_status")
_SEGMENT_FIELDS = ("description", "comment", "completion_status")
# The device numbers of a line, the line numbers of an area.
_DEVICE_NUMBERS = range(1, 256)
_LINE_NUMBERS = range(1, MAX_LINES)


_MODELS: dict[PasteKind, type[Base]] = {
    "device": Device,
    "space": Space,
    "trade": Trade,
    "function": Function,
    "group_address": GroupAddress,
    "group_range": GroupRange,
    "line": Line,
    "segment": Segment,
}
_TARGET_MODELS: dict[TargetKind, type[Base]] = {
    "space": Space,
    "trade": Trade,
    "segment": Segment,
    "line": Line,
    "area": Area,
    "group_range": GroupRange,
}


@dataclass(frozen=True)
class PasteTarget:
    """Where copies go: a building part, trade, segment, line, area or group range by id;
    ``element_id`` None for the top of the Buildings, the Trades or the Group Addresses."""

    kind: TargetKind
    element_id: int | None


class PasteError(Exception):
    """A paste refused before anything changed; ``reason`` says why: "group_addresses" (the
    group has too few free group addresses), "group_ranges" (too few free main or middle
    groups), "target_range" (no group to create the group addresses in) or "medium" (the
    topology does not allow it, ``medium`` says how)."""

    def __init__(self, reason: str, medium: MediumError | None = None) -> None:
        super().__init__(reason if medium is None else str(medium))
        self.reason = reason
        self.medium = medium


def paste_error_text(error: PasteError) -> str:
    """What ``error`` tells the user, in the language shown."""
    if error.medium is not None:
        return medium_error_text(error.medium)
    if error.reason == "group_ranges":
        return S.PASTE_NO_RANGE_ROOM
    if error.reason == "target_range":
        return S.PASTE_NO_TARGET_RANGE
    return S.PASTE_NO_GA_ROOM


def _values(row: Any, fields: Iterable[str]) -> dict[str, Any]:
    return {name: getattr(row, name) for name in fields}


def _lowest_free(used: set[int], numbers: range) -> int | None:
    return next((n for n in numbers if n not in used), None)


class Paster:
    """Makes the copies of one paste (see the module) through the project service ``core``.
    ``pid`` is the open project, ``installation`` its installation, ``style`` its group
    address style; ``copy_device`` makes a device (see :data:`CopyDevice`)."""

    def __init__(
        self,
        core: CoreService,
        pid: str,
        installation: int,
        style: GroupAddressStyle,
        copy_device: CopyDevice,
    ) -> None:
        self._core = core
        self._pid = pid
        self._installation = installation
        self._style = style
        self._copy_device = copy_device

    # -- reading --------------------------------------------------------------------------------

    @property
    def style(self) -> GroupAddressStyle:
        return self._style

    def row[T: Base](self, model: type[T], row_id: int) -> T:
        """The stored row ``row_id`` of ``model``; raises ``KeyError`` when there is none."""
        row = self._core.row(self._pid, model, row_id)
        if row is None:
            raise KeyError(f"No {model.__name__} with id {row_id}")
        return row

    def _set(self, kind: str, row_id: int, values: dict[str, Any]) -> None:
        self._core.set_fields(self._pid, [[kind, row_id, values]])

    # -- the target group of new group addresses -----------------------------------------------

    def target_range(self, options: PasteOptions, *, create: bool) -> int | None:
        """The group new group addresses go to: the range picked (free style) or the middle or
        main group typed, made first with ``create`` when it does not exist yet. None when
        there is none."""
        if self._style is GroupAddressStyle.FREE:
            return options.target_range_id
        window = parse_target_range(options.target_range, self._style)
        if window is None:
            return None
        first = window[0]
        main_number = first >> 11
        mains = self._core.root_group_ranges(self._pid, self._installation)
        main = next((r for r in mains if r.range_start >> 11 == main_number), None)
        if self._style is GroupAddressStyle.TWO_LEVEL:
            if main is not None:
                return main.id
            if not create:
                return None
            return self._core.create_group_range(
                self._pid, self._installation, None, "", number=main_number
            )
        middle_number = (first >> 8) & 0x7
        if main is not None:
            middle = next(
                (
                    r
                    for r in main.children
                    if (r.range_start >> 8) & 0x7 == middle_number
                ),
                None,
            )
            if middle is not None:
                return middle.id
        if not create:
            return None
        main_id = (
            main.id
            if main is not None
            else self._core.create_group_range(
                self._pid, self._installation, None, "", number=main_number
            )
        )
        if main_id is None:
            return None
        return self._core.create_group_range(
            self._pid, self._installation, main_id, "", number=middle_number
        )

    def address_pool(
        self, group_range: GroupRange, options: PasteOptions
    ) -> AddressPool:
        """The free addresses of ``group_range`` (any group address of the installation in
        them counts as taken), given out as ``options`` say."""
        base = group_base(group_range.range_start, self._style)
        used = self._core.used_group_addresses(self._pid, self._installation)
        return AddressPool(
            base,
            group_range.range_end,
            used,
            options.allocation,
            start_with=options.start_with,
            offset=options.offset,
            base=base,
        )

    def new_group_address(
        self,
        source: GroupAddress,
        group_range: GroupRange,
        address: int,
        *,
        fields: Sequence[str] = _GROUP_ADDRESS_FIELDS,
    ) -> int:
        """A copy of ``source`` at ``address`` in ``group_range``."""
        ga_id = self._core.create_group_address_in(
            self._pid, group_range.id, address, source.name
        )
        self._set("group_address", ga_id, _values(source, fields))
        return ga_id

    def copy_security(self, source: GroupAddress, ga_id: int) -> None:
        """``source``'s own Security on its copy, when it can have it (a secured group address
        needs the project password and securely commissioned devices)."""
        if source.security_mode in (None, "Auto"):
            return
        try:
            self._core.set_group_address_security(
                self._pid, ga_id, source.security_mode
            )
        except (ValueError, KeyError):
            return  # e.g. no project password: the copy keeps the default Security

    # -- naming ---------------------------------------------------------------------------------

    def label(self, kind: PasteKind, ids: Sequence[int], target: PasteTarget) -> str:
        """How the history names the paste of ``ids`` to ``target``."""
        where = self.target_name(target)
        if len(ids) == 1:
            name = self.name(self.row(_MODELS[kind], ids[0]))
            return S.HISTORY_EV_COPIED.format(name=name, target=where)
        if kind == "group_range":
            main = self.row(GroupRange, ids[0]).parent_id is None
            text = (
                S.HISTORY_EV_COPIED_MAIN_GROUPS
                if main
                else S.HISTORY_EV_COPIED_MIDDLE_GROUPS
            )
        else:
            text = {
                "device": S.HISTORY_EV_COPIED_DEVICES,
                "space": S.HISTORY_EV_COPIED_SPACES,
                "trade": S.HISTORY_EV_COPIED_TRADES,
                "function": S.HISTORY_EV_COPIED_FUNCTIONS,
                "group_address": S.HISTORY_EV_COPIED_GROUP_ADDRESSES,
                "line": S.HISTORY_EV_COPIED_LINES,
                "segment": S.HISTORY_EV_COPIED_SEGMENTS,
            }[kind]
        return text.format(count=len(ids), target=where)

    def names(self, kind: PasteKind, ids: Sequence[int]) -> list[str]:
        """The names of the elements ``ids`` of ``kind``."""
        return [self.name(self.row(_MODELS[kind], ident)) for ident in ids]

    def name(self, row: Base) -> str:
        """The name of an element as its tree shows it."""
        if isinstance(row, Device):
            return row.name or row.product_name
        if isinstance(row, GroupAddress):
            return f"{format_ga(row.address, self._style)} {row.name}".strip()
        if isinstance(row, Line):
            return row.name or f"{row.area.address}.{row.address}"
        if isinstance(row, Segment):
            return row.name or self.name(row.line)
        if isinstance(row, Area):
            return row.name or str(row.address)
        return str(getattr(row, "name", ""))

    def target_name(self, target: PasteTarget) -> str:
        """The name of the place copies go to."""
        if target.element_id is None:
            if target.kind == "space":
                return S.SPACES_ROOT_BUILDINGS
            if target.kind == "trade":
                return S.SPACES_ROOT_TRADES
            return S.PANEL_GROUP_ADDRESSES
        return self.name(self.row(_TARGET_MODELS[target.kind], target.element_id))

    # -- pasting --------------------------------------------------------------------------------

    def paste(
        self,
        kind: PasteKind,
        ids: Sequence[int],
        target: PasteTarget,
        options: PasteOptions,
    ) -> list[int]:
        """Make ``options.count`` copies of each element ``ids`` of ``kind`` in ``target``; the
        ids of the copies. Raises ``PasteError`` when the paste cannot be made (nothing is
        changed then)."""
        count = max(1, options.count)
        if kind == "device":
            return self._paste_devices(ids, target, options, count)
        if kind == "space":
            return self._paste_spaces(ids, target.element_id, options, count)
        if kind == "trade":
            return self._paste_trades(ids, target.element_id, options, count)
        if kind == "function":
            assert target.element_id is not None
            return self._paste_functions(ids, target.element_id, options, count)
        if kind == "group_address":
            assert target.element_id is not None
            return self._paste_group_addresses(ids, target.element_id, options, count)
        if kind == "group_range":
            return self._paste_group_ranges(ids, target.element_id, options, count)
        if kind == "line":
            assert target.element_id is not None
            return self._paste_lines(ids, target.element_id, options, count)
        assert target.element_id is not None
        return self._paste_segments(ids, target.element_id, options, count)

    # -- devices --------------------------------------------------------------------------------

    def _device_place(self, target: PasteTarget) -> tuple[int | None, int | None]:
        """The segment and building part a device copied to ``target`` goes to (segment None:
        none, the copy then has no individual address)."""
        if target.kind == "segment":
            return target.element_id, None
        if target.kind == "space" and target.element_id is not None:
            return (
                self._core.current_line_segment(self._pid, target.element_id),
                target.element_id,
            )
        return None, None

    def _paste_devices(
        self,
        ids: Sequence[int],
        target: PasteTarget,
        options: PasteOptions,
        count: int,
    ) -> list[int]:
        sources = sorted((self.row(Device, ident) for ident in ids), key=_device_order)
        links = _Links(self, options, count, self._device_addresses(sources))
        segment_id, space_id = self._device_place(target)
        created: list[int] = []
        for _ in range(count):
            links.next_copy()
            for source in sources:
                number = (
                    self._kept_number(source, segment_id)
                    if segment_id is not None
                    else None
                )
                new_id = self._device_copy(source, segment_id, number, space_id, links)
                if new_id is None:
                    continue
                if target.kind == "trade" and target.element_id is not None:
                    self._core.add_trade_device(self._pid, target.element_id, new_id)
                created.append(new_id)
        return created

    def _kept_number(self, source: Device, segment_id: int) -> int | None:
        """The source's device number in the segment's line when it is free there, else the
        lowest free one; None when the line is full."""
        line_id = self.row(Segment, segment_id).line_id
        used = self._core.line_numbers_in_use(self._pid, line_id)
        if source.address is not None and source.address not in used:
            return source.address
        return _lowest_free(used, _DEVICE_NUMBERS)

    def _lowest_number(self, segment_id: int) -> int | None:
        """The lowest free device number of the segment's line."""
        line_id = self.row(Segment, segment_id).line_id
        return _lowest_free(
            self._core.line_numbers_in_use(self._pid, line_id), _DEVICE_NUMBERS
        )

    def _device_copy(
        self,
        source: Device,
        segment_id: int | None,
        number: int | None,
        space_id: int | None,
        links: _Links,
    ) -> int | None:
        """A copy of ``source`` in the segment with the number (segment None: on no line, also
        when the segment's medium does not suit it), with its fields, group objects and
        links."""
        try:
            new_id = self._copy_device(source, segment_id, number, space_id)
        except MediumError:
            new_id = self._copy_device(source, None, None, space_id)
        if new_id is None:
            return None
        copy = self.row(Device, new_id)
        values = _values(source, _DEVICE_FIELDS)
        attributes = {
            key: value
            for key, value in (source.extra_attributes or {}).items()
            if key in _DEVICE_ATTRIBUTES
        }
        if attributes:
            values["extra_attributes"] = {**(copy.extra_attributes or {}), **attributes}
        rows: list[list[Any]] = [["device", new_id, values]]
        pairs = _pair_objects(source, copy)
        rows += [
            ["com_object", target.id, _values(origin, _COM_OBJECT_FIELDS)]
            for origin, target in pairs
        ]
        self._core.set_fields(self._pid, rows)
        for origin, target in pairs:
            for link in sorted(origin.links, key=lambda link: not link.is_sending):
                ga_id = links.target(link.group_address_id)
                if ga_id is None:
                    continue
                try:
                    self._core.link_com_object(
                        self._pid, target.id, ga_id, sending=link.is_sending
                    )
                except (ValueError, KeyError):
                    continue  # e.g. a secured group address: not linked
        links.secure_new()
        return new_id

    @staticmethod
    def _device_addresses(devices: Iterable[Device]) -> list[int]:
        """The group addresses the devices' group objects link."""
        return [
            link.group_address_id
            for device in devices
            for co in device.com_objects
            for link in co.links
        ]

    # -- building parts and functions ------------------------------------------------------------

    def _paste_spaces(
        self,
        ids: Sequence[int],
        parent_id: int | None,
        options: PasteOptions,
        count: int,
    ) -> list[int]:
        sources = [self.row(Space, ident) for ident in ids]
        devices = [device for space in sources for device in _space_devices(space)]
        functions = [fn for space in sources for fn in _space_functions(space)]
        links = _Links(
            self,
            options,
            count,
            self._device_addresses(devices) + _function_addresses(functions),
        )
        target_segment = (
            self._core.current_line_segment(self._pid, parent_id)
            if parent_id is not None
            else None
        )
        created: list[int] = []
        for _ in range(count):
            links.next_copy()
            for source in sources:
                segment_id = (
                    self._line_segment(source.default_line_id)
                    if source.default_line_id is not None
                    else target_segment
                )
                new_id = self._space_copy(source, parent_id, links)
                created.append(new_id)
                self._place_devices(source, new_id, segment_id, links)
        return created

    def _line_segment(self, line_id: int) -> int:
        line = self.row(Line, line_id)
        return min(line.segments, key=lambda s: (s.number, s.id)).id

    def _space_copy(self, source: Space, parent_id: int | None, links: _Links) -> int:
        """A copy of ``source`` (with its functions and the building parts below it, not yet
        their devices) below ``parent_id``."""
        new_id = self._core.create_space(
            self._pid,
            self._installation,
            source.space_type,
            source.name,
            parent_id,
            usage=source.usage,
            usage_text=source.usage_text,
        )
        self._set("space", new_id, _values(source, _SPACE_FIELDS))
        for function in sorted(source.functions, key=lambda f: (f.order, f.id)):
            self._function_copy(function, new_id, links)
        for child in sorted(source.children, key=lambda s: (s.order, s.id)):
            self._space_copy(child, new_id, links)
        return new_id

    def _place_devices(
        self, source: Space, copy_id: int, segment_id: int | None, links: _Links
    ) -> None:
        """Copies of the devices of ``source`` and the building parts below it into the
        matching parts of its copy ``copy_id``, numbered from the lowest free number of the
        segment's line in the order of their individual addresses."""
        copies: dict[int, int] = {}

        def match(origin: Space, copy: Space) -> None:
            copies[origin.id] = copy.id
            mine = sorted(copy.children, key=lambda s: (s.order, s.id))
            theirs = sorted(origin.children, key=lambda s: (s.order, s.id))
            for child, child_copy in zip(theirs, mine, strict=False):
                match(child, child_copy)

        match(source, self.row(Space, copy_id))
        for device in sorted(_space_devices(source), key=_device_order):
            assert device.space_id is not None
            number = self._lowest_number(segment_id) if segment_id is not None else None
            self._device_copy(
                device, segment_id, number, copies.get(device.space_id), links
            )

    def _paste_functions(
        self,
        ids: Sequence[int],
        space_id: int,
        options: PasteOptions,
        count: int,
    ) -> list[int]:
        sources = [self.row(Function, ident) for ident in ids]
        links = _Links(
            self, options, count, _function_addresses(sources), for_functions=True
        )
        created: list[int] = []
        for _ in range(count):
            links.next_copy()
            created += [self._function_copy(f, space_id, links) for f in sources]
        return created

    def _function_copy(self, source: Function, space_id: int, links: _Links) -> int:
        new_id = self._core.create_function(
            self._pid, space_id, source.function_type, source.name
        )
        self._set("function", new_id, _values(source, _FUNCTION_FIELDS))
        for reference in source.group_addresses:
            ga_id = links.target(reference.group_address_id)
            if ga_id is not None:
                self._core.add_function_group_address(
                    self._pid, new_id, ga_id, reference.role
                )
        return new_id

    # -- trades ---------------------------------------------------------------------------------

    def _paste_trades(
        self,
        ids: Sequence[int],
        parent_id: int | None,
        options: PasteOptions,
        count: int,
    ) -> list[int]:
        sources = [self.row(Trade, ident) for ident in ids]
        devices = [link.device for trade in sources for link in _trade_links(trade)]
        links = _Links(self, options, count, self._device_addresses(devices))
        created: list[int] = []
        for _ in range(count):
            links.next_copy()
            created += [self._trade_copy(t, parent_id, links) for t in sources]
        return created

    def _trade_copy(self, source: Trade, parent_id: int | None, links: _Links) -> int:
        """A copy of ``source`` with the trades below it and copies of their devices (without
        individual addresses)."""
        new_id = self._core.create_trade(
            self._pid, self._installation, source.name, parent_id
        )
        self._set("trade", new_id, _values(source, _TRADE_FIELDS))
        for child in sorted(source.children, key=lambda t: (t.order, t.id)):
            self._trade_copy(child, new_id, links)
        for device in sorted(
            (link.device for link in source.devices), key=_device_order
        ):
            copy = self._device_copy(device, None, None, None, links)
            if copy is not None:
                self._core.add_trade_device(self._pid, new_id, copy)
        return new_id

    # -- group addresses and groups -------------------------------------------------------------

    def _paste_group_addresses(
        self,
        ids: Sequence[int],
        range_id: int,
        options: PasteOptions,
        count: int,
    ) -> list[int]:
        target = self.row(GroupRange, range_id)
        pool = self.address_pool(target, options)
        if pool.room() < len(ids) * count:
            raise PasteError("group_addresses")
        sources = sorted(
            (self.row(GroupAddress, ident) for ident in ids), key=lambda ga: ga.address
        )
        created: list[int] = []
        for _ in range(count):
            for source in sources:
                address = pool.take(
                    place_in_group(
                        source.address, source.group_range.range_start, self._style
                    )
                )
                if address is None:
                    continue
                ga_id = self.new_group_address(source, target, address)
                self.copy_security(source, ga_id)
                created.append(ga_id)
        return created

    def _paste_group_ranges(
        self,
        ids: Sequence[int],
        parent_id: int | None,
        options: PasteOptions,
        count: int,
    ) -> list[int]:
        """Copies of middle groups below the main group ``parent_id``, or of main groups
        (``parent_id`` None); their group addresses keep their place in the group, the middle
        groups of a main group their number."""
        sources = [self.row(GroupRange, ident) for ident in ids]
        if parent_id is None:
            used = {
                r.range_start >> 11
                for r in self._core.root_group_ranges(self._pid, self._installation)
            }
            numbers = range(32)
        else:
            parent = self.row(GroupRange, parent_id)
            used = {(r.range_start >> 8) & 0x7 for r in parent.children}
            numbers = range(8)
        pool = AddressPool(
            numbers.start,
            numbers.stop - 1,
            used,
            options.allocation,
            start_with=options.start_with,
            offset=options.offset,
            base=0,
            skip_zero=False,
        )
        if pool.room() < len(sources) * count:
            raise PasteError("group_ranges")
        created: list[int] = []
        for _ in range(count):
            for source in sources:
                own = (
                    source.range_start >> 11
                    if parent_id is None
                    else (source.range_start >> 8) & 0x7
                )
                number = pool.take(own)
                if number is None:
                    continue
                new_id = self._range_copy(source, parent_id, number)
                if new_id is not None:
                    created.append(new_id)
        return created

    def _range_copy(
        self, source: GroupRange, parent_id: int | None, number: int
    ) -> int | None:
        """A copy of the group ``source`` numbered ``number`` below ``parent_id``, with its
        middle groups (same numbers) and group addresses (same place in the group)."""
        new_id = self._core.create_group_range(
            self._pid, self._installation, parent_id, source.name, number=number
        )
        if new_id is None:
            return None
        self._set("group_range", new_id, _values(source, _GROUP_RANGE_FIELDS))
        copy = self.row(GroupRange, new_id)
        for child in sorted(source.children, key=lambda r: r.range_start):
            self._range_copy(child, new_id, (child.range_start >> 8) & 0x7)
        source_base = group_base(source.range_start, self._style)
        copy_base = group_base(copy.range_start, self._style)
        for ga in sorted(source.group_addresses, key=lambda ga: ga.address):
            address = copy_base + ga.address - source_base
            if copy.range_start <= address <= copy.range_end:
                ga_id = self.new_group_address(ga, copy, address)
                self.copy_security(ga, ga_id)
        return new_id

    # -- lines and segments ---------------------------------------------------------------------

    def _paste_lines(
        self,
        ids: Sequence[int],
        area_id: int,
        options: PasteOptions,
        count: int,
    ) -> list[int]:
        """Copies of lines in the area: the same line number when it is free there, else the
        lowest free; with their segments and copies of their devices."""
        sources = [self.row(Line, ident) for ident in ids]
        used = {line.address for line in self.row(Area, area_id).lines}
        if len(used - {0}) + len(sources) * count > len(_LINE_NUMBERS):
            raise PasteError("medium", MediumError("too_many_lines"))
        devices = [d for line in sources for s in line.segments for d in s.devices]
        links = _Links(self, options, count, self._device_addresses(devices))
        created: list[int] = []
        for _ in range(count):
            links.next_copy()
            for source in sources:
                number = (
                    source.address
                    if source.address not in used and source.address in _LINE_NUMBERS
                    else _lowest_free(used, _LINE_NUMBERS)
                )
                if number is None:
                    continue
                used.add(number)
                line_id = self._core.create_line(
                    self._pid, area_id, number, source.name
                )
                self._set("line", line_id, _values(source, _LINE_FIELDS))
                created.append(line_id)
                segments = sorted(source.segments, key=lambda s: (s.number, s.id))
                main_id = self._line_segment(line_id)
                for segment in segments:
                    segment_id = (
                        main_id
                        if segment is segments[0]
                        else self._segment_copy(segment, line_id)
                    )
                    if segment_id is not None:
                        self._copy_segment_devices(segment, segment_id, links)
        return created

    def _paste_segments(
        self,
        ids: Sequence[int],
        line_id: int,
        options: PasteOptions,
        count: int,
    ) -> list[int]:
        sources = [self.row(Segment, ident) for ident in ids]
        devices = [device for segment in sources for device in segment.devices]
        links = _Links(self, options, count, self._device_addresses(devices))
        created: list[int] = []
        for _ in range(count):
            links.next_copy()
            for source in sources:
                try:
                    segment_id = self._core.add_segment(
                        self._pid,
                        line_id,
                        medium_type=source.medium_type,
                        name=source.name,
                    )
                except MediumError as exc:
                    if not created:
                        raise PasteError("medium", exc) from exc
                    continue
                self._set("segment", segment_id, _values(source, _SEGMENT_FIELDS))
                created.append(segment_id)
                self._copy_segment_devices(source, segment_id, links)
        return created

    def _segment_copy(self, source: Segment, line_id: int) -> int | None:
        """A copy of the sub-segment ``source`` in the line; None when the line cannot have it."""
        try:
            segment_id = self._core.add_segment(
                self._pid, line_id, medium_type=source.medium_type, name=source.name
            )
        except MediumError:
            return None
        self._set("segment", segment_id, _values(source, _SEGMENT_FIELDS))
        return segment_id

    def _copy_segment_devices(
        self, source: Segment, segment_id: int, links: _Links
    ) -> None:
        for device in sorted(source.devices, key=_device_order):
            self._device_copy(
                device,
                segment_id,
                self._kept_number(device, segment_id),
                device.space_id,
                links,
            )


class _Links:
    """What the copies of one paste link instead of what their sources link: nothing
    (``DONT_COPY``), the same group addresses (``KEEP``), or new ones (``CREATE_NEW``): per copy
    one new group address for each group address the sources link (a central one of the project
    stays shared), made in ascending order of the source addresses. ``sources`` are the group
    addresses the sources link; a paste that would not find room for all of them in the target
    group is refused (``PasteError``). New group addresses for function copies take over less
    (no Central, no Security)."""

    def __init__(
        self,
        paster: Paster,
        options: PasteOptions,
        count: int,
        sources: Iterable[int],
        *,
        for_functions: bool = False,
    ) -> None:
        self._paster = paster
        self._mode = options.group_addresses
        self._for_functions = for_functions
        self._new: dict[int, int] = {}
        self._fresh: list[tuple[GroupAddress, int]] = []
        self._sources: list[GroupAddress] = []
        self._range: GroupRange | None = None
        self._pool: AddressPool | None = None
        if self._mode is not GroupAddressCopy.CREATE_NEW:
            return
        range_id = paster.target_range(options, create=False)
        if range_id is None:
            raise PasteError("target_range")
        self._range = paster.row(GroupRange, range_id)
        self._pool = paster.address_pool(self._range, options)
        distinct = {
            ga_id: paster.row(GroupAddress, ga_id) for ga_id in dict.fromkeys(sources)
        }
        self._sources = sorted(
            (ga for ga in distinct.values() if not ga.central or for_functions),
            key=lambda ga: ga.address,
        )
        if self._pool.room() < len(self._sources) * count:
            raise PasteError("group_addresses")

    def next_copy(self) -> None:
        """Start the next copy: it gets its own new group addresses, made now."""
        if self._pool is None or self._range is None:
            return
        self._new = {}
        fields = (
            ("comment", "description", "datapoint_type")
            if self._for_functions
            else _GROUP_ADDRESS_FIELDS
        )
        for source in self._sources:
            address = self._pool.take(
                place_in_group(
                    source.address,
                    source.group_range.range_start,
                    self._paster.style,
                )
            )
            if address is None:
                continue
            ga_id = self._paster.new_group_address(
                source, self._range, address, fields=fields
            )
            self._new[source.id] = ga_id
            if not self._for_functions:
                self._fresh.append((source, ga_id))

    def target(self, source_ga_id: int) -> int | None:
        """What a copy links instead of the group address ``source_ga_id``; None for nothing."""
        if self._mode is GroupAddressCopy.DONT_COPY:
            return None
        if self._mode is GroupAddressCopy.KEEP:
            return source_ga_id
        return self._new.get(source_ga_id, source_ga_id)

    def secure_new(self) -> None:
        """Give the new group addresses made since the last call their source's Security, now
        that the copies are linked to them."""
        for source, ga_id in self._fresh:
            self._paster.copy_security(source, ga_id)
        self._fresh = []


def _device_order(device: Device) -> tuple[bool, int, int, int, int]:
    """Devices by individual address (those without one last, by id)."""
    if device.address is None or device.segment is None:
        return (True, 0, 0, 0, device.id)
    line = device.segment.line
    return (False, line.area.address, line.address, device.address, device.id)


def _space_devices(space: Space) -> list[Device]:
    """The devices of the building part and of all below it."""
    return list(space.devices) + [
        device for child in space.children for device in _space_devices(child)
    ]


def _space_functions(space: Space) -> list[Function]:
    """The functions of the building part and of all below it."""
    return list(space.functions) + [
        fn for child in space.children for fn in _space_functions(child)
    ]


def _function_addresses(functions: Iterable[Function]) -> list[int]:
    return [ref.group_address_id for fn in functions for ref in fn.group_addresses]


def _trade_links(trade: Trade) -> list[Any]:
    """The device links of the trade and of all trades below it."""
    return list(trade.devices) + [
        link for child in trade.children for link in _trade_links(child)
    ]


def _pair_objects(source: Device, copy: Device) -> list[tuple[ComObject, ComObject]]:
    """The group objects of ``copy`` with the ones of ``source`` they copy: the same object
    (application ref and instance), else the next one of the same application ref."""
    exact: dict[tuple[str, str], ComObject] = {}
    by_ref: dict[str, list[ComObject]] = {}
    for co in sorted(copy.com_objects, key=lambda co: co.id):
        exact.setdefault((co.ref_id, co.instance_ref_id), co)
        by_ref.setdefault(co.ref_id, []).append(co)
    taken: set[int] = set()
    pairs: list[tuple[ComObject, ComObject]] = []
    for co in sorted(source.com_objects, key=lambda co: co.id):
        match = exact.get((co.ref_id, co.instance_ref_id))
        if match is None or match.id in taken:
            match = next(
                (c for c in by_ref.get(co.ref_id, []) if c.id not in taken), None
            )
        if match is None:
            continue
        taken.add(match.id)
        pairs.append((co, match))
    return pairs

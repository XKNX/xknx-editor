"""Flow layout of the link view: sending devices -> group addresses -> receiving devices.

The group addresses form the middle column in address order, under a header per middle group (or
main group). Each device sits at the median height of its group addresses, pushed down where it
would overlap the one above, so that most links run nearly level. A device that sends and receives
shows on both sides. An opened device lists the group objects it links with below its box, under a
heading per channel, and its links run from those. Only a chosen part of the project is laid out (a
room, a main group, one device, one channel of a device or one group address, or the whole
project); devices outside it are marked, and on a group address with many of them they collapse into
one "more" entry until it is expanded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService

# A node: ("s", device id) sending, ("r", device id) receiving, ("g", group address id), and the
# group objects of an opened device: ("so", group object id) sending, ("ro", group object id)
# receiving.
NodeKey = tuple[str, int]
ScopeKind = Literal["all", "room", "main", "device", "channel", "ga"]

ROW = 30.0
# More devices outside the scope than this on one group address collapse into one entry.
COLLAPSE = 6


@dataclass(frozen=True)
class Member:
    """A device on a group address: whether it sends and receives there, and through which of its
    group objects, ``(group object id, sends, receives)``."""

    node_id: int
    sends: bool
    receives: bool
    objects: tuple[tuple[int, bool, bool], ...] = ()


@dataclass(frozen=True)
class Hub:
    """A group address with the devices linked to it."""

    ga_id: int
    address: str
    name: str
    members: tuple[Member, ...]


@dataclass(frozen=True)
class GroupObject:
    """A group object as an opened device lists it: its number, name and channel (``""`` when it
    is in none); ``channel_order`` is the channel's position in the device's parameters."""

    number: int
    name: str
    channel: str = ""
    channel_label: str = ""
    channel_order: int = 0


@dataclass(frozen=True)
class Scope:
    """The part of the project the view lays out; ``key`` is the room label, main group number,
    device node id or group address id. A channel scope is the channel ``channel`` of the device
    ``key``."""

    kind: ScopeKind
    key: str | int | None = None
    channel: str = ""


@dataclass
class FlowNode:
    key: NodeKey
    column: int  # 0 senders, 1 group addresses, 2 receivers
    y: float
    label: str
    main: int = 0
    # A device inside the scope (always true for group addresses).
    inside: bool = True
    # Group address nodes: the address and name, drawn apart.
    address: str = ""
    name: str = ""
    # Device nodes: listing their group objects; the device the scope shows.
    opened: bool = False
    chosen: bool = False
    # Group object nodes: their device and channel.
    owner: int | None = None
    channel: str = ""


@dataclass
class FlowChannel:
    """The heading of a channel among the group objects of an opened device."""

    column: int  # 0 or 2
    y: float
    label: str
    node_id: int
    channel: str
    inside: bool = True


@dataclass
class FlowMore:
    """Devices outside the scope on one group address, collapsed into one entry."""

    ga_id: int
    column: int  # 0 or 2
    y: float
    count: int


@dataclass
class FlowLayout:
    nodes: list[FlowNode] = field(default_factory=list[FlowNode])
    # (source, target, main group): sender -> group address -> receiver
    edges: list[tuple[NodeKey, NodeKey, int]] = field(
        default_factory=list[tuple[NodeKey, NodeKey, int]]
    )
    channels: list[FlowChannel] = field(default_factory=list[FlowChannel])
    more: list[FlowMore] = field(default_factory=list[FlowMore])
    headers: list[tuple[float, str]] = field(default_factory=list[tuple[float, str]])
    height: float = 0.0
    # Each node with the nodes it links to, for highlighting.
    adj: dict[NodeKey, set[NodeKey]] = field(
        default_factory=dict[NodeKey, set[NodeKey]]
    )


def project_hubs(project: ProjectService) -> list[Hub]:
    """One hub per group address of the open project, with the devices linked to it and the group
    objects they link with.

    Direction follows the group object's flags, gated by Communication: a device SENDS on a group
    address when it transmits there (Transmit on its sending link); it RECEIVES when it is
    written to (Write) or takes updates (Update). Answering read requests is not sending. Every
    group address with at least one device is kept; the view decides what to show."""
    part: dict[int, dict[int, dict[int, list[bool]]]] = {}
    for link in project.device_links():
        if not link.communication:
            continue
        sends = bool(link.transmit and link.is_sending)
        receives = bool(link.write or link.update)
        if not (sends or receives):
            continue
        bits = (
            part.setdefault(link.group_address_id, {})
            .setdefault(link.device_id, {})
            .setdefault(link.com_object_id, [False, False])
        )
        bits[0] = bits[0] or sends
        bits[1] = bits[1] or receives
    gas = {ga.id: ga for ga in project.group_addresses}
    hubs: list[Hub] = []
    for ga_id, devs in part.items():
        ga = gas.get(ga_id)
        address = ga.address if ga is not None else str(ga_id)
        name = ga.name if ga is not None else ""
        members = tuple(
            Member(
                nid,
                any(s for s, _r in objects.values()),
                any(r for _s, r in objects.values()),
                tuple((co, s, r) for co, (s, r) in sorted(objects.items())),
            )
            for nid, objects in devs.items()
        )
        hubs.append(Hub(ga_id, address, name, members))
    return hubs


def device_group_objects(
    device: Device, with_channels: bool = True
) -> dict[int, GroupObject]:
    """The group objects of ``device`` by their id, with their channels from its parameters
    (``with_channels``; that needs the device's application). A device not evaluated before is
    released again afterwards."""
    from editor_gui.device import com_object_display_name
    from editor_gui.widgets.parameter_widgets import com_object_channels

    channels: dict[str, tuple[str, str, int]] = {}
    if with_channels:
        evaluated = device.evaluated
        channels = com_object_channels(device.get_ui())
        if not evaluated:
            device.release_dynamic_ui()
    objects: dict[int, GroupObject] = {}
    for co in device.com_objects:
        if co.db_id is None:
            continue
        channel, label, order = channels.get(co.id, ("", "", 0))
        objects[co.db_id] = GroupObject(
            co.number, com_object_display_name(co), channel, label, order
        )
    return objects


def address_key(address: str) -> tuple[int, ...]:
    """Numeric sort key of a group address ("1/2/10" after "1/2/9"); unparseable ones last."""
    try:
        return tuple(int(part) for part in address.split("/"))
    except ValueError:
        return (1 << 30,)


def main_of(address: str) -> int:
    """The main group number of a group address ("1/2/3" -> 1); 0 when unparseable."""
    try:
        return int(address.split("/", 1)[0])
    except ValueError:
        return 0


def _group_of(address: str) -> str:
    """The middle group ("1/2") of a three-level address, the main group ("1") of a two-level one,
    nothing for a free one."""
    parts = address.split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else ""


def _header(group: str, names: Mapping[str, str]) -> str:
    if not group:
        return ""
    main = group.split("/", 1)[0]
    titles = [names.get(main, "")]
    if "/" in group:
        titles.append(names.get(group, ""))
    title = " · ".join(t for t in titles if t)
    return f"{group}/*" + (f"   {title}" if title else "")


def chosen_device(scope: Scope) -> int | None:
    """The device a device or channel scope shows."""
    if scope.kind in ("device", "channel"):
        return int(scope.key or 0)
    return None


def in_scope(
    hubs: Sequence[Hub],
    scope: Scope,
    rooms: Mapping[str, set[int]],
    objects: Mapping[int, Mapping[int, GroupObject]] | None = None,
) -> tuple[list[Hub], set[int] | None]:
    """The group addresses ``scope`` shows, and the devices inside it (``None``: all are). A channel
    scope needs the group objects of its device in ``objects``."""
    core: set[int] | None = None
    if scope.kind == "room":
        core = set(rooms.get(str(scope.key), set()))
    elif scope.kind in ("device", "channel"):
        core = {int(scope.key or 0)}
    # The channel's group objects.
    channel: set[int] = set()
    if scope.kind == "channel" and objects is not None:
        own = objects.get(int(scope.key or 0), {})
        channel = {co for co, o in own.items() if o.channel == scope.channel}
    kept: list[Hub] = []
    for hub in hubs:
        if scope.kind == "ga":
            if hub.ga_id == scope.key:
                kept.append(hub)
            continue
        if len(hub.members) < 2:
            continue  # one device on a group address links nothing
        if scope.kind == "main" and main_of(hub.address) != scope.key:
            continue
        if core is not None and not any(m.node_id in core for m in hub.members):
            continue
        if scope.kind == "channel" and not any(
            m.node_id == scope.key and any(co in channel for co, _s, _r in m.objects)
            for m in hub.members
        ):
            continue
        kept.append(hub)
    return kept, core


def build_flow(
    hubs: Sequence[Hub],
    scope: Scope,
    rooms: Mapping[str, set[int]],
    labels: Mapping[int, str],
    names: Mapping[str, str],
    expanded: set[int] | frozenset[int] = frozenset(),
    objects: Mapping[int, Mapping[int, GroupObject]] | None = None,
) -> FlowLayout:
    """Lay out the group addresses of ``scope`` with their devices. ``labels`` names the devices,
    ``names`` the main ("1") and middle ("1/2") groups; ``expanded`` lists the group addresses whose
    devices outside the scope are shown one by one. ``objects`` has the group objects of the opened
    devices, which list them."""
    opened = objects or {}
    kept, core = in_scope(hubs, scope, rooms, opened)
    kept.sort(key=lambda hub: address_key(hub.address))
    chosen = chosen_device(scope)
    layout = FlowLayout()
    ga_y: dict[int, float] = {}
    # Per side, each device's group addresses and, when opened, its group objects' ones.
    gas_of: dict[str, dict[int, list[int]]] = {"s": {}, "r": {}}
    objects_of: dict[str, dict[int, dict[int, list[int]]]] = {"s": {}, "r": {}}
    y = 0.0
    group: str | None = None
    for hub in kept:
        here = _group_of(hub.address)
        if here != group:
            group = here
            title = _header(here, names)
            if title:
                layout.headers.append((y, title))
                y += ROW
        ga_y[hub.ga_id] = y
        layout.nodes.append(
            FlowNode(
                ("g", hub.ga_id),
                1,
                y,
                f"{hub.address}  {hub.name}".strip(),
                main_of(hub.address),
                address=hub.address,
                name=hub.name,
            )
        )
        outside = [m for m in hub.members if core is not None and m.node_id not in core]
        collapse = len(outside) > COLLAPSE and hub.ga_id not in expanded
        if collapse:
            for column, count in (
                (0, sum(1 for m in outside if m.sends)),
                (2, sum(1 for m in outside if m.receives)),
            ):
                if count:
                    layout.more.append(FlowMore(hub.ga_id, column, y, count))
        for m in hub.members:
            if collapse and core is not None and m.node_id not in core:
                continue
            if m.node_id not in labels:
                continue
            for side, on, index in (("s", m.sends, 1), ("r", m.receives, 2)):
                if not on:
                    continue
                gas_of[side].setdefault(m.node_id, []).append(hub.ga_id)
                if m.node_id in opened:
                    per_object = objects_of[side].setdefault(m.node_id, {})
                    for link in m.objects:
                        if link[index]:
                            per_object.setdefault(link[0], []).append(hub.ga_id)
        y += ROW
    bottom = y
    for side, column in (("s", 0), ("r", 2)):
        rows = {
            nid: _rows(objects_of[side].get(nid, {}), opened.get(nid, {}))
            for nid in gas_of[side]
        }
        tops = _place(gas_of[side], ga_y, {nid: len(r) for nid, r in rows.items()})
        for nid, top in tops.items():
            inside = core is None or nid in core
            layout.nodes.append(
                FlowNode(
                    (side, nid),
                    column,
                    top,
                    labels[nid],
                    inside=inside,
                    opened=nid in opened,
                    chosen=nid == chosen,
                )
            )
            for i, row in enumerate(rows[nid], start=1):
                row_y = top + i * ROW
                if isinstance(row, FlowChannel):
                    row.column, row.y, row.node_id, row.inside = (
                        column,
                        row_y,
                        nid,
                        inside,
                    )
                    layout.channels.append(row)
                else:
                    co, label, channel = row
                    layout.nodes.append(
                        FlowNode(
                            (f"{side}o", co),
                            column,
                            row_y,
                            label,
                            inside=inside,
                            owner=nid,
                            channel=channel,
                        )
                    )
            bottom = max(bottom, top + (1 + len(rows[nid])) * ROW)
    placed = {n.key for n in layout.nodes}
    for hub in kept:
        main = main_of(hub.address)
        target: NodeKey = ("g", hub.ga_id)
        for m in hub.members:
            for side, on, index in (("s", m.sends, 1), ("r", m.receives, 2)):
                if not on or (side, m.node_id) not in placed:
                    continue
                ends = [
                    (f"{side}o", link[0])
                    for link in m.objects
                    if link[index] and (f"{side}o", link[0]) in placed
                ]
                for end in ends or [(side, m.node_id)]:
                    if side == "s":
                        _link(layout, end, target, main)
                    else:
                        _link(layout, target, end, main)
    layout.height = bottom
    return layout


def _rows(
    linked: Mapping[int, list[int]], objects: Mapping[int, GroupObject]
) -> list[FlowChannel | tuple[int, str, str]]:
    """The rows an opened device lists below its box: its linked group objects ``(id, label,
    channel)`` in channel and number order, each channel under its heading."""
    order = sorted(
        linked,
        key=lambda co: (
            (o.channel_order, o.number, co)
            if (o := objects.get(co)) is not None
            else (1 << 30, 0, co)
        ),
    )
    rows: list[FlowChannel | tuple[int, str, str]] = []
    channel: str | None = None
    for co in order:
        o = objects.get(co)
        if o is None:
            rows.append((co, "?", ""))
            continue
        if o.channel and o.channel != channel:
            rows.append(FlowChannel(0, 0.0, o.channel_label, 0, o.channel))
        channel = o.channel
        rows.append((co, f"{o.number}  {o.name}".strip(), o.channel))
    return rows


def _place(
    members: Mapping[int, list[int]],
    ga_y: Mapping[int, float],
    extra: Mapping[int, int],
) -> dict[int, float]:
    """Each device at the median height of its group addresses, pushed down where it would
    overlap the device above; ``extra`` rows below a device list its group objects, which start
    one row higher to sit beside the group addresses."""
    wanted = sorted(
        (
            max(
                0.0,
                sorted(ga_y[g] for g in gas)[len(gas) // 2]
                - (ROW if extra[nid] else 0),
            ),
            nid,
        )
        for nid, gas in members.items()
    )
    placed: dict[int, float] = {}
    free = 0.0
    for want, nid in wanted:
        top = max(want, free)
        placed[nid] = top
        free = top + (1 + extra[nid]) * ROW
    return placed


def _link(layout: FlowLayout, source: NodeKey, target: NodeKey, main: int) -> None:
    layout.edges.append((source, target, main))
    layout.adj.setdefault(source, set()).add(target)
    layout.adj.setdefault(target, set()).add(source)

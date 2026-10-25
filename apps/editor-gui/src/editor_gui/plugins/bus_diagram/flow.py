"""Flow layout of the link view: sending devices -> group addresses -> receiving devices.

The group addresses form the middle column in address order, under a header per middle group (or
main group). Each device sits at the median height of its group addresses, pushed down where it
would overlap the one above, so that most links run nearly level. A device that sends and receives
shows on both sides. Only a chosen part of the project is laid out (a room, a main group, one
device or one group address, or the whole project); devices outside it are marked, and on a group
address with many of them they collapse into one "more" entry until it is expanded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectService

# A group address with its devices: ``(ga_id, address, name, ((node id, sends, receives), ...))``.
Hub = tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]
# A node: ("s", device id) sending, ("r", device id) receiving, ("g", group address id).
NodeKey = tuple[str, int]
ScopeKind = Literal["all", "room", "main", "device", "ga"]

ROW = 30.0
# More devices outside the scope than this on one group address collapse into one entry.
COLLAPSE = 6


@dataclass(frozen=True)
class Scope:
    """The part of the project the view lays out; ``key`` is the room label, main group number,
    device node id or group address id."""

    kind: ScopeKind
    key: str | int | None = None


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
    more: list[FlowMore] = field(default_factory=list[FlowMore])
    headers: list[tuple[float, str]] = field(default_factory=list[tuple[float, str]])
    height: float = 0.0
    # Each node with the nodes it links to, for highlighting.
    adj: dict[NodeKey, set[NodeKey]] = field(
        default_factory=dict[NodeKey, set[NodeKey]]
    )


def project_hubs(project: ProjectService) -> list[Hub]:
    """One hub per group address of the open project: ``(ga_id, address, name, participants)``
    where each participant is ``(device node id, sends, receives)``.

    Direction follows the com-object's flags, gated by Communication: a device SENDS on a group
    address when it transmits there (Transmit on its sending link); it RECEIVES when it is
    written to (Write) or takes updates (Update). Answering read requests is not sending. Every
    group address with at least one participant is kept; the view decides what to show."""
    part: dict[int, dict[int, list[bool]]] = {}
    for link in project.device_links():
        if not link.communication:
            continue
        sends = link.transmit and link.is_sending
        receives = link.write or link.update
        if not (sends or receives):
            continue
        bits = part.setdefault(link.group_address_id, {}).setdefault(
            link.device_id, [False, False]
        )
        bits[0] = bits[0] or sends
        bits[1] = bits[1] or receives
    gas = {ga.id: ga for ga in project.group_addresses}
    hubs: list[Hub] = []
    for ga_id, devs in part.items():
        ga = gas.get(ga_id)
        address = ga.address if ga is not None else str(ga_id)
        name = ga.name if ga is not None else ""
        participants = tuple((nid, bits[0], bits[1]) for nid, bits in devs.items())
        hubs.append((ga_id, address, name, participants))
    return hubs


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


def in_scope(
    hubs: Sequence[Hub], scope: Scope, rooms: Mapping[str, set[int]]
) -> tuple[list[Hub], set[int] | None]:
    """The group addresses ``scope`` shows, and the devices inside it (``None``: all are)."""
    core: set[int] | None = None
    if scope.kind == "room":
        core = set(rooms.get(str(scope.key), set()))
    elif scope.kind == "device":
        core = {int(scope.key or 0)}
    kept: list[Hub] = []
    for hub in hubs:
        ga_id, address, _name, parts = hub
        if scope.kind == "ga":
            if ga_id == scope.key:
                kept.append(hub)
            continue
        if len(parts) < 2:
            continue  # one device on a group address links nothing
        if scope.kind == "main" and main_of(address) != scope.key:
            continue
        if core is not None and not any(nid in core for nid, _s, _r in parts):
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
) -> FlowLayout:
    """Lay out the group addresses of ``scope`` with their devices. ``labels`` names the devices,
    ``names`` the main ("1") and middle ("1/2") groups; ``expanded`` lists the group addresses whose
    devices outside the scope are shown one by one."""
    kept, core = in_scope(hubs, scope, rooms)
    kept.sort(key=lambda hub: address_key(hub[1]))
    layout = FlowLayout()
    ga_y: dict[int, float] = {}
    sends: dict[int, list[int]] = {}
    receives: dict[int, list[int]] = {}
    y = 0.0
    group: str | None = None
    for ga_id, address, name, parts in kept:
        here = _group_of(address)
        if here != group:
            group = here
            title = _header(here, names)
            if title:
                layout.headers.append((y, title))
                y += ROW
        ga_y[ga_id] = y
        main = main_of(address)
        layout.nodes.append(
            FlowNode(
                ("g", ga_id),
                1,
                y,
                f"{address}  {name}".strip(),
                main,
                address=address,
                name=name,
            )
        )
        outside = [p for p in parts if core is not None and p[0] not in core]
        collapse = len(outside) > COLLAPSE and ga_id not in expanded
        if collapse:
            for column, count in (
                (0, sum(1 for p in outside if p[1])),
                (2, sum(1 for p in outside if p[2])),
            ):
                if count:
                    layout.more.append(FlowMore(ga_id, column, y, count))
        for nid, sending, receiving in parts:
            if collapse and core is not None and nid not in core:
                continue
            if nid not in labels:
                continue
            if sending:
                sends.setdefault(nid, []).append(ga_id)
            if receiving:
                receives.setdefault(nid, []).append(ga_id)
        y += ROW
    bottom = y
    placed: dict[NodeKey, float] = {}
    for side, column, members in (("s", 0, sends), ("r", 2, receives)):
        for nid, top in _place(members, ga_y).items():
            key = (side, nid)
            placed[key] = top
            layout.nodes.append(
                FlowNode(
                    key,
                    column,
                    top,
                    labels[nid],
                    inside=core is None or nid in core,
                )
            )
            bottom = max(bottom, top + ROW)
    for ga_id, address, _name, parts in kept:
        main = main_of(address)
        target: NodeKey = ("g", ga_id)
        for nid, sending, receiving in parts:
            if sending and ("s", nid) in placed:
                _link(layout, ("s", nid), target, main)
            if receiving and ("r", nid) in placed:
                _link(layout, target, ("r", nid), main)
    layout.height = bottom
    return layout


def _place(
    members: Mapping[int, list[int]], ga_y: Mapping[int, float]
) -> dict[int, float]:
    """Each device at the median height of its group addresses, pushed down where it would
    overlap the device above."""
    wanted = sorted(
        (sorted(ga_y[g] for g in gas)[len(gas) // 2], nid)
        for nid, gas in members.items()
    )
    placed: dict[int, float] = {}
    previous = -ROW
    for want, nid in wanted:
        top = max(want, previous + ROW)
        placed[nid] = top
        previous = top
    return placed


def _link(layout: FlowLayout, source: NodeKey, target: NodeKey, main: int) -> None:
    layout.edges.append((source, target, main))
    layout.adj.setdefault(source, set()).add(target)
    layout.adj.setdefault(target, set()).add(source)

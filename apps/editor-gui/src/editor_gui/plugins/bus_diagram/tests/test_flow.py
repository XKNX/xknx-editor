"""The link view's flow layout: senders -> group addresses -> receivers, per chosen scope, with the
group objects of opened devices."""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui.device import ComObject, ComObjectFlags
from editor_gui.dpt import DPT_UNKNOWN
from editor_gui.plugins.bus_diagram.flow import (
    COLLAPSE,
    ROW,
    FlowLayout,
    GroupObject,
    Hub,
    Member,
    Scope,
    build_flow,
    chosen_device,
    device_group_objects,
    in_scope,
    project_hubs,
)

LABELS = {n: f"1.1.{n}  Device {n}" for n in range(1, 30)}
NAMES = {"1": "Lighting", "1/2": "Living room", "2": "Central"}


def _hub(ga_id: int, address: str, name: str, *members: tuple[int, bool, bool]) -> Hub:
    """A hub whose devices link with one group object each (numbered after the device)."""
    return Hub(
        ga_id,
        address,
        name,
        tuple(Member(n, s, r, ((100 * n, s, r),)) for n, s, r in members),
    )


def _keys(layout: FlowLayout, column: int) -> list[tuple[str, int]]:
    return [
        n.key for n in sorted(layout.nodes, key=lambda n: n.y) if n.column == column
    ]


def test_senders_left_receivers_right_group_addresses_in_address_order() -> None:
    hubs = [
        _hub(11, "1/2/10", "Light dim", (1, True, False), (2, False, True)),
        _hub(10, "1/2/9", "Light", (1, True, False), (2, False, True), (3, True, True)),
    ]
    layout = build_flow(hubs, Scope("all"), {}, LABELS, NAMES)
    assert _keys(layout, 1) == [("g", 10), ("g", 11)]
    assert set(_keys(layout, 0)) == {("s", 1), ("s", 3)}
    assert set(_keys(layout, 2)) == {("r", 2), ("r", 3)}
    assert layout.headers == [(0.0, "1/2/*   Lighting · Living room")]
    assert (("s", 1), ("g", 10), 1) in layout.edges
    assert (("g", 10), ("r", 3), 1) in layout.edges


def test_a_group_address_with_one_device_links_nothing() -> None:
    hubs = [_hub(1, "1/2/3", "Alone", (1, True, False))]
    assert build_flow(hubs, Scope("all"), {}, LABELS, NAMES).nodes == []
    # Chosen on its own it is shown, with its device.
    layout = build_flow(hubs, Scope("ga", 1), {}, LABELS, NAMES)
    assert {n.key for n in layout.nodes} == {("g", 1), ("s", 1)}


def test_devices_sit_beside_their_group_addresses_without_overlapping() -> None:
    hubs = [
        _hub(n, f"1/2/{n}", "", (n, True, False), (20, False, True))
        for n in range(1, 6)
    ]
    layout = build_flow(hubs, Scope("all"), {}, LABELS, NAMES)
    ga_y = {n.key[1]: n.y for n in layout.nodes if n.column == 1}
    senders = {n.key[1]: n.y for n in layout.nodes if n.column == 0}
    assert senders == ga_y  # each sender level with its only group address
    receiver = next(n for n in layout.nodes if n.column == 2)
    assert receiver.y == sorted(ga_y.values())[2]  # the median of its five
    ys = sorted(senders.values())
    assert all(b - a >= ROW for a, b in itertools.pairwise(ys))


def test_room_scope_marks_and_collapses_devices_outside() -> None:
    many = tuple((n, False, True) for n in range(10, 10 + COLLAPSE + 2))
    hubs = [
        _hub(1, "2/0/1", "Time", (1, True, False), *many),
        _hub(2, "1/2/1", "Light", (1, True, False), (5, False, True)),
        _hub(3, "1/2/2", "Elsewhere", (6, True, False), (7, False, True)),
    ]
    rooms = {"Living": {1}}
    kept, core = in_scope(hubs, Scope("room", "Living"), rooms)
    assert {h.ga_id for h in kept} == {1, 2}
    assert core == {1}
    layout = build_flow(hubs, Scope("room", "Living"), rooms, LABELS, NAMES)
    inside = {n.key: n.inside for n in layout.nodes if n.column != 1}
    assert inside == {("s", 1): True, ("r", 5): False}
    assert [(m.ga_id, m.column, m.count) for m in layout.more] == [(1, 2, COLLAPSE + 2)]
    expanded = build_flow(hubs, Scope("room", "Living"), rooms, LABELS, NAMES, {1})
    assert not expanded.more
    assert sum(1 for n in expanded.nodes if n.column == 2) == COLLAPSE + 3


def test_main_group_and_device_scopes() -> None:
    hubs = [
        _hub(1, "1/2/1", "", (1, True, False), (2, False, True)),
        _hub(2, "2/0/1", "", (3, True, False), (2, False, True)),
    ]
    main = build_flow(hubs, Scope("main", 2), {}, LABELS, NAMES)
    assert {n.key for n in main.nodes} == {("g", 2), ("s", 3), ("r", 2)}
    device = build_flow(hubs, Scope("device", 1), {}, LABELS, NAMES)
    assert {n.key for n in device.nodes} == {("g", 1), ("s", 1), ("r", 2)}
    assert next(n for n in device.nodes if n.key == ("r", 2)).inside is False
    assert chosen_device(Scope("device", 1)) == 1
    assert chosen_device(Scope("main", 2)) is None


def test_the_device_shown_stands_out_on_both_sides() -> None:
    hubs = [
        _hub(1, "1/2/1", "", (1, True, False), (2, False, True)),
        _hub(2, "1/2/2", "", (2, True, False), (3, False, True)),
    ]
    layout = build_flow(hubs, Scope("device", 2), {}, LABELS, NAMES)
    assert {n.key for n in layout.nodes if n.chosen} == {("s", 2), ("r", 2)}


def test_neighbours_for_highlighting() -> None:
    hubs = [_hub(1, "1/2/1", "", (1, True, False), (2, False, True))]
    layout = build_flow(hubs, Scope("all"), {}, LABELS, NAMES)
    assert layout.adj[("g", 1)] == {("s", 1), ("r", 2)}
    assert layout.adj[("s", 1)] == {("g", 1)}


# A switch actuator (device 1) with two channels: its objects 11 (channel A) and 12 (channel B)
# receive, 13 (channel A) sends its status. Device 2 is a push button.
_OBJECTS = {
    11: GroupObject(1, "Switch", "CH-A", "Channel A: Kitchen", 1),
    12: GroupObject(5, "Switch", "CH-B", "Channel B: Hall", 2),
    13: GroupObject(2, "Status", "CH-A", "Channel A: Kitchen", 1),
}
_HUBS = [
    Hub(
        1,
        "1/1/1",
        "Kitchen",
        (
            Member(2, True, False, ((21, True, False),)),
            Member(1, False, True, ((11, False, True),)),
        ),
    ),
    Hub(
        2,
        "1/1/2",
        "Hall",
        (
            Member(2, True, False, ((22, True, False),)),
            Member(1, False, True, ((12, False, True),)),
        ),
    ),
    Hub(
        3,
        "1/1/3",
        "Kitchen status",
        (
            Member(1, True, False, ((13, True, False),)),
            Member(2, False, True, ((23, False, True),)),
        ),
    ),
]


def test_an_opened_device_lists_its_group_objects_under_their_channels() -> None:
    layout = build_flow(_HUBS, Scope("all"), {}, LABELS, NAMES, objects={1: _OBJECTS})
    receiving = sorted(
        (n for n in layout.nodes if n.column == 2 and n.owner == 1), key=lambda n: n.y
    )
    assert [(n.key, n.label, n.channel) for n in receiving] == [
        (("ro", 11), "1  Switch", "CH-A"),
        (("ro", 12), "5  Switch", "CH-B"),
    ]
    device = next(n for n in layout.nodes if n.key == ("r", 1))
    assert device.opened
    headings = sorted((c for c in layout.channels if c.column == 2), key=lambda c: c.y)
    assert [(c.label, c.node_id, c.channel) for c in headings] == [
        ("Channel A: Kitchen", 1, "CH-A"),
        ("Channel B: Hall", 1, "CH-B"),
    ]
    # Device box, heading, object, heading, object: one row each.
    assert [headings[0].y, receiving[0].y, headings[1].y, receiving[1].y] == [
        device.y + ROW * i for i in range(1, 5)
    ]
    # The links run from and to the group objects, not the device box.
    assert (("g", 1), ("ro", 11), 1) in layout.edges
    assert (("so", 13), ("g", 3), 1) in layout.edges
    assert not any(("r", 1) in e[:2] for e in layout.edges)
    # The push button stays closed and links with its box.
    assert (("s", 2), ("g", 1), 1) in layout.edges
    assert layout.height >= device.y + 5 * ROW


def test_opened_devices_do_not_overlap_the_next_device() -> None:
    layout = build_flow(
        _HUBS, Scope("all"), {}, LABELS, NAMES, objects={1: _OBJECTS, 2: {}}
    )
    for column in (0, 2):
        rows = sorted(
            [n.y for n in layout.nodes if n.column == column]
            + [c.y for c in layout.channels if c.column == column]
        )
        assert all(b - a >= ROW for a, b in itertools.pairwise(rows))
    # A group object without details (not among the device's) is still listed.
    unknown = next(n for n in layout.nodes if n.key == ("so", 21))
    assert (unknown.label, unknown.owner) == ("?", 2)


def test_a_channel_shows_the_group_addresses_of_its_group_objects() -> None:
    scope = Scope("channel", 1, "CH-A")
    kept, core = in_scope(_HUBS, scope, {}, {1: _OBJECTS})
    assert [h.ga_id for h in kept] == [1, 3]
    assert core == {1}
    layout = build_flow(_HUBS, scope, {}, LABELS, NAMES, objects={1: _OBJECTS})
    assert {n.key for n in layout.nodes if n.chosen} == {("s", 1), ("r", 1)}
    assert ("g", 2) not in {n.key for n in layout.nodes}
    # Without the device's group objects nothing belongs to the channel.
    assert in_scope(_HUBS, scope, {})[0] == []


def test_the_links_of_a_project_with_their_group_objects() -> None:
    base = {
        "communication": True,
        "transmit": True,
        "is_sending": True,
        "write": False,
        "update": False,
        "group_address_id": 7,
        "device_id": 1,
        "com_object_id": 11,
    }
    variants: list[dict[str, Any]] = [
        {},
        {"com_object_id": 12, "is_sending": False, "write": True},
        {"device_id": 2, "com_object_id": 21},
        # Communication off, or only answering reads: no part in the diagram.
        {"device_id": 3, "communication": False},
        {"device_id": 4, "transmit": False},
        {"group_address_id": 8},
    ]
    links = [SimpleNamespace(**{**base, **v}) for v in variants]
    project: Any = SimpleNamespace(
        device_links=lambda: links,
        group_addresses=[SimpleNamespace(id=7, address="1/1/7", name="Light")],
    )
    hubs = {h.ga_id: h for h in project_hubs(project)}
    assert hubs[7].address == "1/1/7"
    assert hubs[7].members == (
        Member(1, True, True, ((11, True, False), (12, False, True))),
        Member(2, True, False, ((21, True, False),)),
    )
    # A group address the project no longer has is named by its id.
    assert (hubs[8].address, hubs[8].name) == ("8", "")


def _com_object(db_id: int | None, ref_id: str, number: int, name: str) -> ComObject:
    return ComObject(
        id=ref_id,
        name=name,
        dpt=DPT_UNKNOWN,
        flags=ComObjectFlags(),
        function_text="On/Off",
        number=number,
        db_id=db_id,
    )


def test_the_group_objects_of_a_device_with_their_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[bool] = []
    device: Any = SimpleNamespace(
        com_objects=[
            _com_object(11, "O-1", 1, "Switch"),
            _com_object(12, "O-2", 2, "Status"),
            _com_object(None, "O-3", 3, "Not in the project"),
        ],
        evaluated=False,
        get_ui=lambda: [],
        release_dynamic_ui=lambda: released.append(True),
    )
    monkeypatch.setattr(
        "editor_gui.widgets.parameter_widgets.com_object_channels",
        lambda _nodes: {"O-1": ("CH-1", "Channel A", 1)},
    )
    assert device_group_objects(device) == {
        11: GroupObject(1, "Switch  ·  On/Off", "CH-1", "Channel A", 1),
        12: GroupObject(2, "Status  ·  On/Off"),
    }
    # Not evaluated before: released again.
    assert released == [True]
    # Without the application: no channels, and nothing evaluated.
    assert device_group_objects(device, with_channels=False)[11].channel == ""
    assert released == [True]

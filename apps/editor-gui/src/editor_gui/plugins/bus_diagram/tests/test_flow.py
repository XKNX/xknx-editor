"""The link view's flow layout: senders -> group addresses -> receivers, per chosen scope."""

from __future__ import annotations

import itertools

from editor_gui.plugins.bus_diagram.flow import (
    COLLAPSE,
    ROW,
    Hub,
    Scope,
    build_flow,
    in_scope,
)

LABELS = {n: f"1.1.{n}  Device {n}" for n in range(1, 30)}
NAMES = {"1": "Lighting", "1/2": "Living room", "2": "Central"}


def _keys(layout, column: int) -> list[tuple[str, int]]:
    return [
        n.key for n in sorted(layout.nodes, key=lambda n: n.y) if n.column == column
    ]


def test_senders_left_receivers_right_group_addresses_in_address_order() -> None:
    hubs: list[Hub] = [
        (11, "1/2/10", "Light dim", ((1, True, False), (2, False, True))),
        (10, "1/2/9", "Light", ((1, True, False), (2, False, True), (3, True, True))),
    ]
    layout = build_flow(hubs, Scope("all"), {}, LABELS, NAMES)
    assert _keys(layout, 1) == [("g", 10), ("g", 11)]
    assert set(_keys(layout, 0)) == {("s", 1), ("s", 3)}
    assert set(_keys(layout, 2)) == {("r", 2), ("r", 3)}
    assert layout.headers == [(0.0, "1/2/*   Lighting · Living room")]
    assert (("s", 1), ("g", 10), 1) in layout.edges
    assert (("g", 10), ("r", 3), 1) in layout.edges


def test_a_group_address_with_one_device_links_nothing() -> None:
    hubs: list[Hub] = [(1, "1/2/3", "Alone", ((1, True, False),))]
    assert build_flow(hubs, Scope("all"), {}, LABELS, NAMES).nodes == []
    # Chosen on its own it is shown, with its device.
    layout = build_flow(hubs, Scope("ga", 1), {}, LABELS, NAMES)
    assert {n.key for n in layout.nodes} == {("g", 1), ("s", 1)}


def test_devices_sit_beside_their_group_addresses_without_overlapping() -> None:
    hubs: list[Hub] = [
        (n, f"1/2/{n}", "", ((n, True, False), (20, False, True))) for n in range(1, 6)
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
    hubs: list[Hub] = [
        (1, "2/0/1", "Time", ((1, True, False), *many)),
        (2, "1/2/1", "Light", ((1, True, False), (5, False, True))),
        (3, "1/2/2", "Elsewhere", ((6, True, False), (7, False, True))),
    ]
    rooms = {"Living": {1}}
    kept, core = in_scope(hubs, Scope("room", "Living"), rooms)
    assert {h[0] for h in kept} == {1, 2}
    assert core == {1}
    layout = build_flow(hubs, Scope("room", "Living"), rooms, LABELS, NAMES)
    inside = {n.key: n.inside for n in layout.nodes if n.column != 1}
    assert inside == {("s", 1): True, ("r", 5): False}
    assert [(m.ga_id, m.column, m.count) for m in layout.more] == [(1, 2, COLLAPSE + 2)]
    expanded = build_flow(hubs, Scope("room", "Living"), rooms, LABELS, NAMES, {1})
    assert not expanded.more
    assert sum(1 for n in expanded.nodes if n.column == 2) == COLLAPSE + 3


def test_main_group_and_device_scopes() -> None:
    hubs: list[Hub] = [
        (1, "1/2/1", "", ((1, True, False), (2, False, True))),
        (2, "2/0/1", "", ((3, True, False), (2, False, True))),
    ]
    main = build_flow(hubs, Scope("main", 2), {}, LABELS, NAMES)
    assert {n.key for n in main.nodes} == {("g", 2), ("s", 3), ("r", 2)}
    device = build_flow(hubs, Scope("device", 1), {}, LABELS, NAMES)
    assert {n.key for n in device.nodes} == {("g", 1), ("s", 1), ("r", 2)}
    assert next(n for n in device.nodes if n.key == ("r", 2)).inside is False


def test_neighbours_for_highlighting() -> None:
    hubs: list[Hub] = [(1, "1/2/1", "", ((1, True, False), (2, False, True)))]
    layout = build_flow(hubs, Scope("all"), {}, LABELS, NAMES)
    assert layout.adj[("g", 1)] == {("s", 1), ("r", 2)}
    assert layout.adj[("s", 1)] == {("g", 1)}

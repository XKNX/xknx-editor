"""Drag and drop in the Buildings and Group Addresses trees: an element dropped where its "Move to"
would put it moves there; a place it may not go does not take it."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.ui import group_addresses, spaces
from editor_gui.plugins.project.ui.group_addresses import GroupAddressesPanel
from editor_gui.plugins.project.ui.spaces import SpacesPanel
from editor_gui.plugins.project.ui.tree_drag import Dragged

from ._clicks import Clicks
from ._drops import drop_on
from .test_buildings_actions import _device
from .test_buildings_view import _panel as _buildings


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    yield proj
    proj.close()


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frames(panel: SpacesPanel | GroupAddressesPanel, count: int = 1) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Tree")
        panel.render()
        imgui.end()
        imgui.end_frame()


def _parent(project: ProjectService, space_id: int) -> int | None:
    def walk(nodes: list[object], parent: int | None) -> int | bool | None:
        for node in nodes:
            if node.id == space_id:  # type: ignore[attr-defined]
                return parent
            found = walk(node.children, node.id)  # type: ignore[attr-defined]
            if found is not False:
                return found
        return False

    found = walk(list(project.get_space_tree()), None)
    assert found is not False
    return found  # type: ignore[return-value]


@pytest.mark.usefixtures("context")
def test_spaces_devices_and_trades_are_dropped_where_they_may_go(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    building = project.get_space_tree()[0].id
    ground = project.create_space(building, "Floor", "Ground")
    upper = project.create_space(building, "Floor", "Upper")
    assert ground is not None and upper is not None
    room = project.create_space(ground, "Room", "Kitchen")
    assert room is not None
    notes: list[str] = []
    panel = _buildings(project, notes)
    _frames(panel, 2)

    # The room onto the upper floor.
    offered = drop_on(monkeypatch, spaces, f"##sp{upper}", Dragged("space", (room,)))
    _frames(panel)
    assert offered == [True] and _parent(project, room) == upper
    monkeypatch.undo()
    # A room does not go to the top level, nor onto the floor it is on already.
    for row in ("##spaces_root", f"##sp{upper}"):
        offered = drop_on(monkeypatch, spaces, row, Dragged("space", (room,)))
        _frames(panel)
        assert offered == [False]
        monkeypatch.undo()
    assert _parent(project, room) == upper

    # A device into the room, and not onto a floor (it holds no devices).
    device = _device(project, 7)
    offered = drop_on(
        monkeypatch, spaces, f"##sp{ground}", Dragged("device", (device,))
    )
    _frames(panel)
    assert offered == [False]
    monkeypatch.undo()
    offered = drop_on(monkeypatch, spaces, f"##sp{room}", Dragged("device", (device,)))
    _frames(panel)
    assert offered == [True]
    assert [d.id for d in project.find_space(room).devices] == [device]  # type: ignore[union-attr]
    monkeypatch.undo()

    # Trades: one below the other, back to the top level, and a device from one into the other.
    light = project.create_trade(None, "Lighting")
    heat = project.create_trade(None, "Heating")
    assert light is not None and heat is not None
    project.add_trade_device(light, device)
    _frames(panel, 2)
    offered = drop_on(monkeypatch, spaces, f"##trade{light}", Dragged("trade", (heat,)))
    _frames(panel)
    assert offered == [True]
    assert [c.id for c in project.get_trade_tree()[0].children] == [heat]
    monkeypatch.undo()
    offered = drop_on(monkeypatch, spaces, "##trades_root", Dragged("trade", (heat,)))
    _frames(panel)
    assert [t.id for t in project.get_trade_tree()] == [light, heat]
    monkeypatch.undo()
    offered = drop_on(
        monkeypatch, spaces, f"##trade{heat}", Dragged("device", (device,))
    )
    _frames(panel)
    assert offered == [True]
    trades = {
        t.id: [link.device.id for link in t.devices] for t in project.get_trade_tree()
    }
    assert trades == {light: [], heat: [device]}
    assert notes == []


@pytest.mark.usefixtures("context")
def test_group_addresses_and_middle_groups_are_dropped_on_groups(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    light = project.create_group_address("1/1/5", "Light")
    project.create_group_address("2/1/5", "Other")
    assert light is not None
    notes: list[str] = []
    panel = GroupAddressesPanel(
        get_range_tree=project.get_group_range_tree,
        get_assignments_for_ga=project.get_assignments_for_ga,
        get_devices=lambda: [],
        on_create_ga=lambda _a, _n: None,
        on_rename_ga=lambda _i, _n: None,
        on_remove_ga=lambda _i: None,
        on_create_range=project.create_group_range,
        on_rename_range=project.rename_group_range,
        on_remove_range=project.remove_group_range,
        group_style=lambda: project.group_address_style,
        on_move_ga=project.move_group_address,
        on_move_range=project.move_group_range,
        notify=notes.append,
    )
    clicks = Clicks(monkeypatch, group_addresses)
    tree = project.get_group_range_tree()
    main_1, main_2 = tree[0], tree[1]
    middle_1, middle_2 = main_1.children[0], main_2.children[0]
    for node in (main_1, middle_1, main_2, middle_2):
        clicks.open.add(f"{node.address}  {node.name or f'[{node.id}]'}##gr{node.id}")
    _frames(panel, 2)
    # Not into its own group, nor into a main group (the three-level style).
    for row in (f"##gr{middle_1.id}", f"##gr{main_2.id}"):
        offered = drop_on(
            monkeypatch, group_addresses, row, Dragged("group_address", (light,))
        )
        _frames(panel)
        assert offered == [False]
        monkeypatch.undo()
    offered = drop_on(
        monkeypatch,
        group_addresses,
        f"##gr{middle_2.id}",
        Dragged("group_address", (light,)),
    )
    _frames(panel)
    assert offered == [True]
    moved = project.element_details("group_address", light)
    assert moved is not None and moved.address == "2/1/0"
    monkeypatch.undo()
    # The middle group 1/1 below main group 2, not below the main group it is in.
    offered = drop_on(
        monkeypatch,
        group_addresses,
        f"##gr{main_1.id}",
        Dragged("group_range", (middle_1.id,)),
    )
    _frames(panel)
    assert offered == [False]
    monkeypatch.undo()
    offered = drop_on(
        monkeypatch,
        group_addresses,
        f"##gr{main_2.id}",
        Dragged("group_range", (middle_1.id,)),
    )
    _frames(panel)
    assert [c.address for c in project.get_group_range_tree()[-1].children] == [
        "2/0",
        "2/1",
    ]
    assert notes == []


@pytest.mark.usefixtures("context")
def test_lines_and_segments_move_by_menu_and_by_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui.plugins.project.strings import S
    from editor_gui.plugins.project.ui import topology as topology_ui
    from editor_gui.plugins.project.ui.topology import (
        Area,
        Line,
        Segment,
        TopologyPanel,
    )

    lines = {
        1: [
            Line(10, 1, 0, "", main_segment_id=100),
            Line(11, 1, 1, "Office", [Segment(5, "Garden")], main_segment_id=110),
        ],
        2: [
            Line(20, 2, 0, "", main_segment_id=200),
            Line(21, 2, 1, "Hall", main_segment_id=210),
        ],
    }
    moved_lines: list[tuple[int, int]] = []
    moved_segments: list[tuple[int, int]] = []
    panel = TopologyPanel(
        get_devices=list,
        get_areas=lambda: [Area(1, 1, "A"), Area(2, 2, "B")],
        get_lines=lambda area_id: lines[area_id],
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _s: None,
        on_remove_area=lambda _a: None,
        on_rename_area=lambda _a, _n: None,
        on_create_line=lambda _a, _n, _s: None,
        on_remove_line=lambda _l: None,
        on_rename_line=lambda _l, _n: None,
        on_move_line=lambda line, area: moved_lines.append((line, area)),
        on_move_segment=lambda segment, line: moved_segments.append((segment, line)),
    )

    def frames(count: int = 2) -> None:
        for _ in range(count):
            imgui.new_frame()
            imgui.begin("Topology")
            panel.render()
            imgui.end()
            imgui.end_frame()

    clicks = Clicks(monkeypatch, topology_ui)
    frames()
    # "Move to" of the line 1.1: area 2 (its own, area 1, grayed out).
    clicks.context = "##line_ctx_11"
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add("2.0 B##mvline11_2")
    frames()
    assert moved_lines == [(11, 2)]
    # "Move to" of the segment: line 2.1.
    clicks.context = "##segment_ctx_5"
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add("2.1 Hall##mvseg5_21")
    frames()
    assert moved_segments == [(5, 21)]
    monkeypatch.undo()
    # Dropped: the line on area 2's node, the segment on line 2.1; not where they are.
    for row, dragged, taken in (
        ("##area_2", Dragged("line", (11,)), True),
        ("##area_1", Dragged("line", (11,)), False),
        ("##line_21", Dragged("line", (11,)), False),
        ("##line_21", Dragged("segment", (5,)), True),
        ("##line_11", Dragged("segment", (5,)), False),
    ):
        offered = drop_on(monkeypatch, topology_ui, row, dragged)
        frames(1)
        assert offered == [taken], row
        monkeypatch.undo()
    assert moved_lines == [(11, 2), (11, 2)]
    assert moved_segments == [(5, 21), (5, 21)]


@pytest.mark.usefixtures("context")
def test_a_group_address_is_dragged_with_the_mouse(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole way, with imgui's own drag and drop: pressed on the group address, moved onto
    another middle group, let go there."""
    from editor_gui.plugins.project.ui import tree_drag

    light = project.create_group_address("1/1/5", "Light")
    project.create_group_address("2/1/5", "Other")
    assert light is not None
    panel = GroupAddressesPanel(
        get_range_tree=project.get_group_range_tree,
        get_assignments_for_ga=project.get_assignments_for_ga,
        get_devices=lambda: [],
        on_create_ga=lambda _a, _n: None,
        on_rename_ga=lambda _i, _n: None,
        on_remove_ga=lambda _i: None,
        on_create_range=project.create_group_range,
        on_rename_range=project.rename_group_range,
        on_remove_range=project.remove_group_range,
        group_style=lambda: project.group_address_style,
        on_move_ga=project.move_group_address,
        on_move_range=project.move_group_range,
        notify=lambda _t: None,
    )
    clicks = Clicks(monkeypatch, group_addresses)
    tree = project.get_group_range_tree()
    for node in (*tree, *(m for main in tree for m in main.children)):
        clicks.open.add(f"{node.address}  {node.name or f'[{node.id}]'}##gr{node.id}")
    middle_2 = tree[1].children[0]
    # Where the frame drew the group address and the middle group 2/1.
    rows: dict[str, imgui.ImVec2] = {}
    real_source = tree_drag.drag_source

    def drag_source(dragged: Dragged, label: str) -> None:
        rows.setdefault(f"source{dragged.ids[0]}", imgui.get_item_rect_min())
        real_source(dragged, label)

    monkeypatch.setattr(group_addresses, "drag_source", drag_source)
    target_rows: dict[int, imgui.ImVec2] = {}
    real_tree_node = group_addresses.tree_node

    def tree_node(label: str, *args: object) -> bool:
        opened = real_tree_node(label, *args)
        if label.endswith(f"##gr{middle_2.id}"):
            target_rows[middle_2.id] = imgui.get_item_rect_min()
        return opened

    monkeypatch.setattr(group_addresses, "tree_node", tree_node)
    io = imgui.get_io()
    io.delta_time = 1 / 60
    _frames(panel, 3)
    source = rows[f"source{light}"]
    target = target_rows[middle_2.id]
    io.add_mouse_pos_event(source.x + 30, source.y + 5)
    _frames(panel)
    io.add_mouse_button_event(0, True)
    _frames(panel)
    for step in range(1, 6):
        io.add_mouse_pos_event(
            source.x + 30, source.y + 5 + (target.y - source.y) * step / 5
        )
        _frames(panel)
    io.add_mouse_pos_event(target.x + 40, target.y + 5)
    _frames(panel, 2)
    io.add_mouse_button_event(0, False)
    _frames(panel, 2)
    moved = project.element_details("group_address", light)
    assert moved is not None and moved.address == "2/1/0"

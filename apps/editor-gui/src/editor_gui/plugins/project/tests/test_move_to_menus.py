""" "Move to" in the Group Addresses and Topology trees: a group address into another group, a
middle group below another main group, a device onto another line or segment; where it is
already is grayed out."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import group_addresses
from editor_gui.plugins.project.ui import topology as topology_ui
from editor_gui.plugins.project.ui.group_addresses import GroupAddressesPanel
from editor_gui.plugins.project.ui.topology import Area, Line, Segment, TopologyPanel

from ._clicks import Clicks


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


def _frames(render: object, count: int = 3) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Tree")
        render.render()  # type: ignore[attr-defined]
        imgui.end()
        imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_group_addresses_and_middle_groups_move(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, group_addresses)
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
    tree = project.get_group_range_tree()
    main_1, main_2 = tree[0], tree[1]
    middle_1, middle_2 = main_1.children[0], main_2.children[0]
    for node in (main_1, middle_1, main_2, middle_2):
        clicks.open.add(f"{node.address}  {node.name or f'[{node.id}]'}##gr{node.id}")
    _frames(panel)
    # The group address into middle group 2/1: 2/1/5 is taken, so 2/1/0.
    clicks.context = f"##ga_ctx_{light}"
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add(
        f"{middle_2.address}  {middle_2.name or f'[{middle_2.id}]'}##mvgr{middle_2.id}"
    )
    _frames(panel)
    moved = project.element_details("group_address", light)
    assert moved is not None and moved.address == "2/1/0"
    # The (now empty) middle group 1/1 below main group 2: its number 1 is taken there.
    clicks.context = f"##gr_ctx_{middle_1.id}"
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add(
        f"{main_2.address}  {main_2.name or f'[{main_2.id}]'}##mvgr{main_2.id}"
    )
    _frames(panel)
    assert [c.address for c in project.get_group_range_tree()[-1].children] == [
        "2/0",
        "2/1",
    ]
    assert notes == []
    assert project.history()[0].display_text == S.HISTORY_EV_MOVE_GROUP_RANGE


@pytest.mark.usefixtures("context")
def test_a_device_moves_to_another_line_or_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks = Clicks(monkeypatch, topology_ui)
    main = Line(3, 1, 0, "", main_segment_id=30)
    line = Line(1, 1, 1, "Office", [Segment(5, "Garden")], main_segment_id=10)
    moves: list[int] = []
    shown: list[tuple[str, bool]] = []
    real_item = imgui.menu_item

    def menu_item(label: str, *args: object) -> object:
        if "##mvdev" in label:
            shown.append((label.split("##")[0], bool(args[2])))
        return real_item(label, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(imgui, "menu_item", menu_item)
    from editor_gui.device import UnloadedDevice

    device = UnloadedDevice(7, "", "Product", "1.1.5", None)
    panel = TopologyPanel(
        get_devices=list,
        get_areas=lambda: [Area(1, 1, "Hauptlinie")],
        get_lines=lambda _area_id: [line, main],
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _s, _m: None,
        on_remove_area=lambda _a: None,
        on_rename_area=lambda _a, _n: None,
        on_create_line=lambda _a, _n, _s, _m: None,
        on_remove_line=lambda _l: None,
        on_rename_line=lambda _l, _n: None,
        on_move_device_to_segment=lambda _d, s: moves.append(s.id),
        get_unloaded_devices=lambda: [device],
        get_device_segments=lambda: {7: 10},
    )
    clicks.open.add("1.0 Hauptlinie##area_1")
    _frames(panel)
    clicks.context = "##dev_ctx_7"
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add("  1.1 Garden##mvdev5")
    _frames(panel, 1)
    assert moves == [5]
    # The main line first, then the line it is on (grayed out) and its segment.
    assert shown[:3] == [
        ("1.0 Hauptlinie", True),
        ("1.1 Office", False),
        ("  1.1 Garden", True),
    ]


@pytest.mark.usefixtures("context")
def test_a_device_moves_to_another_room_or_trade(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.project.ui import spaces

    from .test_buildings_actions import _device
    from .test_buildings_view import _panel

    building = project.get_space_tree()[0]
    floor = project.create_space(building.id, "Floor", "Ground")
    assert floor is not None
    kitchen = project.create_space(floor, "Room", "Kitchen")
    hall = project.create_space(floor, "Room", "Hall")
    assert kitchen is not None and hall is not None
    device = _device(project, 7)
    project.set_device_space(device, kitchen)
    panel = _panel(project, [])
    clicks = Clicks(monkeypatch, spaces)
    clicks.open.update(
        {
            f"{building.name}##sp{building.id}",
            f"Ground##sp{floor}",
            f"Kitchen##sp{kitchen}",
        }
    )
    _frames(panel)
    # The floor holds no devices: grayed out, nothing moves.
    clicks.context = f"##spdev_ctx_{device}"
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add(f"  Ground##mvdev{floor}")
    _frames(panel)
    assert [d.id for d in project.find_space(kitchen).devices] == [device]  # type: ignore[union-attr]
    clicks.items.clear()
    clicks.context = f"##spdev_ctx_{device}"
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add(f"    Hall##mvdev{hall}")
    _frames(panel)
    assert [d.id for d in project.find_space(hall).devices] == [device]  # type: ignore[union-attr]

    # From one trade into another.
    light = project.create_trade(None, "Lighting")
    heat = project.create_trade(None, "Heating")
    assert light is not None and heat is not None
    project.add_trade_device(light, device)
    clicks.open.add(f"Lighting##trade{light}")
    _frames(panel)
    clicks.context = f"##spdev_ctx_t{light}{device}"
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add(f"Heating##mvdtr{heat}")
    _frames(panel)
    trades = {
        t.id: [link.device.id for link in t.devices] for t in project.get_trade_tree()
    }
    assert trades == {light: [], heat: [device]}

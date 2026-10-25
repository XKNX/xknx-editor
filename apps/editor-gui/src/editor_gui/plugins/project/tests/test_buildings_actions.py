"""What the menus and dialogs of the Buildings view do: adding building parts and trades with the
Add dialog, moving and deleting them, putting devices into spaces and trades and taking them out."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.space_text import add_item_text
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import spaces
from editor_gui.plugins.project.ui.spaces import SpacesPanel

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


@pytest.fixture
def clicks(monkeypatch: pytest.MonkeyPatch, context: None) -> Clicks:
    return Clicks(monkeypatch, spaces)


def _panel(project: ProjectService, notes: list[str]) -> SpacesPanel:
    return SpacesPanel(
        get_space_tree=project.get_space_tree,
        on_select_device_id=lambda _n: None,
        on_create_function=project.create_function,
        on_create_space=project.create_space,
        get_selected_space_id=lambda: project.selected_space_id,
        on_move_space=project.move_space,
        on_remove_space=project.remove_space,
        on_set_device_space=project.set_device_space,
        get_unassigned_devices=project.get_unassigned_devices,
        get_trade_tree=project.get_trade_tree,
        on_create_trade=project.create_trade,
        on_move_trade=project.move_trade,
        on_remove_trade=project.remove_trade,
        on_add_trade_device=project.add_trade_device,
        on_remove_trade_device=project.remove_trade_device,
        get_space_devices=project.get_space_devices,
        notify=notes.append,
    )


def _frames(panel: SpacesPanel, count: int = 3) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Buildings")
        panel.render()
        imgui.end()
        imgui.end_frame()


def _device(project: ProjectService, address: int) -> int:
    svc, pid = project._svc, project._pid  # pyright: ignore[reportPrivateUsage]
    assert pid is not None
    segment = next(
        s.id
        for a in svc.topology(pid, 0).areas
        if a.address == 1
        for ln in a.lines
        if ln.address == 1
        for s in ln.segments
    )
    device = svc.add_device(
        pid, segment, "M-1_H-1_P-1", address=address, name=f"D{address}"
    )
    project._bump()  # pyright: ignore[reportPrivateUsage]
    return device


def _names(project: ProjectService) -> list[object]:
    def walk(items: list[object]) -> list[object]:
        return [
            (s.name, walk(s.children), [d.name for d in s.devices])  # type: ignore[attr-defined]
            for s in items
        ]

    return walk(list(project.get_space_tree()))


def _choose(clicks: Clicks, panel: SpacesPanel, context: str, *path: str) -> None:
    """Open the context menu ``context`` and choose ``path``: its submenus, then the item."""
    clicks.context = context
    clicks.menus.update(path[:-1])
    clicks.items.add(path[-1])
    _frames(panel)


def test_the_add_dialog_adds_building_parts(
    project: ProjectService, clicks: Clicks
) -> None:
    notes: list[str] = []
    panel = _panel(project, notes)
    _frames(panel)
    # Buildings: two, with the default name.
    _choose(clicks, panel, "##spaces_root_ctx", S.SPACES_ADD_BUILDINGS)
    panel._add_count = 2  # pyright: ignore[reportPrivateUsage]
    clicks.button = S.SPACES_SPACE_CREATE
    _frames(panel)
    assert [s.name for s in project.get_space_tree()][1:] == ["New building"] * 2
    building = project.get_space_tree()[0].id
    # A room with its use, named in the dialog.
    _choose(
        clicks, panel, f"##spctx{building}", S.SPACES_ADD_MENU, add_item_text("Room")
    )
    panel._add_name = "Kitchen"  # pyright: ignore[reportPrivateUsage]
    panel._add_usage = 1  # pyright: ignore[reportPrivateUsage]
    clicks.button = S.SPACES_SPACE_CREATE
    _frames(panel)
    room = next(s for s in project.get_space_tree()[0].children if s.name == "Kitchen")
    assert room.space_type == "Room" and room.usage
    # Cancel adds nothing.
    _choose(
        clicks,
        panel,
        f"##spctx{room.id}",
        S.SPACES_ADD_MENU,
        add_item_text("DistributionBoard"),
    )
    clicks.button = S.ML_CANCEL
    _frames(panel)
    assert (
        next(
            s for s in project.get_space_tree()[0].children if s.id == room.id
        ).children
        == []
    )
    assert notes == []


def test_building_parts_move_and_are_deleted(
    project: ProjectService, clicks: Clicks
) -> None:
    notes: list[str] = []
    building = project.get_space_tree()[0].id
    floor = project.create_space(building, "Floor", "EG")
    room = project.create_space(building, "Room", "Kitchen")
    other = project.create_space(None, "Building", "Garage")
    assert floor is not None and room is not None and other is not None
    panel = _panel(project, notes)
    _frames(panel)
    # The room goes onto the floor …
    _choose(
        clicks, panel, f"##spctx{room}", S.SPACES_MOVE_TO, f"  EG##mv{room}_{floor}"
    )
    assert _names(project)[0] == ("p", [("EG", [("Kitchen", [], [])], [])], [])
    # … and the garage, a building, may go to the top level (it is there already: no change).
    _choose(clicks, panel, f"##spctx{other}", S.SPACES_MOVE_TO, S.SPACES_MOVE_ROOT)
    assert [s.name for s in project.get_space_tree()] == ["p", "Garage"]
    # Deleting asks first.
    _choose(clicks, panel, f"##spctx{other}", S.SPACES_DELETE)
    clicks.button = S.SPACES_DELETE
    _frames(panel)
    assert [s.name for s in project.get_space_tree()] == ["p"]
    _choose(clicks, panel, f"##spctx{floor}", S.SPACES_DELETE)
    clicks.button = S.ML_CANCEL
    _frames(panel)
    assert _names(project)[0] == ("p", [("EG", [("Kitchen", [], [])], [])], [])


def test_devices_go_into_spaces_and_out_again(
    project: ProjectService, clicks: Clicks
) -> None:
    notes: list[str] = []
    building = project.get_space_tree()[0].id
    room = project.create_space(building, "Room", "Kitchen")
    assert room is not None
    first, second = _device(project, 5), _device(project, 6)
    panel = _panel(project, notes)
    _frames(panel)
    # "Add > Devices…" of the room lists the devices without a space.
    _choose(clicks, panel, f"##spctx{room}", S.SPACES_ADD_MENU, S.SPACES_ADD_DEVICES)
    clicks.items.add(f"1.1.5  D5##assignpick{room}_{first}")
    _frames(panel)
    assert _names(project)[0] == ("p", [("Kitchen", [], ["D5"])], [])
    # A device without a space goes to one from the "Without Space" section.
    clicks.open.add(f"{S.SPACES_UNASSIGNED_TITLE} (1)##unassigned")
    clicks.items.add(f"{S.SPACES_ASSIGN_DEVICE}##uassign{second}")
    _frames(panel, 2)
    clicks.items.add(f"  Kitchen##uto{second}_{room}")
    _frames(panel)
    assert _names(project)[0] == ("p", [("Kitchen", [], ["D5", "D6"])], [])
    # A building holds no devices: told why.
    panel._report(project.set_device_space(first, building))  # pyright: ignore[reportPrivateUsage]
    assert notes
    # "Unassign" takes it out again.
    _choose(clicks, panel, f"##spdev_ctx_{first}", S.SPACES_UNASSIGN)
    assert _names(project)[0] == ("p", [("Kitchen", [], ["D6"])], [])


def test_functions_are_added_in_a_space(
    project: ProjectService, clicks: Clicks
) -> None:
    building = project.get_space_tree()[0].id
    panel = _panel(project, [])
    _frames(panel)
    _choose(
        clicks, panel, f"##spctx{building}", S.SPACES_ADD_MENU, S.SPACES_ADD_FUNCTIONS
    )
    panel._new_fn_name = "Ceiling light"  # pyright: ignore[reportPrivateUsage]
    clicks.button = S.SPACES_FN_CREATE
    _frames(panel)
    assert [f.name for f in project.get_space_tree()[0].functions] == ["Ceiling light"]


def test_trades_are_added_moved_and_deleted(
    project: ProjectService, clicks: Clicks
) -> None:
    panel = _panel(project, [])
    _frames(panel)
    _choose(clicks, panel, "##trades_root_ctx", S.TRADES_ADD)
    panel._add_name = "Lighting"  # pyright: ignore[reportPrivateUsage]
    clicks.button = S.SPACES_SPACE_CREATE
    _frames(panel)
    (light,) = project.get_trade_tree()
    clicks.open.add(f"Lighting##trade{light.id}")
    _choose(
        clicks, panel, f"##tradectx{light.id}", S.SPACES_ADD_MENU, S.TRADES_ADD_TRADES
    )
    panel._add_count = 2  # pyright: ignore[reportPrivateUsage]
    clicks.button = S.SPACES_SPACE_CREATE
    _frames(panel)
    first, second = project.get_trade_tree()[0].children
    assert (first.name, second.name) == (S.TRADES_DEFAULT, S.TRADES_DEFAULT)
    # A sub-trade moves to the top level and below another trade.
    _choose(
        clicks, panel, f"##tradectx{first.id}", S.SPACES_MOVE_TO, S.SPACES_MOVE_ROOT
    )
    assert len(project.get_trade_tree()) == 2
    _choose(
        clicks,
        panel,
        f"##tradectx{first.id}",
        S.SPACES_MOVE_TO,
        f"  {S.TRADES_DEFAULT}##mvtr{first.id}_{second.id}",
    )
    assert [t.name for t in project.get_trade_tree()] == ["Lighting"]
    _choose(clicks, panel, f"##tradectx{light.id}", S.SPACES_DELETE)
    assert project.get_trade_tree() == []


def test_devices_go_into_trades_and_out_again(
    project: ProjectService, clicks: Clicks
) -> None:
    device = _device(project, 5)
    trade = project.create_trade(None, "Lighting")
    assert trade is not None
    panel = _panel(project, [])
    clicks.open.add(f"Lighting##trade{trade}")
    _frames(panel)
    _choose(
        clicks, panel, f"##tradectx{trade}", S.SPACES_ADD_MENU, S.SPACES_ADD_DEVICES
    )
    clicks.items.add(f"1.1.5  D5##tradepick{trade}_{device}")
    _frames(panel)
    assert [d.device.name for d in project.get_trade_tree()[0].devices] == ["D5"]
    # Its context menu takes it out of the trade.
    _choose(clicks, panel, f"##spdev_ctx_t{trade}{device}", S.TRADES_REMOVE_DEVICE)
    assert project.get_trade_tree()[0].devices == []


def test_the_filter_shows_what_matches(project: ProjectService, context: None) -> None:
    building = project.get_space_tree()[0].id
    room = project.create_space(building, "Room", "Kitchen")
    assert room is not None
    project.create_trade(None, "Lighting")
    panel = _panel(project, [])
    for text in ("kitch", "light", "nothing"):
        panel._filter_text = text  # pyright: ignore[reportPrivateUsage]
        _frames(panel, 2)

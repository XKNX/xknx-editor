"""The Buildings view: its "Buildings" and "Trades" roots, what each building part offers to add
and where it can be moved, the trades with their devices, and the texts of the types."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.space_text import (
    add_item_text,
    add_title,
    default_name,
    space_error_text,
    space_type_name,
)
from editor_gui.plugins.project.ui.spaces import SpacesPanel
from xknxeditor.proj.core.space_rules import SPACE_TYPES, SpaceRuleError


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
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def test_the_texts_of_the_types() -> None:
    assert space_type_name("DistributionBoard") == "Cabinet"
    assert space_type_name("Ground") == "Ground"
    assert add_item_text("Floor") == "Floors…"
    assert add_title("DistributionBoard") == "Add Cabinets"
    assert default_name("Room") == "New room"
    assert default_name("Ground") == "Ground"
    for space_type in SPACE_TYPES:
        assert add_item_text(space_type).endswith("…")
    assert space_error_text(SpaceRuleError("cabinet_not_here")).startswith(
        "A building part of this type cannot be added here.\nCabinets"
    )


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


@pytest.mark.usefixtures("context")
def test_the_view_shows_buildings_and_trades(project: ProjectService) -> None:
    building = project.get_space_tree()[0].id
    floor = project.create_space(building, "Floor", "EG")
    assert floor is not None
    room = project.create_space(floor, "Room", "Küche")
    assert room is not None
    cabinet = project.create_space(room, "DistributionBoard", "UV")
    assert cabinet is not None
    light = project.create_trade(None, "Lighting")
    assert light is not None
    project.create_trade(light, "Inside")
    notes: list[str] = []
    panel = _panel(project, notes)
    for _frame in range(2):
        imgui.new_frame()
        imgui.begin("Buildings")
        panel.render()
        imgui.end()
        imgui.end_frame()
    # A room goes below a building or a floor, a cabinet only below a room (here).
    assert [s.name for s, _depth in panel._move_targets(room, "Room")] == ["p", "EG"]
    assert [
        s.name for s, _depth in panel._move_targets(cabinet, "DistributionBoard")
    ] == ["Küche"]
    # Devices go to the spaces that hold them.
    assert [s.name for s, _depth in panel._move_targets(-1)] == ["Küche", "UV"]
    # A trade goes below any trade but itself and those below it.
    inside = project.get_trade_tree()[0].children[0].id
    assert [t.name for t, _depth in panel._trade_targets(light)] == []
    assert [t.name for t, _depth in panel._trade_targets(inside)] == ["Lighting"]
    panel._report(project.move_space(cabinet, floor))
    assert notes and notes[0].startswith("A building part of this type")


def test_a_trade_shows_its_details(project: ProjectService) -> None:
    trade = project.create_trade(None, "Lighting")
    assert trade is not None
    project.set_trade_field(trade, "number", "01")
    details = project.element_details("trade", trade)
    assert details is not None
    assert (details.title_kind, details.name, details.number, details.status) == (
        "trade",
        "Lighting",
        "01",
        "Undefined",
    )
    project.rename_element("trade", trade, "Light")
    project.set_element_description("trade", trade, "All lights")
    project.set_completion_status("trade", trade, "Tested")
    details = project.element_details("trade", trade)
    assert details is not None
    assert (details.name, details.description, details.status) == (
        "Light",
        "All lights",
        "Tested",
    )
    assert project.history()[0].display_text == "Set status to Tested"
    assert project.history()[1].display_text == "Set trade description"
    assert project.history()[2].display_text == "Rename trade to 'Light'"
    assert project.element_details("trade", 999) is None

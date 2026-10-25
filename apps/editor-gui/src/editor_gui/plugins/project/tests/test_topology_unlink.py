""" "Unlink" of devices in the Topology tree: a device on a line is taken off it, and a device on
no line is listed under "Unassigned"."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.device import UnloadedDevice
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import topology as topology_ui
from editor_gui.plugins.project.ui.topology import Area, Line, TopologyPanel

from ._clicks import Clicks
from .test_buildings_actions import _device
from .test_move_to_menus import _frames


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _panel(
    devices: list[UnloadedDevice],
    segments: dict[int, int],
    unlinked: list[int],
) -> TopologyPanel:
    line = Line(1, 1, 1, "Office", main_segment_id=10)
    return TopologyPanel(
        get_devices=list,
        get_areas=lambda: [Area(1, 1, "Ground")],
        get_lines=lambda _area_id: [line],
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _s: None,
        on_remove_area=lambda _a: None,
        on_rename_area=lambda _a, _n: None,
        on_create_line=lambda _a, _n, _s: None,
        on_remove_line=lambda _l: None,
        on_rename_line=lambda _l, _n: None,
        get_unloaded_devices=lambda: devices,
        get_device_segments=lambda: segments,
        on_unlink_device=unlinked.append,
    )


def test_devices_are_on_the_line_of_their_segment_or_unassigned() -> None:
    on_segment = UnloadedDevice(7, "", "Product", "", None)
    by_address = UnloadedDevice(8, "", "Product", "1.1.9", None)
    unknown_line = UnloadedDevice(9, "", "Product", "3.2.1", None)
    loose = UnloadedDevice(11, "", "Product", "", None)
    devices = [on_segment, by_address, unknown_line, loose]
    panel = _panel(devices, {7: 10}, [])
    tree, unassigned = panel._device_lines(devices, [Area(1, 1, "Ground")])  # pyright: ignore[reportPrivateUsage]
    assert [d.node_id for d in tree[1][1]] == [7, 8]
    assert [d.node_id for d in unassigned] == [9, 11]


@pytest.mark.usefixtures("context")
def test_unlink_takes_a_device_off_its_line(monkeypatch: pytest.MonkeyPatch) -> None:
    clicks = Clicks(monkeypatch, topology_ui)
    shown: list[str] = []
    real_item = imgui.menu_item

    def menu_item(label: str, *args: object) -> object:
        shown.append(label)
        return real_item(label, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(imgui, "menu_item", menu_item)
    on_line = UnloadedDevice(7, "", "Product", "1.1.5", None)
    loose = UnloadedDevice(8, "", "Product", "", None)
    unlinked: list[int] = []
    panel = _panel([on_line, loose], {7: 10}, unlinked)
    clicks.open.add("1.0 Ground##area_1")
    _frames(panel)
    clicks.context = "##dev_ctx_7"
    clicks.items.add(S.CONTEXT_UNLINK_LINE)
    _frames(panel, 1)
    assert unlinked == [7]
    # A device on no line has nothing to unlink from.
    shown.clear()
    clicks.context = "##dev_ctx_8"
    _frames(panel, 1)
    assert shown and S.CONTEXT_UNLINK_LINE not in shown


def test_the_project_unlinks_a_device_in_one_undo_step(tmp_path: Path) -> None:
    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "p.xknx")
    try:
        node_id = _device(project, 4)
        assert node_id in project.device_segments()
        project.unlink_device(node_id)
        assert node_id not in project.device_segments()
        assert project.history()[0].display_text == S.HISTORY_EV_UNLINK_DEVICE
        project.undo()
        assert node_id in project.device_segments()
    finally:
        project.close()

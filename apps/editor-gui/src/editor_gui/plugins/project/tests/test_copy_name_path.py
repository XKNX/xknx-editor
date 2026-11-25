"""The "Copy Name" and "Copy Path" context-menu items of the Group Addresses, Topology and
Buildings trees put the element's name or its "/"-joined path on the clipboard."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import add_dialog, group_addresses, spaces, topology
from editor_gui.plugins.project.ui.group_addresses import (
    GroupAddressesPanel,
    group_text,
)
from editor_gui.plugins.project.ui.spaces import SpacesPanel
from editor_gui.plugins.project.ui.topology import TopologyPanel
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.service import SpaceDeviceInfo

from ._clicks import Clicks

_NONE = "<nothing copied>"


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx", group_address_style=GroupAddressStyle.THREE_LEVEL)
    yield proj
    proj.close()


def _frames(render: Any, count: int = 3) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Tree")
        render()
        imgui.end()
        imgui.end_frame()


def _copy(render: Any, clicks: Clicks, context_id: str, item: str) -> str:
    """Open ``context_id`` and click ``item``; the clipboard text afterwards (``_NONE`` when
    the item was not there to click)."""
    imgui.set_clipboard_text(_NONE)
    clicks.context = context_id
    clicks.items.add(item)
    _frames(render)
    return imgui.get_clipboard_text()


def _ga_panel(project: ProjectService) -> GroupAddressesPanel:
    return GroupAddressesPanel(
        get_range_tree=project.get_group_range_tree,
        get_assignments_for_ga=project.get_assignments_for_ga,
        get_devices=lambda: [],
        on_create_ga_in=project.create_group_address_in,
        on_remove_ga=project.remove_group_address,
        on_create_range=project.create_group_range,
        on_remove_range=project.remove_group_range,
        group_style=lambda: project.group_address_style,
        one_undo_step=project.one_undo_step,
    )


@pytest.mark.usefixtures("context")
def test_group_address_copies_name_and_path(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    main = project.create_group_range(None, "Lights", 1)
    middle = project.create_group_range(main, "Ground", 1)
    assert main is not None and middle is not None
    base = project.get_group_range_tree()
    main_node = next(n for n in base if n.id == main)
    middle_node = next(n for n in main_node.children if n.id == middle)
    ga = project.create_group_address_in(middle, middle_node.range_start, "Kitchen")
    panel = _ga_panel(project)
    clicks.open.add(f"{group_text(main_node)}##gr{main}")
    clicks.open.add(f"{group_text(middle_node)}##gr{middle}")
    _frames(panel.render)
    assert (
        _copy(panel.render, clicks, f"##ga_ctx_{ga}", S.CONTEXT_COPY_NAME) == "Kitchen"
    )
    assert (
        _copy(panel.render, clicks, f"##ga_ctx_{ga}", S.CONTEXT_COPY_PATH)
        == "Lights/Ground/Kitchen"
    )
    # The middle group: its name and the path down to it.
    assert (
        _copy(panel.render, clicks, f"##gr_ctx_{middle}", S.CONTEXT_COPY_NAME)
        == "Ground"
    )
    assert (
        _copy(panel.render, clicks, f"##gr_ctx_{middle}", S.CONTEXT_COPY_PATH)
        == "Lights/Ground"
    )


@pytest.mark.usefixtures("context")
def test_root_group_has_no_copy_path(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    main = project.create_group_range(None, "Lights", 1)
    assert main is not None
    panel = _ga_panel(project)
    _frames(panel.render)
    # The top group's path equals its name, so only "Copy Name" is offered.
    assert (
        _copy(panel.render, clicks, f"##gr_ctx_{main}", S.CONTEXT_COPY_NAME) == "Lights"
    )
    assert _copy(panel.render, clicks, f"##gr_ctx_{main}", S.CONTEXT_COPY_PATH) == _NONE


@pytest.mark.usefixtures("context")
def test_topology_device_copies_name_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    clicks = Clicks(monkeypatch, topology, add_dialog)
    panel = TopologyPanel(
        get_devices=list,
        get_areas=list,
        get_lines=lambda _a: [],
        on_select_device=lambda _d: None,
        on_move_device=lambda *_a: None,
        on_create_area=lambda *_a: None,
        on_remove_area=lambda _a: None,
        on_create_line=lambda *_a: None,
        on_remove_line=lambda _l: None,
        get_device_product=lambda _n: ("Maker", "Product", "Hardware"),
    )
    device = Device(7, "Kitchen Light", None, "1.1.7")
    leaf_flags = (
        imgui.TreeNodeFlags_.leaf.value
        | imgui.TreeNodeFlags_.no_tree_push_on_open.value
    )

    def render() -> None:
        panel._render_device_node(  # pyright: ignore[reportPrivateUsage]
            device, lambda _d: leaf_flags, [7], "Area/Line"
        )

    assert _copy(render, clicks, "##dev_ctx_7", S.CONTEXT_COPY_NAME) == "Kitchen Light"
    assert (
        _copy(render, clicks, "##dev_ctx_7", S.CONTEXT_COPY_PATH)
        == "Area/Line/Kitchen Light"
    )


@pytest.mark.usefixtures("context")
def test_buildings_device_copies_name_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    clicks = Clicks(monkeypatch, spaces, add_dialog)
    panel = SpacesPanel(list, lambda _n: None, get_app_version=lambda _n: "2.6")
    device = SpaceDeviceInfo(
        7, "Kitchen Light", "1.1.7", "", "Product", "Hardware", "Maker"
    )

    def render() -> None:
        panel._render_device(  # pyright: ignore[reportPrivateUsage]
            device, space_id=1, path="House/Ground/Kitchen"
        )

    assert (
        _copy(render, clicks, "##spdev_ctx_7", S.CONTEXT_COPY_NAME) == "Kitchen Light"
    )
    assert (
        _copy(render, clicks, "##spdev_ctx_7", S.CONTEXT_COPY_PATH)
        == "House/Ground/Kitchen/Kitchen Light"
    )

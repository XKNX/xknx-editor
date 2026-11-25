"""Row information gives way to the tooltips of markers and live values."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.device import Device, UnloadedDevice
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import group_addresses, spaces, topology
from editor_gui.plugins.project.ui.group_addresses import GroupAddressesPanel
from editor_gui.plugins.project.ui.spaces import SpacesPanel
from editor_gui.plugins.project.ui.topology import TopologyPanel
from editor_gui.widgets import description
from editor_gui.widgets.dpt_value import dpt_label
from xknxeditor.prod import Application
from xknxeditor.proj.core.service import (
    GroupAddressInfo,
    GroupRangeInfo,
    SpaceDeviceInfo,
)

_NOTE = "A description long enough to appear behind the note icon"


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class Hover:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tips: list[str] = []
        self.positions: dict[str, tuple[float, float]] = {}
        real_colored = imgui.text_colored
        real_tip = imgui.set_tooltip
        real_text = imgui.text_unformatted

        def colored(color: imgui.ImVec4, text: str) -> None:
            real_colored(color, text)
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            self.positions[text] = ((low.x + high.x) / 2, (low.y + high.y) / 2)

        def tip(text: str) -> None:
            self.tips.append(text)
            real_tip(text)

        def text(value: str) -> None:
            if imgui.internal.get_current_window().flags & imgui.WindowFlags_.tooltip:
                self.tips.append(value)
            real_text(value)

        monkeypatch.setattr(imgui, "text_colored", colored)
        monkeypatch.setattr(imgui, "set_tooltip", tip)
        monkeypatch.setattr(imgui, "text_unformatted", text)

    def at(self, render: Callable[[], None], spot: str) -> list[str]:
        def frame() -> None:
            self.tips.clear()
            imgui.new_frame()
            imgui.set_next_window_pos(imgui.ImVec2(0, 0))
            imgui.set_next_window_size(imgui.ImVec2(900, 700))
            imgui.begin("Tree")
            pos = imgui.get_cursor_screen_pos()
            self.positions["row"] = (pos.x + 35, pos.y + 6)
            render()
            imgui.end()
            imgui.end_frame()

        frame()
        frame()
        imgui.get_io().add_mouse_pos_event(*self.positions[spot])
        frame()
        frame()
        return self.tips


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("dpt", [None, "DPST-1-1"])
@pytest.mark.parametrize(
    "spot", ["row", description.ICON_DESCRIPTION, description.ICON_SECURE, "On"]
)
def test_group_address_tooltips(
    monkeypatch: pytest.MonkeyPatch, dpt: str | None, spot: str
) -> None:
    monkeypatch.setattr(description, "_secure_tooltip", lambda: "Secure")
    ga = GroupAddressInfo(1, 1, "0/0/1", "Light", dpt, [], _NOTE, "", True)
    group = GroupRangeInfo(2, "Lights", 0, 255, [], [ga])
    panel = GroupAddressesPanel(
        get_range_tree=lambda: [group],
        get_assignments_for_ga=lambda _n: [],
        get_devices=list,
        on_create_ga_in=lambda *_a: None,
        on_remove_ga=lambda _n: None,
        get_latest_value=lambda *_a: ("On", "12:34:56", 0.0),
    )
    hover = Hover(monkeypatch)
    tips = hover.at(lambda: panel._render_group_addresses(group, ""), spot)
    expected = {
        "row": [dpt_label(dpt)] if dpt else [],
        description.ICON_DESCRIPTION: [_NOTE],
        description.ICON_SECURE: ["Secure"],
        "On": ["On\n12:34:56"],
    }
    assert tips == expected[spot]


@pytest.mark.usefixtures("context")
def test_group_address_hover_lists_linked_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    co = SimpleNamespace(db_id=10, number=1, name="Switch", function_text="")
    device = SimpleNamespace(
        display_name="Taster", individual_address="1.1.5", node_id=7, com_objects=[co]
    )
    ga = GroupAddressInfo(1, 1, "0/0/1", "Light", "DPST-1-1", [], "", "", False)
    group = GroupRangeInfo(2, "Lights", 0, 255, [], [ga])
    panel = GroupAddressesPanel(
        get_range_tree=lambda: [group],
        get_assignments_for_ga=lambda _n: [
            SimpleNamespace(id=1, com_object_id=10, group_address_id=1, is_sending=True)
        ],
        get_devices=lambda: [device],
        on_create_ga_in=lambda *_a: None,
        on_remove_ga=lambda _n: None,
        get_latest_value=lambda *_a: None,
    )
    hover = Hover(monkeypatch)
    tips = hover.at(lambda: panel._render_group_addresses(group, ""), "row")
    # One tooltip: the data point type, then the linked object (sending marked).
    assert tips == [f"{dpt_label('DPST-1-1')}\n» 1.1.5  Taster  —  Switch"]


def _topology() -> TopologyPanel:
    return TopologyPanel(
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


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("view", ["topology", "buildings", "unloaded"])
@pytest.mark.parametrize(
    "spot",
    [
        "row",
        description.ICON_DESCRIPTION,
        description.ICON_SECURE,
        description.ICON_DOWNLOAD,
    ],
)
def test_device_markers_take_the_hover(
    monkeypatch: pytest.MonkeyPatch, view: str, spot: str
) -> None:
    monkeypatch.setattr(description, "_secure_devices", lambda: {7})
    monkeypatch.setattr(description, "_secure_tooltip", lambda: "Secure")
    monkeypatch.setattr(description, "_pending_download", lambda _n: "Pending")
    # Keep drag previews and context menus out of this hover-only test.
    monkeypatch.setattr(topology, "context_menu", lambda _id: False)
    monkeypatch.setattr(spaces, "context_menu", lambda _id: False)
    monkeypatch.setattr(group_addresses, "context_menu", lambda _id: False)
    if view == "buildings":
        panel = SpacesPanel(list, lambda _n: None, get_app_version=lambda _n: "2.6")
        info = SpaceDeviceInfo(
            7, "Device", "1.1.7", _NOTE, "Product", "Hardware", "Maker"
        )

        def render() -> None:
            panel._render_device(info)

        row_tip = "Maker\nProduct\nHardware\nV2.6"
    else:
        tree = _topology()
        device = (
            UnloadedDevice(7, "Device", "Product", "1.1.7", None, _NOTE)
            if view == "unloaded"
            else Device(7, "Device", None, "1.1.7", description=_NOTE)
        )
        flags = (
            imgui.TreeNodeFlags_.leaf
            | imgui.TreeNodeFlags_.no_tree_push_on_open
            | imgui.TreeNodeFlags_.span_avail_width
        )

        def render() -> None:
            tree._render_device_node(device, lambda _d: flags, [7], "")

        row_tip = (
            S.DEVICE_PRODUCT_DATA_MISSING
            if view == "unloaded"
            else "Maker\nProduct\nHardware"
        )
    hover = Hover(monkeypatch)
    assert hover.at(render, spot) == [
        {
            "row": row_tip,
            description.ICON_DESCRIPTION: _NOTE,
            description.ICON_SECURE: "Secure",
            description.ICON_DOWNLOAD: "Pending",
        }[spot]
    ]


@pytest.mark.parametrize(
    ("name", "application"),
    [("Switch", "Switch V2.6"), ("", "V2.6"), ("Switch V2.6", "Switch V2.6")],
)
def test_topology_application_version_and_product_fallback(
    monkeypatch: pytest.MonkeyPatch, name: str, application: str
) -> None:
    def load() -> Application:
        pytest.fail("Hover must not load an application with a parseable id")

    app = Application.unloaded("M-0083_A-0045-26-ABCD", name, "23", "M-0083", load)
    device = Device(
        7,
        "Device",
        app,
        "1.1.7",
        product_name="Product",
        order_number="123",
        visible=[],
    )
    panel = _topology()
    tips: list[str] = []
    monkeypatch.setattr(imgui, "set_tooltip", tips.append)
    panel._device_tooltip(device)
    assert tips == [f"Maker\nProduct\nHardware\n{application}\n123"]
    panel._get_device_product = lambda _n: ("", "", "")
    panel._device_tooltip(device)
    assert tips[-1] == f"Product\n{application}\n123"


def test_product_lookup_includes_devices_without_a_line(tmp_path: Path) -> None:
    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    assert project.device_product(1) == ("", "", "")
    project.new(tmp_path / "p.xknx")
    try:
        svc, pid = project._svc, project._pid
        assert pid is not None
        node_id = svc.add_device(
            pid,
            None,
            "Product",
            manufacturer_name="Maker",
            product_name="Product",
            hardware_name="Hardware",
        )
        project._bump()
        assert all(d.id != node_id for d in project.get_space_devices())
        assert project.device_product(node_id) == ("Maker", "Product", "Hardware")
        assert project.device_product(node_id + 1) == ("", "", "")
        assert project.device_app_version(node_id + 1) == ""
    finally:
        project.close()

"""Drive the real table through ImGui input; observe rendered rows and public callbacks."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.widgets.group_objects_widgets import GroupObjectsTable
from xknxeditor.prod import load


@pytest.fixture
def device() -> Device:
    path = (
        Path(__file__).parents[6]
        / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
    )
    app = next(iter(load(path.read_bytes()).applications.values()))
    return Device(1, "Gira", app, "1.1.1")


class TableUI:
    def __init__(self, device: Device, monkeypatch: pytest.MonkeyPatch) -> None:
        self.device = device
        self.width = 950
        self.popup_height = 0.0
        self.backgrounds: dict[str, tuple[float, float, float, float]] = {}
        self.rects: dict[str, tuple[float, float]] = {}
        self.texts: list[str] = []
        self.created: list[list[int]] = []
        self.table = GroupObjectsTable(
            set_flag=lambda *_: None,
            on_link=lambda *_: None,
            on_unlink=lambda *_: None,
            on_auto_create_gas=lambda _device, objects, _start, _template: (
                self.created.append([co.number for co in objects])
            ),
        )
        for name in (
            "button",
            "checkbox",
            "begin_combo",
            "selectable",
            "input_text_with_hint",
        ):
            original = getattr(imgui, name)

            def record(
                label: str, *args: Any, _original: Any = original, **kwargs: Any
            ) -> Any:
                color = imgui.get_style().color_(imgui.Col_.frame_bg)
                self.backgrounds[label] = (color.x, color.y, color.z, color.w)
                result = _original(label, *args, **kwargs)
                if label == "##module_filter" and result:
                    self.popup_height = imgui.get_window_size().y
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                self.rects[label] = ((low.x + high.x) / 2, (low.y + high.y) / 2)
                return result

            monkeypatch.setattr(imgui, name, record)
        text = imgui.text

        def record_text(value: str) -> None:
            self.texts.append(value)
            text(value)

        monkeypatch.setattr(imgui, "text", record_text)

    def frame(self) -> None:
        self.texts.clear()
        self.rects.clear()
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(self.width, 650))
        imgui.begin("Objects", None, imgui.WindowFlags_.no_title_bar)
        self.table.render(
            self.device, self.device.get_visible_com_objects(), lambda _: [], lambda: []
        )
        imgui.end()
        imgui.render()

    def click(self, label: str) -> None:
        assert label in self.rects, (
            f"Control missing: {label}; available: {list(self.rects)}"
        )
        io = imgui.get_io()
        io.add_mouse_pos_event(*self.rects[label])
        self.frame()
        io.add_mouse_button_event(0, True)
        self.frame()
        io.add_mouse_button_event(0, False)
        self.frame()
        self.frame()

    def close_combo(self) -> None:
        io = imgui.get_io()
        io.add_key_event(imgui.Key.escape, True)
        self.frame()
        io.add_key_event(imgui.Key.escape, False)
        self.frame()

    def numbers(self) -> list[int]:
        return [
            co.number
            for co in self.device.get_visible_com_objects()
            if any(text.startswith(co.name) for text in self.texts)
        ]


@pytest.fixture
def ui(device: Device, monkeypatch: pytest.MonkeyPatch) -> Iterator[TableUI]:
    context = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 700)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    table = TableUI(device, monkeypatch)
    table.frame()
    table.frame()
    yield table
    imgui.destroy_context(context)


def test_dropdown_filters_multiple_modules_and_resets(ui: TableUI) -> None:
    assert ui.numbers() == [254, 253, 262, 261]
    ui.click("##module_filter")
    assert ui.popup_height < 180
    ui.click("Channel 2 (...)")
    ui.close_combo()
    assert ui.numbers() == [262, 261]
    ui.click("##module_filter")
    ui.click("Channel 1 (...)")
    ui.close_combo()
    assert ui.numbers() == [254, 253, 262, 261]
    ui.click("##module_filter")
    ui.click("All modules")
    ui.close_combo()
    assert ui.numbers() == [254, 253, 262, 261]


def test_module_filter_has_no_external_label(ui: TableUI) -> None:
    assert "Modules" not in ui.rects
    assert "##module_filter" in ui.rects


def test_closed_module_filter_uses_field_background_without_changing_other_combos(
    ui: TableUI,
) -> None:
    from editor_gui.widgets.combo import begin_combo

    style = imgui.get_style()
    style.set_color_(imgui.Col_.frame_bg, imgui.ImVec4(1, 1, 1, 1))
    style.set_color_(imgui.Col_.text, imgui.ImVec4(0.1, 0.1, 0.1, 1))
    ui.frame()
    assert ui.backgrounds["##module_filter"] == pytest.approx((1, 1, 1, 1))
    imgui.new_frame()
    imgui.begin("Reference")
    if begin_combo("Other dropdown", "Choice"):
        imgui.end_combo()
    imgui.end()
    imgui.render()
    assert ui.backgrounds["Other dropdown"] == pytest.approx(
        (0.9424, 0.9424, 0.9424, 1)
    )
    color = style.color_(imgui.Col_.frame_bg)
    assert (color.x, color.y, color.z, color.w) == pytest.approx((1, 1, 1, 1))


@pytest.mark.parametrize("screen_height", [1000, 400])
def test_long_module_list_uses_more_room_but_at_most_sixty_percent(
    ui: TableUI, screen_height: int
) -> None:
    from xml.etree import ElementTree as ET

    from xknxeditor.prod.application import parse_application_xml

    fixture = (
        Path(__file__).parents[6]
        / "packages/prod/tests/fixtures/object_tree_application.xml"
    )
    root = ET.fromstring(fixture.read_bytes())
    dynamic = root.find(".//{*}Dynamic")
    assert dynamic is not None
    namespace = dynamic.tag.removesuffix("Dynamic")
    ET.register_namespace("", namespace[1:-1])
    dynamic.clear()
    for index in range(30):
        channel = ET.SubElement(
            dynamic,
            f"{namespace}Channel",
            {
                "Id": f"M-00FA_A-0005-01-0000_CH-{index}",
                "Text": f"Module {index}",
                "Name": f"Module {index}",
                "Number": str(index),
            },
        )
        ET.SubElement(
            channel,
            f"{namespace}ComObjectRefRef",
            {
                "RefId": "M-00FA_A-0005-01-0000_O-2_R-2",
            },
        )
    app = parse_application_xml(ET.tostring(root), "M-00FA")[0]
    ui.device = Device(2, "Many modules", app, "1.2.10")
    imgui.get_io().display_size = imgui.ImVec2(1000, screen_height)
    ui.frame()
    ui.frame()
    ui.click("##module_filter")
    assert min(300, screen_height * 0.5) < ui.popup_height <= screen_height * 0.6


def test_nested_modules_are_indented_and_selectable(ui: TableUI) -> None:
    from xknxeditor.prod.application import parse_application_xml

    fixture = (
        Path(__file__).parents[6]
        / "packages/prod/tests/fixtures/object_tree_application.xml"
    )
    app = parse_application_xml(fixture.read_bytes(), "M-00FA")[0]
    ui.device = Device(2, "Tree", app, "1.2.10")
    ui.frame()
    ui.click("##module_filter")
    assert ui.rects["Status"][0] > ui.rects["Channel A"][0]
    ui.click("Status")
    ui.close_combo()
    assert ui.numbers() == [3]
    ui.click("##module_filter")
    ui.click("Channel A")
    ui.close_combo()
    assert ui.numbers() == [6, 2, 3]


def test_hidden_selection_does_not_return_and_device_switch_clears_selection(
    ui: TableUI,
) -> None:
    ui.click("All")
    ui.click("##module_filter")
    ui.click("Channel 2 (...)")
    ui.close_combo()
    ui.click("##module_filter")
    ui.click("All modules")
    ui.close_combo()
    ui.click("Create Group Addresses (2)")
    ui.click("Create Group Addresses (2)")
    assert ui.created == [[262, 261]]
    ui.click("All")
    ui.device = Device(2, "Other", ui.device.app, "1.1.2")
    ui.frame()
    assert "Create Group Addresses (0)" in ui.rects


@pytest.mark.parametrize("width", [950, 340])
def test_search_and_device_switch_work_on_wide_and_narrow_panels(
    ui: TableUI, width: int
) -> None:
    ui.width = width
    ui.frame()
    ui.click("##module_filter")
    ui.click("##module_search")
    imgui.get_io().add_input_characters_utf8("CHANNEL 2")
    ui.frame()
    assert "Channel 1 (...)" not in ui.rects
    assert "Channel 2 (...)" in ui.rects
    ui.click("Channel 2 (...)")
    ui.close_combo()
    assert ui.numbers() == [262, 261]
    ui.device = Device(2, "Other", ui.device.app, "1.1.2")
    ui.frame()
    assert ui.numbers() == [254, 253, 262, 261]
    ui.click("##module_filter")
    assert "Channel 1 (...)" in ui.rects
    assert "Channel 2 (...)" in ui.rects

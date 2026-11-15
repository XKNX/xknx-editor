"""The theme's choice colors on drop-downs and radio buttons, and table rows highlighted from line
to line."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.widgets import checkbox as checkbox_module
from editor_gui.widgets.choice import ChoiceColors, set_choice_colors
from editor_gui.widgets.combo import begin_combo, end_combo
from editor_gui.widgets.tables import row_selectable

BLUE = imgui.ImVec4(195 / 255, 208 / 255, 221 / 255, 1.0)
LIST = imgui.ImVec4(242 / 255, 242 / 255, 242 / 255, 1.0)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    set_choice_colors(ChoiceColors(fill=BLUE, list_bg=LIST))
    yield
    set_choice_colors(None)
    imgui.destroy_context(ctx)


def _same(a: imgui.ImVec4, b: imgui.ImVec4) -> bool:
    pairs = zip((a.x, a.y, a.z), (b.x, b.y, b.z), strict=True)
    return all(abs(x - y) < 1e-6 for x, y in pairs)


def test_an_open_drop_down_chooses_in_the_choice_color(context: None) -> None:
    header = imgui.get_style().color_(imgui.Col_.header)
    seen: list[imgui.ImVec4] = []
    for frame in range(3):
        imgui.new_frame()
        imgui.begin("Window")
        if frame == 1:
            popup = internal.im_hash_str("##ComboPopup", 0, imgui.get_id("##choice"))
            internal.open_popup_ex(popup, 0)
        if begin_combo("##choice", "One"):
            seen.append(imgui.get_style_color_vec4(imgui.Col_.header))
            imgui.selectable("One", True)
            end_combo()
        # Back to the theme's colors after the drop-down.
        assert _same(imgui.get_style_color_vec4(imgui.Col_.header), header)
        imgui.end()
        imgui.end_frame()
    assert seen and all(_same(color, BLUE) for color in seen)


def test_a_chosen_radio_button_has_a_dot_in_the_choice_color(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    dots: list[imgui.ImVec4] = []
    real = imgui.radio_button

    def radio(label: str, active: bool) -> Any:
        dots.append(imgui.get_style_color_vec4(imgui.Col_.check_mark))
        return real(label, active)

    monkeypatch.setattr(imgui, "radio_button", radio)
    imgui.new_frame()
    imgui.begin("Window")
    checkbox_module.radio_button("Yes", True)
    imgui.end()
    imgui.end_frame()
    assert len(dots) == 1 and _same(dots[0], BLUE)


@pytest.mark.parametrize("lines", [False, True])
def test_a_table_row_is_highlighted_from_line_to_line(
    context: None, monkeypatch: pytest.MonkeyPatch, lines: bool
) -> None:
    spacing: list[float] = []
    real = imgui.selectable

    def selectable(*args: Any) -> Any:
        spacing.append(imgui.get_style().item_spacing.y)
        return real(*args)

    monkeypatch.setattr(imgui, "selectable", selectable)
    imgui.new_frame()
    imgui.begin("Window")
    flags = imgui.TableFlags_.borders_inner_h if lines else 0
    if imgui.begin_table("##rows", 2, flags):
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        row_selectable("Row")
        imgui.end_table()
    imgui.end()
    imgui.end_frame()
    # imgui's highlight reaches half the item spacing past the text: the cell padding, less
    # the pixel of the line between the rows on top of the row.
    padding = imgui.get_style().cell_padding.y
    assert spacing == [padding * 2 - (1.0 if lines else 0.0)]

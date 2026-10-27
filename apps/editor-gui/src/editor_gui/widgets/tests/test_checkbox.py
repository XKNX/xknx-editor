"""Radio buttons beside a field share its row: both centred on it, the row as high as the field.
A checkbox with a long text wraps it at the window's edge, and a click on the text toggles it."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.checkbox import radio_beside_field, wrapped_checkbox

_LONG = "Create backups at regular intervals while a project is open##interval"


def _wrapped_frame(value: bool) -> tuple[bool, bool, tuple[float, float, float, float]]:
    """One frame of a narrow window with the wrapped checkbox: what it returns and its text's
    rectangle."""
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(200, 200))
    imgui.begin("Narrow", None, imgui.WindowFlags_.no_title_bar)
    changed, value = wrapped_checkbox(_LONG, value)
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    right = imgui.get_window_pos().x + imgui.get_window_size().x
    imgui.end()
    imgui.end_frame()
    imgui.render()
    return changed, value, (low.x, low.y, high.x, right)


@pytest.mark.usefixtures("context")
def test_a_long_checkbox_text_wraps_and_toggles_it() -> None:
    changed, value, (left, top, right, window_right) = _wrapped_frame(False)
    assert (changed, value) == (False, False)
    assert right <= window_right
    io = imgui.get_io()
    io.add_mouse_pos_event(left + 5, top + 5)
    _wrapped_frame(False)
    io.add_mouse_button_event(0, True)
    changed, value, _rect = _wrapped_frame(False)
    io.add_mouse_button_event(0, False)
    assert (changed, value) == (True, True)
    assert _wrapped_frame(True)[:2] == (False, True)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(400, 300)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _rect() -> tuple[float, float]:
    return imgui.get_item_rect_min().y, imgui.get_item_rect_max().y


@pytest.mark.parametrize(
    "field",
    [
        lambda: imgui.input_text("##text", "x"),
        lambda: imgui.input_int("##number", 1),
        lambda: imgui.button("Button"),
    ],
)
@pytest.mark.usefixtures("context")
def test_a_radio_button_and_its_field_are_centred_on_one_row(
    field: Callable[[], object],
) -> None:
    imgui.new_frame()
    imgui.begin("Dialog")
    imgui.text("Above")
    radio_beside_field("Start with", True)
    # The radio button is the bottom of what it returns: the text with a quarter of the frame
    # padding above and below.
    _top, radio_bottom = _rect()
    radio_top = (
        radio_bottom - imgui.get_font_size() - imgui.get_style().frame_padding.y / 2
    )
    field()
    field_top, field_bottom = _rect()
    imgui.text("Below")
    below = imgui.get_item_rect_min().y
    imgui.end()
    imgui.end_frame()
    assert (radio_top + radio_bottom) / 2 == pytest.approx(
        (field_top + field_bottom) / 2, abs=0.5
    )
    assert field_bottom - field_top == pytest.approx(imgui.get_frame_height())
    # The next row follows the field, no taller row in between.
    assert below == pytest.approx(field_bottom + imgui.get_style().item_spacing.y)

"""Radio buttons beside a field share its row: both centred on it, the row as high as the field."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.checkbox import radio_beside_field


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

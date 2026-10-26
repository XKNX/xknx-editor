"""The "x" of the small clear and remove buttons sits in the middle of the button, not low on the
baseline of a text line."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import x_button as module
from editor_gui.widgets.x_button import small_x_size, x_button


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(400, 300)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("small", [False, True])
def test_the_x_is_centred(monkeypatch: pytest.MonkeyPatch, small: bool) -> None:
    drawn: list[imgui.ImVec2] = []

    class Recorder:
        def add_text(self, pos: imgui.ImVec2, _col: int, text: str) -> None:
            assert text == "x"
            drawn.append(pos)

    monkeypatch.setattr(module.imgui, "get_window_draw_list", Recorder)
    imgui.new_frame()
    imgui.begin("Panel")
    side = imgui.get_frame_height()
    size = small_x_size() if small else imgui.ImVec2(side, side)
    assert not x_button("clear", size)
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    glyph = imgui.get_font().get_font_baked(imgui.get_font_size()).find_glyph(ord("x"))
    imgui.end()
    imgui.end_frame()
    (pos,) = drawn
    ink_x = pos.x + (glyph.x0 + glyph.x1) / 2
    ink_y = pos.y + (glyph.y0 + glyph.y1) / 2
    assert abs(ink_x - (low.x + high.x) / 2) <= 0.5
    assert abs(ink_y - (low.y + high.y) / 2) <= 0.5

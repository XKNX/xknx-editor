"""The formatting buttons of a rich text field: each letter centred on its button as it is drawn,
on whole pixels, the underline exactly below the underlined one."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import rich_text_edit
from editor_gui.widgets.rich_text_edit import RichTextEdit


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _Recorder:
    """Takes the place of the window's draw list for the letters and the underline."""

    def __init__(self, draw: imgui.ImDrawList) -> None:
        self._draw = draw
        self.texts: list[tuple[imgui.ImFont, float, float]] = []
        self.rects: list[tuple[float, float, float, float]] = []

    def add_text(
        self, font: imgui.ImFont, size: float, pos: imgui.ImVec2, *args: Any
    ) -> None:
        self.texts.append((font, size, pos.x))

    def add_rect_filled(
        self, low: imgui.ImVec2, high: imgui.ImVec2, *args: Any
    ) -> None:
        self.rects.append((low.x, low.y, high.x, high.y))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._draw, name)


@pytest.mark.usefixtures("context")
def test_letters_centred_and_the_underline_below_its_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buttons: list[tuple[float, float]] = []
    button = imgui.button

    def press(label: str, size: imgui.ImVec2) -> bool:
        pressed = button(label, size)
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        buttons.append((low.x, high.x))
        return pressed

    recorder: list[_Recorder] = []
    window_draw_list = imgui.get_window_draw_list

    def draw_list() -> _Recorder:
        if not recorder:
            recorder.append(_Recorder(window_draw_list()))
        return recorder[0]

    monkeypatch.setattr(rich_text_edit.imgui, "button", press)
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(400, 300))
    imgui.begin("Field", None, imgui.WindowFlags_.no_decoration)
    monkeypatch.setattr(rich_text_edit.imgui, "get_window_draw_list", draw_list)
    RichTextEdit("comment").render(("device", 1, "comment"), "", 100.0, "Comment")
    imgui.end()
    imgui.end_frame()

    letters = recorder[0].texts[-3:]
    assert len(buttons) == 3
    for (left, right), (font, size, x) in zip(buttons, letters, strict=True):
        glyph = font.get_font_baked(size).find_glyph(ord("a"))
        assert x == int(x)
        assert abs(x + (glyph.x0 + glyph.x1) / 2 - (left + right) / 2) < 1.0
    font, size, x = letters[2]
    glyph = font.get_font_baked(size).find_glyph(ord("a"))
    x0, y0, x1, y1 = recorder[0].rects[-1]
    assert (x0, x1) == (round(x + glyph.x0), round(x + glyph.x1))
    assert all(v == int(v) for v in (x0, y0, x1, y1))
    assert y1 - y0 >= 1

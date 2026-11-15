"""Copy buttons are icon buttons as high as a field, right of the text they copy; the browser
file list's download button is an icon like the row's "x"."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.icon_button import (
    ICON_COPY,
    copy_button,
    icon_button,
    icon_button_width,
    text_with_copy_button,
)
from editor_gui.widgets.x_button import (
    glyph_button,
    small_glyph_size,
    small_x_size,
)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frame(content: Callable[[], None]) -> None:
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(400, 300))
    imgui.begin("Test")
    content()
    imgui.end()
    imgui.end_frame()


def _click(at: imgui.ImVec2, content: Callable[[], None]) -> None:
    """Move the mouse to ``at``, press and release it, a frame each (a button reports the
    release)."""
    io = imgui.get_io()
    io.add_mouse_pos_event(at.x, at.y)
    _frame(content)
    io.add_mouse_button_event(0, True)
    _frame(content)
    io.add_mouse_button_event(0, False)
    _frame(content)


def _centre(low: imgui.ImVec2, high: imgui.ImVec2) -> imgui.ImVec2:
    return imgui.ImVec2((low.x + high.x) / 2, (low.y + high.y) / 2)


def test_a_copy_button_puts_the_text_on_the_clipboard(context: None) -> None:
    copied: list[str] = []
    rects: list[tuple[imgui.ImVec2, imgui.ImVec2]] = []
    imgui.get_platform_io().platform_set_clipboard_text_fn = lambda _c, text: (
        copied.append(text)
    )

    def content() -> None:
        if copy_button("value", "1.1.5"):
            copied.append("pressed")
        rects.append((imgui.get_item_rect_min(), imgui.get_item_rect_max()))

    _frame(content)
    low, high = rects[-1]
    # As high as a field: the text does not lose its descenders.
    assert high.y - low.y == pytest.approx(imgui.get_frame_height())
    assert high.x - low.x == pytest.approx(icon_button_width(ICON_COPY))
    _click(_centre(low, high), content)
    assert copied == ["1.1.5", "pressed"]


def test_nothing_is_copied_without_a_text(context: None) -> None:
    copied: list[str] = []
    rects: list[tuple[imgui.ImVec2, imgui.ImVec2]] = []
    imgui.get_platform_io().platform_set_clipboard_text_fn = lambda _c, text: (
        copied.append(text)
    )

    def content() -> None:
        assert not copy_button("empty", "")
        rects.append((imgui.get_item_rect_min(), imgui.get_item_rect_max()))

    _frame(content)
    _click(_centre(*rects[-1]), content)
    assert copied == []


def test_a_disabled_icon_button_is_never_pressed(context: None) -> None:
    rects: list[tuple[imgui.ImVec2, imgui.ImVec2]] = []
    pressed: list[bool] = []

    def content() -> None:
        pressed.append(icon_button(ICON_COPY, "off", "Copy", enabled=False))
        rects.append((imgui.get_item_rect_min(), imgui.get_item_rect_max()))

    _frame(content)
    _click(_centre(*rects[-1]), content)
    assert not any(pressed)


def test_the_copy_button_is_right_of_the_first_line_of_its_text(context: None) -> None:
    text = "a long command that wraps over several lines of the window " * 4
    places: dict[str, imgui.ImVec2] = {}

    def content() -> None:
        imgui.text("A Tool")
        places["title"] = imgui.get_item_rect_max()
        places["text"] = imgui.get_cursor_screen_pos()
        text_with_copy_button("cmd", text)
        places["button"] = imgui.get_item_rect_min()
        places["button_max"] = imgui.get_item_rect_max()
        places["after"] = imgui.get_cursor_screen_pos()

    _frame(content)
    style = imgui.get_style()
    # The text wraps before the button, which sits at the right edge of the window's content.
    assert places["button_max"].x <= 400 - style.window_padding.x + 1
    # Its icon on the text's first line: the button starts a frame padding above the text,
    # which keeps the distance of any line of text to the title above it.
    assert places["text"].y - places["title"].y == pytest.approx(style.item_spacing.y)
    assert places["button"].y == pytest.approx(places["text"].y - style.frame_padding.y)
    # What follows comes below the wrapped text.
    assert places["after"].y > places["text"].y + 2 * imgui.get_text_line_height()


def test_an_aligned_value_and_its_button_share_the_line_of_a_label(
    context: None,
) -> None:
    places: dict[str, imgui.ImVec2] = {}

    def content() -> None:
        places["line"] = imgui.get_cursor_screen_pos()
        text_with_copy_button("endpoint", "http://127.0.0.1:8765/mcp", aligned=True)
        places["button"] = imgui.get_item_rect_min()

    _frame(content)
    assert places["button"].y == pytest.approx(places["line"].y)


def test_a_row_icon_button_is_as_large_as_the_rows_x(context: None) -> None:
    pressed: list[bool] = []
    rects: list[tuple[imgui.ImVec2, imgui.ImVec2]] = []

    def content() -> None:
        pressed.append(glyph_button("download", "", small_glyph_size("")))
        rects.append((imgui.get_item_rect_min(), imgui.get_item_rect_max()))

    _frame(content)
    low, high = rects[-1]
    assert high.y - low.y == pytest.approx(small_x_size().y)
    _click(_centre(low, high), content)
    assert any(pressed)

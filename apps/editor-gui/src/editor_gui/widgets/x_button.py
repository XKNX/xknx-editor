"""The small buttons in a line of text or a table row ("x" to clear a field or remove a row, the
icon of a row's action), with the glyph in the middle of the button: as a label it would sit low,
on the baseline of its text line."""

from __future__ import annotations

from imgui_bundle import imgui


def glyph_button(str_id: str, glyph: str, size: imgui.ImVec2) -> bool:
    """A button ``size`` large with ``glyph`` (one character) centred in it; whether it was
    pressed."""
    pressed = imgui.button(f"##{str_id}", size)
    low = imgui.get_item_rect_min()
    found = (
        imgui.get_font().get_font_baked(imgui.get_font_size()).find_glyph(ord(glyph))
    )
    x = low.x + (size.x - (found.x1 - found.x0)) / 2 - found.x0
    y = low.y + (size.y - (found.y1 - found.y0)) / 2 - found.y0
    # The text color with the alpha of a grayed-out button.
    imgui.get_window_draw_list().add_text(
        imgui.ImVec2(round(x), round(y)), imgui.get_color_u32(imgui.Col_.text), glyph
    )
    return pressed


def x_button(str_id: str, size: imgui.ImVec2) -> bool:
    """A button ``size`` large with an "x" centred in it; whether it was pressed."""
    return glyph_button(str_id, "x", size)


def small_glyph_size(glyph: str) -> imgui.ImVec2:
    """The size of a small button with ``glyph`` (one text line high, beside a line of text)."""
    pad = imgui.get_style().frame_padding.x
    return imgui.ImVec2(
        imgui.calc_text_size(glyph).x + 2 * pad, imgui.get_text_line_height()
    )


def small_x_size() -> imgui.ImVec2:
    """The size of a small button with an "x" (one text line high, beside a line of text)."""
    return small_glyph_size("x")

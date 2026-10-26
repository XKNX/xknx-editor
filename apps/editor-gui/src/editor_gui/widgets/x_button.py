"""The small "x" buttons that clear a field or remove a row, with the "x" in the middle of the
button: as a label it would sit low, on the baseline of its text line."""

from __future__ import annotations

from imgui_bundle import imgui


def x_button(str_id: str, size: imgui.ImVec2) -> bool:
    """A button ``size`` large with an "x" centred in it; whether it was pressed."""
    pressed = imgui.button(f"##{str_id}", size)
    low = imgui.get_item_rect_min()
    glyph = imgui.get_font().get_font_baked(imgui.get_font_size()).find_glyph(ord("x"))
    x = low.x + (size.x - (glyph.x1 - glyph.x0)) / 2 - glyph.x0
    y = low.y + (size.y - (glyph.y1 - glyph.y0)) / 2 - glyph.y0
    # The text color with the alpha of a grayed-out button.
    imgui.get_window_draw_list().add_text(
        imgui.ImVec2(round(x), round(y)), imgui.get_color_u32(imgui.Col_.text), "x"
    )
    return pressed


def small_x_size() -> imgui.ImVec2:
    """The size of a small button with an "x" (one text line high, beside a line of text)."""
    pad = imgui.get_style().frame_padding.x
    return imgui.ImVec2(
        imgui.calc_text_size("x").x + 2 * pad, imgui.get_text_line_height()
    )

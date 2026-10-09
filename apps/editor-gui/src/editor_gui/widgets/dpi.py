"""Sizes that follow the display scaling, like the fonts and the style do."""

from __future__ import annotations

from imgui_bundle import hello_imgui, imgui


def px(value: float) -> float:
    """``value`` pixels at 100 % display scaling, in pixels at the current scaling."""
    return value * hello_imgui.dpi_window_size_factor()


def px_vec2(x: float, y: float) -> imgui.ImVec2:
    """:func:`px` for both components."""
    return imgui.ImVec2(px(x), px(y))


def label_column_width(*labels: str, minimum: float = 120.0) -> float:
    """``same_line`` offset of the value column after ``labels``: the widest label plus item
    spacing, but at least ``minimum`` pixels at 100 % scaling."""
    widest = max((imgui.calc_text_size(label).x for label in labels), default=0.0)
    start = imgui.get_cursor_start_pos().x
    return max(px(minimum), start + widest + imgui.get_style().item_spacing.x)

"""Rows of buttons and short texts that go on in the next line where the window is too narrow for
them (a phone, a narrow panel), instead of running past its edge."""

from __future__ import annotations

from imgui_bundle import imgui
from imgui_bundle.imgui import internal


def button_width(label: str) -> float:
    """Width of a button with ``label`` (an id after ``##`` left out)."""
    return (
        imgui.calc_text_size(label, hide_text_after_double_hash=True).x
        + imgui.get_style().frame_padding.x * 2
    )


def text_width(text: str) -> float:
    return imgui.calc_text_size(text).x


def checkbox_width(label: str) -> float:
    """Width of a checkbox with ``label``, its box included."""
    style = imgui.get_style()
    return (
        imgui.get_frame_height()
        + style.item_inner_spacing.x
        + imgui.calc_text_size(label, hide_text_after_double_hash=True).x
    )


def right_edge() -> float:
    """Where the current window's content ends (screen x), left of a vertical scroll bar."""
    window = internal.get_current_window()
    style = imgui.get_style()
    right = window.pos.x + window.size.x - style.window_padding.x
    if window.scrollbar_y:
        right -= style.scrollbar_size
    return right


def fits_beside(width: float, spacing: float | None = None) -> bool:
    """Whether an item ``width`` wide fits right of the previous item before the window's
    edge."""
    gap = imgui.get_style().item_spacing.x if spacing is None else spacing
    return imgui.get_item_rect_max().x + gap + width <= right_edge()


def same_line_if_fits(width: float, spacing: float | None = None) -> None:
    """Put the next item, ``width`` wide, beside the previous one if it fits there, else at the
    start of the next line."""
    if fits_beside(width, spacing):
        imgui.same_line(0.0, -1.0 if spacing is None else spacing)

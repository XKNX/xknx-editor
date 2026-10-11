"""Checkboxes about the size of the text, the same size everywhere in the app."""

from __future__ import annotations

from imgui_bundle import imgui


def checkbox(label: str, value: bool, *, centred: bool = False) -> tuple[bool, bool]:
    """A checkbox about the size of the text. ``centred`` lines it up with full-height fields
    (buttons, inputs) on the same row; otherwise it lines up with plain text."""
    padding = imgui.get_style().frame_padding
    small = padding.y / 4
    if centred:
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + padding.y - small)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(padding.x, small))
    result = imgui.checkbox(label, value)
    imgui.pop_style_var()
    return result

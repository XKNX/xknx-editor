"""Checkboxes and radio buttons about the size of the text, the same size everywhere in the app."""

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


def radio_button(label: str, active: bool, *, centred: bool = False) -> bool:
    """A radio button about the size of the text; whether it was clicked. ``centred`` lines it
    up with full-height fields (buttons, inputs) on the same row; otherwise it lines up with
    plain text."""
    padding = imgui.get_style().frame_padding
    small = padding.y / 4
    if centred:
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + padding.y - small)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(padding.x, small))
    pressed = imgui.radio_button(label, active)
    imgui.pop_style_var()
    return pressed


def radio_beside_field(label: str, active: bool) -> bool:
    """A radio button followed on its row by a full-height field (an input, a drop-down): both
    centred on the row, the field drawn next. Whether it was clicked."""
    # In a group, so that the row starts at its top, not where the lowered radio button does.
    imgui.begin_group()
    pressed = radio_button(label, active, centred=True)
    imgui.end_group()
    imgui.same_line()
    return pressed

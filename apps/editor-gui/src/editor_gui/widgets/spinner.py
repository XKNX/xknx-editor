"""The editor's one loading animation: a short arc turning on a faint ring, beside a text that
says what loads. The arc has the link color (the accent blue of Windows on light themes, a lighter
blue on dark ones) and the ring the text color, faint, so both show on any background."""

from __future__ import annotations

import math

from imgui_bundle import imgui, imspinner
from imgui_bundle.imgui import internal

from editor_gui.widgets.dpi import px

# Radius and line width of the ring at 100 % scaling.
_RADIUS = 6.0
_THICKNESS = 2.0
# How much of the text color the ring shows.
_RING_ALPHA = 0.18
# How fast the arc turns, in radians per second: once a second, as the Windows spinner does.
_SPEED = 2 * math.pi


def spinner(str_id: str) -> None:
    """The loading animation, as an item on the current line, its ring centred on the line's
    text (a text before it or after it)."""
    style = imgui.get_style()
    # imspinner lays the ring out with the frame padding above and below it.
    height = 2 * (px(_RADIUS) + style.frame_padding.y)
    imgui.set_cursor_pos_y(
        imgui.get_cursor_pos_y() + (imgui.get_text_line_height() - height) / 2
    )
    arc = style.color_(imgui.Col_.text_link)
    text = style.color_(imgui.Col_.text)
    ring = imgui.ImVec4(text.x, text.y, text.z, _RING_ALPHA)
    imspinner.spinner_ang(
        str_id,
        px(_RADIUS),
        px(_THICKNESS),
        color=imgui.ImColor(arc),
        bg=imgui.ImColor(ring),
        speed=_SPEED,
    )


def loading(str_id: str, text: str) -> None:
    """The loading animation with ``text`` (what loads) after it, the ring centred on it."""
    top = imgui.get_cursor_pos_y()
    spinner(str_id)
    imgui.same_line()
    # The ring set the line's text a frame padding down; the text goes on the line's top.
    offset = internal.get_current_window().dc.curr_line_text_base_offset
    imgui.set_cursor_pos_y(top - offset)
    imgui.text_disabled(text)

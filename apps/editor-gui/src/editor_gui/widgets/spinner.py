"""The editor's one loading animation, a short arc turning on a faint ring beside a text that says
what loads, and the bar of a task whose total is not known yet.

The arc has the link color (the accent blue of Windows on light themes, a lighter blue on dark
ones) and the ring the text color, faint, so both show on any background.

Each is drawn in a small child window of its own: imgui gives it a draw list of its own, so in
the browser view a turn of the arc sends that small list again, not the whole window around it
(the browser view sends only the draw lists that changed). They move by the animation clock,
in steps in the browser view (see ``widgets.animation``)."""

from __future__ import annotations

import math

from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.widgets.animation import animation_time
from editor_gui.widgets.dpi import px

# Radius and line width of the ring at 100 % scaling.
_RADIUS = 6.0
_THICKNESS = 2.0
# How much of the text color the ring shows.
_RING_ALPHA = 0.18
# How fast the arc turns, in radians per second: once a second, as the Windows spinner does.
_SPEED = 2 * math.pi
# The length of the arc: half the ring.
_ARC = math.pi
# A child window that only shows what is drawn in it.
_OWN_LIST_FLAGS = (
    imgui.WindowFlags_.no_scrollbar
    | imgui.WindowFlags_.no_scroll_with_mouse
    | imgui.WindowFlags_.no_background
    | imgui.WindowFlags_.no_inputs
    | imgui.WindowFlags_.no_nav
)


def spinner(str_id: str) -> None:
    """The loading animation, as an item on the current line, its ring centred on the line's
    text (a text before it or after it)."""
    style = imgui.get_style()
    radius = px(_RADIUS)
    # The ring with the frame padding above and below it.
    size = imgui.ImVec2(2 * radius, 2 * (radius + style.frame_padding.y))
    imgui.set_cursor_pos_y(
        imgui.get_cursor_pos_y() + (imgui.get_text_line_height() - size.y) / 2
    )
    imgui.begin_child(str_id, size, 0, _OWN_LIST_FLAGS)
    low = imgui.get_window_pos()
    centre = imgui.ImVec2(low.x + size.x / 2, low.y + size.y / 2)
    arc = style.color_(imgui.Col_.text_link)
    text = style.color_(imgui.Col_.text)
    ring = imgui.ImVec4(text.x, text.y, text.z, _RING_ALPHA)
    thickness = px(_THICKNESS)
    # The line's middle inside the ring's radius, so the arc stays within its room.
    middle = radius - thickness / 2
    draw = imgui.get_window_draw_list()
    draw.add_circle(centre, middle, imgui.get_color_u32(ring), 0, thickness)
    start = animation_time() * _SPEED
    draw.path_arc_to(centre, middle, start, start + _ARC)
    draw.path_stroke(imgui.get_color_u32(arc), thickness)
    imgui.end_child()


def loading(str_id: str, text: str) -> None:
    """The loading animation with ``text`` (what loads) after it, the ring centred on it. Beside
    a button (on the same line) the text is at the height of the button's label."""
    top = imgui.get_cursor_pos_y()
    # How far down the line's texts sit: a button's label by its frame padding, else none.
    offset = internal.get_current_window().dc.curr_line_text_base_offset
    imgui.set_cursor_pos_y(top + offset)
    spinner(str_id)
    imgui.same_line()
    # Drawn that far down from the line's top, as the labels are.
    imgui.set_cursor_pos_y(top)
    imgui.text_disabled(text)


def busy_bar(str_id: str, size: imgui.ImVec2, text: str = "") -> None:
    """The bar of a task whose total is not known yet: imgui's bar that moves back and forth,
    ``size`` as for ``imgui.progress_bar`` (a width of 0: the rest of the line, less than 0: that
    much less; a height of 0: a field's), with ``text`` on it."""
    avail = imgui.get_content_region_avail().x
    width = size.x if size.x > 0 else avail + size.x
    height = size.y if size.y > 0 else imgui.get_frame_height()
    imgui.begin_child(str_id, imgui.ImVec2(width, height), 0, _OWN_LIST_FLAGS)
    imgui.progress_bar(-animation_time(), imgui.ImVec2(width, height), text)
    imgui.end_child()

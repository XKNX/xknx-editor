"""Zooming and moving a scrolling diagram: Ctrl/Cmd+wheel zooms at the cursor, dragging moves it."""

from __future__ import annotations

from imgui_bundle import imgui

ZOOM_MIN = 0.1
ZOOM_MAX = 2.5
# A press that moves less than this (pixels) is a click, not a drag.
_DRAG_START = 4.0


def zoom_with_wheel(zoom: float) -> float:
    """The new zoom after Ctrl (Windows/Linux) or Cmd (macOS, where Ctrl+scroll is the OS screen
    zoom) + wheel over the current window, keeping the point under the cursor in place."""
    io = imgui.get_io()
    if not (
        imgui.is_window_hovered()
        and (io.key_ctrl or io.key_super)
        and io.mouse_wheel != 0.0
    ):
        return zoom
    new = max(ZOOM_MIN, min(ZOOM_MAX, zoom * (1.1 if io.mouse_wheel > 0 else 0.9)))
    if new != zoom:
        # The content scales by new/zoom, so the scroll offset to the cursor scales the same way.
        win = imgui.get_window_pos()
        content_x = imgui.get_scroll_x() + io.mouse_pos.x - win.x
        content_y = imgui.get_scroll_y() + io.mouse_pos.y - win.y
        ratio = new / zoom
        imgui.set_scroll_x(imgui.get_scroll_x() + content_x * (ratio - 1))
        imgui.set_scroll_y(imgui.get_scroll_y() + content_y * (ratio - 1))
    return new


class DragToMove:
    """Moves the current window's content while the left button is dragged across it, and tells
    a click (a press and release without moving) from a drag."""

    def __init__(self) -> None:
        self._pressed = False
        self._dragging = False

    def update(self) -> bool:
        """Call once per frame inside the window; returns whether the left button was clicked."""
        io = imgui.get_io()
        if imgui.is_mouse_clicked(0) and imgui.is_window_hovered():
            self._pressed = True
            self._dragging = False
        if not self._pressed:
            return False
        if imgui.is_mouse_down(0):
            delta = imgui.get_mouse_drag_delta(0, 0.0)
            if not self._dragging and (
                abs(delta.x) > _DRAG_START or abs(delta.y) > _DRAG_START
            ):
                self._dragging = True
            if self._dragging:
                imgui.set_scroll_x(imgui.get_scroll_x() - io.mouse_delta.x)
                imgui.set_scroll_y(imgui.get_scroll_y() - io.mouse_delta.y)
            return False
        self._pressed = False
        return not self._dragging

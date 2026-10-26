"""Shared by the views of the Bus Diagram: zooming at the cursor (Ctrl/Cmd+wheel) to sharp font sizes,
moving by dragging, and the colours."""

from __future__ import annotations

import colorsys

from imgui_bundle import imgui

from editor_gui.widgets.backgrounds import content_background

ZOOM_MIN = 0.1
ZOOM_MAX = 2.5
# A press that moves less than this (pixels) is a click, not a drag.
_DRAG_START = 4.0


def crisp_zoom(zoom: float) -> float:
    """``zoom`` adjusted so that the font lands on a whole pixel size: text drawn at a fractional
    size is scaled and looks blurry."""
    size = imgui.get_font_size()
    return max(1.0, round(size * zoom)) / size


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


class Palette:
    """The diagrams' colours for the current theme, with a hue per group (main group, area or
    building) for the parts belonging to it."""

    def __init__(self) -> None:
        bg = content_background()
        self.light = 0.2126 * bg.x + 0.7152 * bg.y + 0.0722 * bg.z >= 0.5
        style = imgui.get_style()
        self._text = style.color_(imgui.Col_.text)
        self._muted = style.color_(imgui.Col_.text_disabled)
        self.text = imgui.get_color_u32(self._text)
        self.muted = imgui.get_color_u32(self._muted)
        if self.light:
            self.box = imgui.get_color_u32(imgui.ImVec4(1.0, 1.0, 1.0, 1.0))
            self.box_outside = imgui.get_color_u32(imgui.ImVec4(0.93, 0.94, 0.95, 1.0))
            self.border = imgui.get_color_u32(imgui.ImVec4(0.47, 0.51, 0.57, 1.0))
            self.border_outside = imgui.get_color_u32(
                imgui.ImVec4(0.75, 0.77, 0.8, 1.0)
            )
        else:
            self.box = imgui.get_color_u32(imgui.ImVec4(0.17, 0.18, 0.2, 1.0))
            self.box_outside = imgui.get_color_u32(imgui.ImVec4(0.13, 0.14, 0.15, 1.0))
            self.border = imgui.get_color_u32(imgui.ImVec4(0.45, 0.48, 0.53, 1.0))
            self.border_outside = imgui.get_color_u32(
                imgui.ImVec4(0.3, 0.32, 0.35, 1.0)
            )
        self._hues: dict[int, tuple[float, float, float]] = {}

    def hue(self, main: int, alpha: float) -> int:
        rgb = self._hues.get(main)
        if rgb is None:
            lightness = 0.42 if self.light else 0.64
            # Starting at blue; steps of about 50 degrees keep neighbouring groups apart.
            rgb = colorsys.hls_to_rgb((0.58 + main * 0.137) % 1.0, lightness, 0.62)
            self._hues[main] = rgb
        return imgui.get_color_u32(imgui.ImVec4(rgb[0], rgb[1], rgb[2], alpha))

    def text_alpha(self, alpha: float) -> int:
        return imgui.get_color_u32(
            imgui.ImVec4(self._text.x, self._text.y, self._text.z, self._text.w * alpha)
        )

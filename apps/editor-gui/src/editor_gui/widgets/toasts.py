"""Short messages at the bottom right of the app: a confirmation ("Copied") in green for a moment,
a warning (an edit refused, and why) in an orange box for longer, an error in a red box. A click
(or tap) on one closes it at once."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count

from imgui_bundle import imgui

from editor_gui.color import themed
from editor_gui.widgets.dpi import px

# How many show at once (the newest), and how wide a line of one gets before it wraps.
_SHOWN = 5
_WRAP = 420.0

_keys = count()


@dataclass
class Toast:
    text: str
    # "success", "warning", "error" or "critical".
    level: str
    expires: float
    key: int = field(default_factory=lambda: next(_keys))


def _boxed(level: str) -> tuple[imgui.ImVec4, imgui.ImVec4] | None:
    """The box and text colours of a toast of ``level``; None: plain green text."""
    if level in ("error", "critical"):
        return imgui.ImVec4(0.8, 0.2, 0.2, 1.0), imgui.ImVec4(1.0, 1.0, 1.0, 1.0)
    if level == "success":
        return None
    return imgui.ImVec4(0.96, 0.6, 0.12, 1.0), imgui.ImVec4(0.08, 0.06, 0.02, 1.0)


class Toasts:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._items: list[Toast] = []

    def add(self, text: str, level: str, seconds: float) -> None:
        """Show ``text`` for ``seconds``."""
        self._items.append(Toast(text, level, self._clock() + seconds))

    @property
    def items(self) -> list[Toast]:
        """The toasts shown (or still to show), oldest first."""
        return list(self._items)

    def render(self) -> None:
        now = self._clock()
        self._items = [t for t in self._items if t.expires > now]
        if not self._items:
            return
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(
            imgui.ImVec2(
                viewport.work_pos.x + viewport.work_size.x - px(12.0),
                viewport.work_pos.y + viewport.work_size.y - px(12.0),
            ),
            imgui.Cond_.always,
            imgui.ImVec2(1.0, 1.0),
        )
        imgui.set_next_window_bg_alpha(0.9)
        flags = (
            imgui.WindowFlags_.no_decoration
            | imgui.WindowFlags_.no_saved_settings
            | imgui.WindowFlags_.always_auto_resize
            | imgui.WindowFlags_.no_focus_on_appearing
            | imgui.WindowFlags_.no_nav
        )
        # Narrower on a small screen (a phone), so a toast never runs past its edge.
        style = imgui.get_style()
        room = (
            viewport.work_size.x
            - px(24.0)
            - (style.window_padding.x + style.frame_padding.x) * 2
        )
        wrap = max(px(120.0), min(px(_WRAP), room))
        if imgui.begin("##toasts", None, flags)[0]:
            for toast in self._items[-_SHOWN:]:
                if self._render_one(toast, wrap):
                    self._items.remove(toast)
        imgui.end()

    @staticmethod
    def _render_one(toast: Toast, wrap: float) -> bool:
        """Draw ``toast``, its lines at most ``wrap`` wide; True when it was clicked (to
        close)."""
        pad = imgui.get_style().frame_padding
        size = imgui.calc_text_size(toast.text, wrap_width=wrap)
        start = imgui.get_cursor_screen_pos()
        end = imgui.ImVec2(start.x + size.x + pad.x * 2, start.y + size.y + pad.y * 2)
        colors = _boxed(toast.level)
        if colors is not None:
            imgui.get_window_draw_list().add_rect_filled(
                start,
                end,
                imgui.get_color_u32(colors[0]),
                imgui.get_style().frame_rounding,
            )
        text_color = (
            colors[1]
            if colors is not None
            else themed(
                imgui.ImVec4(0.45, 0.8, 0.5, 1.0), imgui.ImVec4(0.1, 0.5, 0.2, 1.0)
            )
        )
        imgui.set_cursor_screen_pos(imgui.ImVec2(start.x + pad.x, start.y + pad.y))
        imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + wrap)
        imgui.text_colored(text_color, toast.text)
        imgui.pop_text_wrap_pos()
        # The whole box takes the click.
        imgui.set_cursor_screen_pos(start)
        imgui.invisible_button(
            f"##toast{toast.key}", imgui.ImVec2(end.x - start.x, end.y - start.y)
        )
        if imgui.is_item_hovered():
            imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
        return imgui.is_item_clicked()

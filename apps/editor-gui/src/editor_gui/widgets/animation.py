"""The clock of the editor's animations (spinners, progress bars without a total, a flashing
status dot).

On the desktop it runs smoothly. In the browser view every change of a window is sent to the
browser, so there it moves in steps, a few a second: an animation then changes a window at most
that often, however fast the frames come (60 a second while the user acts)."""

from __future__ import annotations

import math

from imgui_bundle import imgui

from editor_gui import web

# Steps per second of an animation in the browser view.
WEB_STEPS = 8.0


def animation_time() -> float:
    """The time in seconds animations are drawn at: imgui's time, in the browser view in steps
    of ``1 / WEB_STEPS`` seconds."""
    now = imgui.get_time()
    if web.active_hub is None:
        return now
    return math.floor(now * WEB_STEPS) / WEB_STEPS

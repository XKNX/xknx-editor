"""The clock of the editor's animations (spinners, progress bars without a total, a flashing
status dot).

On the desktop it runs smoothly. In the browser view every change of a window is sent to the
browser, so there it moves in steps, twice a second: an animation then changes a window at most
that often, however fast the frames come (60 a second while the user acts). Each step moves the
clock on by an eighth of a second only, so an animation makes the same small moves as on the
desktop, more slowly: a bar or spinner that goes round once a second would otherwise stand still
or jump between two places."""

from __future__ import annotations

import math

from imgui_bundle import imgui

from editor_gui import web

# Steps per second of an animation in the browser view, and how far each moves its clock on.
WEB_STEPS = 2.0
WEB_STEP_SECONDS = 0.125


def animation_time() -> float:
    """The time in seconds animations are drawn at: imgui's time, in the browser view moving on
    by ``WEB_STEP_SECONDS`` ``WEB_STEPS`` times a second."""
    now = imgui.get_time()
    if web.active_hub is None:
        return now
    return math.floor(now * WEB_STEPS) * WEB_STEP_SECONDS

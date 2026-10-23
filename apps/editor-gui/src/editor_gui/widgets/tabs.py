"""Tab bars the browser view can scroll with a finger.

:func:`begin_tab_bar` is ``imgui.begin_tab_bar`` that remembers the tab bar for one frame, so a
sideways swipe on it moves its tabs (see :mod:`editor_gui.web.touch`). While the app is used with
a finger, the small arrow buttons that move the tabs are left out: the swipe does that."""

from __future__ import annotations

from imgui_bundle import imgui

from editor_gui import web

# The ids of the tab bars begun in the current and the previous frame, with the frame.
_shown: dict[int, int] = {}


def begin_tab_bar(str_id: str, flags: int = 0) -> bool:
    """``imgui.begin_tab_bar``, without the arrow buttons while a finger is used."""
    frame = imgui.get_frame_count()
    _shown[imgui.get_id(str_id)] = frame
    if len(_shown) > 64:
        for tab_bar_id in [i for i, seen in _shown.items() if seen < frame - 1]:
            del _shown[tab_bar_id]
    if web.touch_used():
        flags |= imgui.TabBarFlags_.no_tab_list_scrolling_buttons
    return imgui.begin_tab_bar(str_id, flags)


def shown_tab_bar_ids() -> list[int]:
    """The ids of the tab bars :func:`begin_tab_bar` began in the last two frames."""
    frame = imgui.get_frame_count()
    return [tab_bar_id for tab_bar_id, seen in _shown.items() if seen >= frame - 1]

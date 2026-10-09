"""Shared filter/search box widget used across all panels (trees, monitor, catalog, parameters).

Renders a full-width hint input followed by a small clear button, and returns the (possibly
cleared) filter text — so every list/search field in the app looks and behaves the same.
"""

from __future__ import annotations

from imgui_bundle import imgui

from editor_gui.widgets.dpi import px


def filter_box(widget_id: str, hint: str, text: str) -> str:
    """Draw a filter input with a clear button; return the current text."""
    btn = imgui.get_frame_height()
    avail = imgui.get_content_region_avail().x
    imgui.set_next_item_width(max(avail - btn - px(4.0), 1.0))
    _, text = imgui.input_text_with_hint(widget_id, hint, text)
    imgui.same_line(0.0, px(4.0))
    imgui.begin_disabled(not text)
    if imgui.button(f"x##{widget_id}_clear", imgui.ImVec2(btn, btn)):
        text = ""
    imgui.end_disabled()
    return text

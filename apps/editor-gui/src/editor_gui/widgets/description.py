"""A small note icon for a description that is too long to show in a list row; hovering it
shows the description."""

from __future__ import annotations

from imgui_bundle import imgui

from editor_gui.widgets.dpi import px

# FontAwesome 4 "comment-o" (the icon font the app loads).
ICON_DESCRIPTION = ""
# Width the description wraps at in its tooltip, at 100 % scaling.
_WRAP_WIDTH = 420.0


def description_icon_width() -> float:
    return imgui.calc_text_size(ICON_DESCRIPTION).x


def description_icon(description: str, *, x: float | None = None) -> bool:
    """The icon on the line of the previous item, after it or at screen position ``x``, showing
    ``description`` while hovered; returns whether it is hovered."""
    imgui.same_line()
    if x is not None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(x, imgui.get_cursor_screen_pos().y))
    pos = imgui.get_cursor_screen_pos()
    size = imgui.calc_text_size(ICON_DESCRIPTION)
    # The rows the icon sits on claim the hover themselves, so test its rectangle.
    hovered = imgui.is_window_hovered(
        imgui.HoveredFlags_.allow_when_blocked_by_active_item
    ) and imgui.is_mouse_hovering_rect(
        pos, imgui.ImVec2(pos.x + size.x, pos.y + imgui.get_text_line_height())
    )
    color = imgui.Col_.text if hovered else imgui.Col_.text_disabled
    imgui.text_colored(imgui.get_style().color_(color), ICON_DESCRIPTION)
    if hovered:
        imgui.begin_tooltip()
        imgui.push_text_wrap_pos(px(_WRAP_WIDTH))
        imgui.text_unformatted(description)
        imgui.pop_text_wrap_pos()
        imgui.end_tooltip()
    return hovered

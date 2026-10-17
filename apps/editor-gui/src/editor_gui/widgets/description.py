"""A device/space description shown after its row: a short one inline, a longer one behind a small
note icon whose tooltip shows the full text."""

from __future__ import annotations

from imgui_bundle import imgui

from editor_gui.widgets.dpi import px

# FontAwesome 4 "comment-o" (the icon font the app loads).
ICON_DESCRIPTION = ""
# Width the description wraps at in its tooltip, at 100 % scaling.
_WRAP_WIDTH = 420.0
# A description this short or shorter is shown inline; a longer one (or one of several lines)
# goes behind the note icon.
MIN_DESCRIPTION_LENGTH = 15


def shows_description_icon(description: str | None) -> bool:
    """Whether ``description`` is shown behind the note icon (vs inline): when it is long or has
    more than one line."""
    if description is None:
        return False
    text = description.strip()
    return len(text) > MIN_DESCRIPTION_LENGTH or "\n" in text


def render_description(description: str | None) -> bool:
    """Append ``description`` after the previous item: a short one inline (dimmed), a longer or
    multi-line one behind a hover icon. Returns whether the icon is hovered (always ``False`` for the inline form)."""
    if not description or not description.strip():
        return False
    if shows_description_icon(description):
        return description_icon(description)
    imgui.same_line()
    imgui.text_disabled(description)
    return False


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
        description_tooltip(description)
    return hovered


def description_tooltip(description: str) -> None:
    """The tooltip of the description icon: ``description``, wrapped."""
    imgui.begin_tooltip()
    imgui.push_text_wrap_pos(px(_WRAP_WIDTH))
    imgui.text_unformatted(description)
    imgui.pop_text_wrap_pos()
    imgui.end_tooltip()

"""Buttons that show an icon of the app's icon font (FontAwesome 4) with the name as tooltip.

A copy button sits right of the text it copies, at the top of a text of several lines, so every
copy action of the app looks and sits alike.
"""

from __future__ import annotations

from imgui_bundle import imgui

from editor_gui.widgets.strings import S

ICON_COPY = ""
ICON_CLEAR = ""
ICON_DOWNLOAD = ""
ICON_ERROR = ""
ICON_WARNING = ""
ICON_INFO = ""
ICON_DEBUG = ""


def icon_button_width(icon: str) -> float:
    """The width of an icon button (the icon and the frame's padding)."""
    return imgui.calc_text_size(icon).x + 2 * imgui.get_style().frame_padding.x


def icon_button(icon: str, str_id: str, tooltip: str, *, enabled: bool = True) -> bool:
    """A button as high as a text field with ``icon`` on it and ``tooltip`` when hovered;
    whether it was pressed while enabled."""
    imgui.begin_disabled(not enabled)
    pressed = imgui.button(f"{icon}##{str_id}")
    imgui.end_disabled()
    if enabled:
        imgui.set_item_tooltip(tooltip)
    return pressed and enabled


def copy_button(str_id: str, text: str, tooltip: str | None = None) -> bool:
    """A copy button that puts ``text`` on the clipboard; whether it did (``text`` not empty)."""
    if icon_button(ICON_COPY, f"copy_{str_id}", tooltip or S.COPY) and text:
        imgui.set_clipboard_text(text)
        return True
    return False


def text_with_copy_button(
    str_id: str, text: str, *, disabled: bool = True, aligned: bool = False
) -> bool:
    """``text`` (wrapped, grey unless ``disabled`` is False) with a copy button right of it;
    whether it was copied. ``aligned``: the text is on the line of a label beside it that is
    aligned to a field (as a setting's value)."""
    style = imgui.get_style()
    width = icon_button_width(ICON_COPY)
    if aligned:
        imgui.align_text_to_frame_padding()
    wrap = imgui.get_cursor_pos_x() + max(
        imgui.get_content_region_avail().x - width - style.item_spacing.x, width
    )
    imgui.push_text_wrap_pos(wrap)
    if disabled:
        imgui.text_disabled(text)
    else:
        imgui.text(text)
    imgui.pop_text_wrap_pos()
    # Beside the first line of the text (a wrapped text's item ends right of its widest line),
    # the icon on the text's line: the text keeps the distance of any text to what is above it.
    imgui.same_line()
    if not aligned:
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - style.frame_padding.y)
    return copy_button(str_id, text)

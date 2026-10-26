"""A device/space description shown after its row: a short one inline, a longer one behind a small
note icon whose tooltip shows the full text. Before it, a device commissioned with KNX Data Secure
shows a shield."""

from __future__ import annotations

from collections.abc import Callable, Collection

from imgui_bundle import imgui

from editor_gui.widgets.dpi import px
from editor_gui.widgets.tree import same_row

# FontAwesome 4 "comment-o" (the icon font the app loads).
ICON_DESCRIPTION = ""
# FontAwesome 4 "shield": the device is commissioned with KNX Data Secure.
ICON_SECURE = ""
# Width the description wraps at in its tooltip, at 100 % scaling.
_WRAP_WIDTH = 420.0
# A description this short or shorter is shown inline; a longer one (or one of several lines)
# goes behind the note icon.
MIN_DESCRIPTION_LENGTH = 20


# The devices commissioned with KNX Data Secure (node ids) and the shield's tooltip, from the
# project (see set_secure_devices).
_secure_devices: Callable[[], Collection[int]] = frozenset
_secure_tooltip: Callable[[], str] = str


def set_secure_devices(
    devices: Callable[[], Collection[int]], tooltip: Callable[[], str]
) -> None:
    """Where the rows learn which devices are commissioned with KNX Data Secure, and the text the
    shield shows while hovered."""
    global _secure_devices, _secure_tooltip
    _secure_devices, _secure_tooltip = devices, tooltip


def is_secure_device(node_id: int) -> bool:
    return node_id in _secure_devices()


def render_device_marks(node_id: int, description: str | None) -> bool:
    """After a device's row: the shield of a device commissioned with KNX Data Secure, then its
    description (see :func:`render_description`). Returns whether an icon is hovered."""
    secure_hovered = is_secure_device(node_id) and secure_icon()
    return render_description(description) or secure_hovered


def secure_tooltip() -> str:
    """What the shield shows while hovered."""
    return _secure_tooltip()


def secure_icon_width() -> float:
    return imgui.calc_text_size(ICON_SECURE).x


def secure_icon(*, x: float | None = None) -> bool:
    """The shield on the line of the previous item, after it or at screen position ``x``;
    returns whether it is hovered."""
    hovered = _hover_icon(ICON_SECURE, x)
    if hovered:
        imgui.set_tooltip(_secure_tooltip())
    return hovered


def shows_description_icon(description: str | None) -> bool:
    """Whether ``description`` is shown behind the note icon (vs inline): when it is long or has
    more than one line."""
    if description is None:
        return False
    text = description.strip()
    return len(text) > MIN_DESCRIPTION_LENGTH or "\n" in text


def render_description(description: str | None) -> bool:
    """Append ``description`` after the previous item: a short one inline (dimmed), a longer or
    multi-line one behind a hover icon. Spaces and line breaks around it are left out. Returns
    whether the icon is hovered (always ``False`` for the inline form)."""
    text = (description or "").strip()
    if not text:
        return False
    if shows_description_icon(text):
        return description_icon(text)
    with same_row():
        imgui.text_disabled(text)
    return False


def description_icon_width() -> float:
    return imgui.calc_text_size(ICON_DESCRIPTION).x


def description_icon(description: str, *, x: float | None = None) -> bool:
    """The icon on the line of the previous item, after it or at screen position ``x``, showing
    ``description`` while hovered; returns whether it is hovered."""
    hovered = _hover_icon(ICON_DESCRIPTION, x)
    if hovered:
        description_tooltip(description.strip())
    return hovered


def _hover_icon(icon: str, x: float | None) -> bool:
    """``icon`` on the line of the previous item, after it or at screen position ``x``, dimmed and
    in the text colour while hovered; returns whether it is hovered."""
    with same_row():
        if x is not None:
            imgui.set_cursor_screen_pos(
                imgui.ImVec2(x, imgui.get_cursor_screen_pos().y)
            )
        pos = imgui.get_cursor_screen_pos()
        size = imgui.calc_text_size(icon)
        # The rows the icon sits on claim the hover themselves, so test its rectangle.
        hovered = imgui.is_window_hovered(
            imgui.HoveredFlags_.allow_when_blocked_by_active_item
        ) and imgui.is_mouse_hovering_rect(
            pos, imgui.ImVec2(pos.x + size.x, pos.y + imgui.get_text_line_height())
        )
        color = imgui.Col_.text if hovered else imgui.Col_.text_disabled
        imgui.text_colored(imgui.get_style().color_(color), icon)
    return hovered


def description_tooltip(description: str) -> None:
    """The tooltip of the description icon: ``description``, wrapped."""
    wrapped_tooltip(description)


def wrapped_tooltip(text: str) -> None:
    """A tooltip showing ``text`` wrapped at the description width (for texts that may hold a
    long description)."""
    imgui.begin_tooltip()
    imgui.push_text_wrap_pos(px(_WRAP_WIDTH))
    imgui.text_unformatted(text)
    imgui.pop_text_wrap_pos()
    imgui.end_tooltip()

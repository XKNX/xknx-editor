"""Modal dialogs and tool windows with one look.

Every dialog opens centred on the main window and stays centred while its content changes its size
(wrapped text settles over the first frames, content loads later), until the user moves it. It has
a title bar and does not remember a position it was moved to. Its buttons sit in one row of equal-width buttons: the actions left-aligned, the
button that dismisses the dialog (Cancel, Close, No, ...) right-aligned and triggered by Escape.
Tool windows open centred too while they float; they can be docked and keep their dock place and
size.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.widgets.backgrounds import window_border
from editor_gui.widgets.dpi import px, px_vec2

_MIN_BUTTON_WIDTH = 90.0
# Off by more than this (pixels) from where it was placed, a dialog was moved by the user.
_MOVED_TOLERANCE = 1.5


@dataclass
class _Placement:
    """Where an open dialog is: its size, whether it is off centre and whether the user moved it."""

    size: tuple[float, float]
    off_centre: bool = False
    moved: bool = False


# The open dialogs' placements, by dialog id.
_placements: dict[str, _Placement] = {}


def _center_next_window(title: str | None = None) -> None:
    """Centre the next window when it appears and, for the dialog ``title``, again whenever it got
    off centre because its size changed, unless the user moved it."""
    placement = _placements.get(title) if title is not None else None
    recentre = placement is not None and placement.off_centre and not placement.moved
    imgui.set_next_window_pos(
        imgui.get_main_viewport().get_center(),
        imgui.Cond_.always if recentre else imgui.Cond_.appearing,
        imgui.ImVec2(0.5, 0.5),
    )


def _near(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= _MOVED_TOLERANCE and abs(a[1] - b[1]) <= _MOVED_TOLERANCE


def _track_placement(title: str) -> None:
    """Note the current dialog's placement after it began: off centre when its size changed since
    the last frame, moved when it is neither centred for its current nor for its last size."""
    pos, size = imgui.get_window_pos(), imgui.get_window_size()
    center = imgui.get_main_viewport().get_center()
    current = (size.x, size.y)
    centred_now = _near((pos.x, pos.y), (center.x - size.x / 2, center.y - size.y / 2))
    placement = _placements.get(title)
    if placement is None or imgui.is_window_appearing():
        # A dialog fitting its content is not placed yet in the frame it appears.
        _placements[title] = _Placement(current, off_centre=not centred_now)
        return
    centred_before = _near(
        (pos.x, pos.y),
        (center.x - placement.size[0] / 2, center.y - placement.size[1] / 2),
    )
    if not centred_now and not centred_before:
        placement.moved = True
    placement.off_centre = not centred_now
    placement.size = current


@contextmanager
def floating_window_border() -> Generator[None]:
    """Windows begun inside get a border that stands out from their background, also in a theme
    without window borders (Microsoft Style), while the fields in them keep the theme's lighter
    one."""
    style = imgui.get_style()
    imgui.push_style_color(imgui.Col_.border, window_border())
    imgui.push_style_var(
        imgui.StyleVar_.window_border_size, max(style.window_border_size, 1.0)
    )
    try:
        yield
    finally:
        imgui.pop_style_var()
        imgui.pop_style_color()


def begin_dialog(
    title: str,
    *,
    width: float | None = None,
    height: float | None = None,
    flags: int = 0,
) -> bool:
    """Begin the modal dialog ``title`` (opened with ``imgui.open_popup(title)``).

    Call :func:`end_dialog` when this returns ``True``. It stays centred while its size changes until
    the user moves it. ``width`` (unscaled pixels) fixes the
    width and the height follows the content; with ``height`` as well the dialog starts at that
    size and can be resized. Without either it fits its content.
    """
    _center_next_window(title)
    if width is not None and height is not None:
        imgui.set_next_window_size(px_vec2(width, height), imgui.Cond_.appearing)
    elif width is not None:
        imgui.set_next_window_size(px_vec2(width, 0.0), imgui.Cond_.always)
    else:
        flags |= imgui.WindowFlags_.always_auto_resize
    flags |= imgui.WindowFlags_.no_saved_settings
    with floating_window_border():
        opened = imgui.begin_popup_modal(title, None, flags)[0]
    if opened:
        _track_placement(title)
    else:
        _placements.pop(title, None)
    return opened


def end_dialog() -> None:
    imgui.end_popup()


def focus_when_appearing() -> None:
    """Give the next input the keyboard focus when its dialog appears, so typing goes into it at
    once (e.g. a password asked for)."""
    if imgui.is_window_appearing():
        imgui.set_keyboard_focus_here()


@contextmanager
def outlined_tabs() -> Generator[None]:
    """Outline the tabs of the tab bars begun inside, so they read as tabs in every theme (light
    themes draw the selected tab in the window colour); their content goes in :func:`tab_page`."""
    imgui.push_style_var(imgui.StyleVar_.tab_border_size, 1.0)
    try:
        yield
    finally:
        imgui.pop_style_var()


@contextmanager
def tab_page(str_id: str) -> Generator[None]:
    """A bordered page right below the tabs for the selected tab's content, as tall as the content,
    in the window's colour like the selected tab of light themes."""
    imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - imgui.get_style().item_spacing.y)
    imgui.push_style_color(imgui.Col_.child_bg, imgui.ImVec4(0.0, 0.0, 0.0, 0.0))
    imgui.begin_child(
        str_id,
        imgui.ImVec2(0.0, 0.0),
        imgui.ChildFlags_.borders
        | imgui.ChildFlags_.auto_resize_y
        | imgui.ChildFlags_.always_use_window_padding,
    )
    imgui.pop_style_color()
    try:
        yield
    finally:
        imgui.end_child()


def dialog_buttons(*labels: str, enabled: Sequence[bool] | None = None) -> int | None:
    """A row of equal-width buttons below a separator, the primary one first.

    The last label is the button that dismisses the dialog: it sits at the right edge and Escape
    triggers it while the dialog has focus. The others sit left-aligned. Returns the index of the
    clicked button, or ``None``.
    """
    style = imgui.get_style()
    width = max(
        px(_MIN_BUTTON_WIDTH),
        *(
            imgui.calc_text_size(label, hide_text_after_double_hash=True).x
            + 2 * style.frame_padding.x
            for label in labels
        ),
    )
    imgui.spacing()
    imgui.separator()
    imgui.spacing()
    row_x = imgui.get_cursor_pos_x()
    dismiss_x = row_x + max(0.0, imgui.get_content_region_avail().x - width)
    dismiss = len(labels) - 1
    clicked: int | None = None
    for index, label in enumerate(labels):
        if index:
            imgui.same_line()
        if index == dismiss:
            imgui.set_cursor_pos_x(max(dismiss_x, imgui.get_cursor_pos_x()))
        disabled = enabled is not None and not enabled[index]
        imgui.begin_disabled(disabled)
        if imgui.button(label, imgui.ImVec2(width, 0.0)):
            clicked = index
        imgui.end_disabled()
        if (
            index == dismiss
            and clicked is None
            and not disabled
            and imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows)
            and imgui.is_key_pressed(imgui.Key.escape, repeat=False)
        ):
            clicked = index
    return clicked


def begin_tool_window(
    title: str, p_open: bool | None, *, width: float, height: float, flags: int = 0
) -> tuple[bool, bool | None]:
    """Begin a movable, dockable, non-modal window.

    It opens centred while it floats, first at ``width`` x ``height`` (unscaled pixels). Returns
    ``imgui.begin``'s ``(expanded, open)``; call ``imgui.end()`` in any case.
    """
    _center_next_window()
    imgui.set_next_window_size(px_vec2(width, height), imgui.Cond_.first_use_ever)
    with floating_window_border():
        return imgui.begin(title, p_open, flags)

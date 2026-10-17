"""Modal dialogs and tool windows with one look.

Every dialog opens centred on the main window, has a title bar and does not remember a position it
was moved to. Its buttons sit in one row of equal-width buttons: the actions left-aligned, the
button that dismisses the dialog (Cancel, Close, No, ...) right-aligned and triggered by Escape.
Tool windows open centred too while they float; they can be docked and keep their dock place and
size.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from imgui_bundle import imgui

from editor_gui.widgets.backgrounds import window_border
from editor_gui.widgets.dpi import px, px_vec2

_MIN_BUTTON_WIDTH = 90.0


def _center_next_window() -> None:
    imgui.set_next_window_pos(
        imgui.get_main_viewport().get_center(),
        imgui.Cond_.appearing,
        imgui.ImVec2(0.5, 0.5),
    )


@contextmanager
def floating_window_border() -> Iterator[None]:
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

    Call :func:`end_dialog` when this returns ``True``. ``width`` (unscaled pixels) fixes the
    width and the height follows the content; with ``height`` as well the dialog starts at that
    size and can be resized. Without either it fits its content.
    """
    _center_next_window()
    if width is not None and height is not None:
        imgui.set_next_window_size(px_vec2(width, height), imgui.Cond_.appearing)
    elif width is not None:
        imgui.set_next_window_size(px_vec2(width, 0.0), imgui.Cond_.always)
    else:
        flags |= imgui.WindowFlags_.always_auto_resize
    flags |= imgui.WindowFlags_.no_saved_settings
    with floating_window_border():
        return imgui.begin_popup_modal(title, None, flags)[0]


def end_dialog() -> None:
    imgui.end_popup()


def focus_when_appearing() -> None:
    """Give the next input the keyboard focus when its dialog appears, so typing goes into it at
    once (e.g. a password asked for)."""
    if imgui.is_window_appearing():
        imgui.set_keyboard_focus_here()


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

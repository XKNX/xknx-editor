"""Modal dialogs and tool windows with one look.

Every dialog opens centred on the main window, at most as large as it (it scrolls then), and
stays centred while its content changes its size (wrapped text settles over the first frames,
content loads later), until the user moves it. When the screen changes its size (a browser window
resized, a phone turned), it is centred and fitted to it again. It has a title bar and does not
remember a position it was moved to. Its buttons sit in one row of equal-width buttons: the actions
left-aligned, the button that dismisses the dialog (Cancel, Close, No, ...) right-aligned and
triggered by Escape. Tool windows open centred too while they float, and are centred again on a
screen that changed its size; they can be docked and keep their dock place and size.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.widgets.backgrounds import window_border
from editor_gui.widgets.dpi import px, px_vec2

_MIN_BUTTON_WIDTH = 90.0
# The frame in which Escape last dismissed a dialog.
_escape_frame = [-1]
# Off by more than this (pixels) from where it was placed, a dialog was moved by the user.
_MOVED_TOLERANCE = 1.5
# Room a dialog leaves to each edge of a screen it does not fit on, at 100 % scaling.
_SCREEN_MARGIN = 6.0


@dataclass
class _Placement:
    """Where an open dialog is: its size, whether it is off centre and whether the user moved it,
    and the size of the screen it was placed on."""

    size: tuple[float, float]
    screen: tuple[float, float]
    off_centre: bool = False
    moved: bool = False


# The open dialogs' placements, by dialog id.
_placements: dict[str, _Placement] = {}


def _screen_size() -> tuple[float, float]:
    size = imgui.get_main_viewport().size
    return (size.x, size.y)


def _center_next_window(title: str | None = None) -> None:
    """Centre the next window when it appears and, for the dialog ``title``, again whenever it got
    off centre because its size changed, unless the user moved it, and whenever the screen changed
    its size (a browser window resized, a phone turned), also when the user moved it."""
    placement = _placements.get(title) if title is not None else None
    recentre = placement is not None and (
        (placement.off_centre and not placement.moved)
        or placement.screen != _screen_size()
    )
    imgui.set_next_window_pos(
        imgui.get_main_viewport().get_center(),
        imgui.Cond_.always if recentre else imgui.Cond_.appearing,
        imgui.ImVec2(0.5, 0.5),
    )


def _fit_screen(title: str | None = None, width: float | None = None) -> int:
    """Keep the next window within the screen (a phone): at most as large as the main window
    less a margin, scrolling what does not fit. Returns the window flags for that: a horizontal
    scroll bar once the window is as wide as the screen allows (``width`` in pixels is the width
    it is given, else the size it had)."""
    viewport = imgui.get_main_viewport()
    margin = 2 * px(_SCREEN_MARGIN)
    room = imgui.ImVec2(
        max(viewport.size.x - margin, px(160)), max(viewport.size.y - margin, px(120))
    )
    imgui.set_next_window_size_constraints(imgui.ImVec2(0.0, 0.0), room)
    placement = _placements.get(title) if title is not None else None
    if placement is not None and placement.screen != _screen_size():
        # Scrolling sideways, a window keeps the width its content had: on a screen that changed
        # its size, the content (wrapped text) fits the window again first.
        return 0
    wanted = width if width is not None else placement.size[0] if placement else 0.0
    return imgui.WindowFlags_.horizontal_scrollbar if wanted >= room.x - 1.0 else 0


def screen_room() -> float:
    """The width a dialog can have on the screen (a phone's is narrow), in pixels."""
    viewport = imgui.get_main_viewport()
    return max(viewport.size.x - 2 * px(_SCREEN_MARGIN), px(160))


def _screen_room_width() -> float:
    """The width a dialog can have on the screen, in unscaled pixels (as :func:`_fit_screen`)."""
    return screen_room() / px(1.0)


def _near(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= _MOVED_TOLERANCE and abs(a[1] - b[1]) <= _MOVED_TOLERANCE


def _track_placement(title: str) -> None:
    """Note the current dialog's placement after it began: off centre when its size changed since
    the last frame, moved when it is neither centred for its current nor for its last size. On a
    screen that changed its size it was centred again (see :func:`_center_next_window`) and counts
    as not moved."""
    pos, size = imgui.get_window_pos(), imgui.get_window_size()
    center = imgui.get_main_viewport().get_center()
    current = (size.x, size.y)
    centred_now = _near((pos.x, pos.y), (center.x - size.x / 2, center.y - size.y / 2))
    placement = _placements.get(title)
    if (
        placement is None
        or imgui.is_window_appearing()
        or placement.screen != _screen_size()
    ):
        # A dialog fitting its content is not placed yet in the frame it appears.
        _placements[title] = _Placement(
            current, _screen_size(), off_centre=not centred_now
        )
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
    size and can be resized. Without either it fits its content. On a screen narrower than
    ``width`` (a phone) the dialog gets the width there is, so its wrapped text reflows.
    """
    _center_next_window(title)
    if width is not None:
        width = min(width, _screen_room_width())
    flags |= _fit_screen(title, px(width) if width is not None else None)
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


# Drawn inside every dialog just before it ends, while it is the current popup: the browser UI
# (``editor_gui.web``) shows its file dialogs nested in the topmost open dialog this way, so that
# dialog stays open below them.
nested_content: Callable[[], None] | None = None


def end_dialog() -> None:
    if nested_content is not None:
        nested_content()
    imgui.end_popup()


def focus_when_appearing() -> None:
    """Give the next input the keyboard focus when its dialog appears, so typing goes into it at
    once (e.g. a password asked for)."""
    if imgui.is_window_appearing():
        imgui.set_keyboard_focus_here()


@contextmanager
def outlined_tabs() -> Generator[None]:
    """Outline the tabs of the tab bars begun inside, so they read as tabs in every theme (light
    themes draw the selected tab in the window colour); their content goes in :func:`tab_page`.

    The bar's own line below the tabs is left out: it is drawn in the selected tab's colour and
    reaches into the window padding, where :func:`tab_page` frames the content instead.
    """
    imgui.push_style_var(imgui.StyleVar_.tab_border_size, 1.0)
    imgui.push_style_var(imgui.StyleVar_.tab_bar_border_size, 0.0)
    try:
        yield
    finally:
        imgui.pop_style_var(2)


@contextmanager
def tab_page(str_id: str, height: float = 0.0) -> Generator[None]:
    """A framed page for the selected tab's content, right below the tabs.

    Call it first thing in the tab item. The selected tab opens into the page (no frame line below
    it), and the page has the window's colour, like the selected tab of light themes. ``height`` (in
    scaled pixels) fixes the page height so sibling tabs stay the same size; the default (0) sizes
    the page to its content.
    """
    tab_min, tab_max = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - imgui.get_style().item_spacing.y)
    imgui.push_style_color(imgui.Col_.child_bg, imgui.ImVec4(0.0, 0.0, 0.0, 0.0))
    flags = imgui.ChildFlags_.always_use_window_padding
    if height <= 0.0:
        flags |= imgui.ChildFlags_.auto_resize_y
    imgui.begin_child(str_id, imgui.ImVec2(0.0, height), flags)
    imgui.pop_style_color()
    try:
        yield
    finally:
        imgui.end_child()
        # One open path on pixel centres, like imgui's own borders: from the selected tab's right
        # border around the page to its left border. The ends reach the far edge of those border
        # pixels, so the frame meets them without a gap.
        lo, hi = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        x0, y0 = round(lo.x) + 0.5, round(lo.y) + 0.5
        x1, y1 = round(hi.x) - 0.5, round(hi.y) - 0.5
        draw = imgui.get_window_draw_list()
        draw.path_line_to(imgui.ImVec2(round(tab_max.x) - 1.0, y0))
        draw.path_line_to(imgui.ImVec2(x1, y0))
        draw.path_line_to(imgui.ImVec2(x1, y1))
        draw.path_line_to(imgui.ImVec2(x0, y1))
        draw.path_line_to(imgui.ImVec2(x0, y0))
        draw.path_line_to(imgui.ImVec2(round(tab_min.x) + 1.0, y0))
        draw.path_stroke(imgui.get_color_u32(imgui.Col_.border))


def dialog_room(minimum: float = 60.0) -> float:
    """The height a part of the current dialog may take from the cursor down, so that the dialog
    with its row of :func:`dialog_buttons` below still fits the screen; at least ``minimum``
    (unscaled pixels)."""
    style = imgui.get_style()
    screen = max(imgui.get_main_viewport().size.y - 2 * px(_SCREEN_MARGIN), px(120))
    # The gap below the part, spacing, separator and spacing above the buttons, the buttons, and
    # the bottom padding of the window.
    buttons = (
        4 * style.item_spacing.y
        + style.separator_size
        + imgui.get_frame_height()
        + style.window_padding.y
    )
    return max(px(minimum), screen - imgui.get_cursor_pos_y() - buttons)


def dialog_buttons(
    *labels: str,
    enabled: Sequence[bool] | None = None,
    rects: list[tuple[float, float, float, float]] | None = None,
) -> int | None:
    """A row of equal-width buttons below a separator, the primary one first.

    The last label is the button that dismisses the dialog: it sits at the right edge and Escape
    triggers it while the dialog has focus. The others sit left-aligned. Returns the index of the
    clicked button, or ``None``. ``rects`` receives each button's screen rectangle (x, y, width,
    height).
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
        if rects is not None:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            rects.append((low.x, low.y, high.x - low.x, high.y - low.y))
        if (
            index == dismiss
            and clicked is None
            and not disabled
            # Not while a dialog or popup opened from this one has the focus: Escape is for that.
            and imgui.is_window_focused(
                imgui.FocusedFlags_.root_and_child_windows
                | imgui.FocusedFlags_.no_popup_hierarchy
            )
            and imgui.is_key_pressed(imgui.Key.escape, repeat=False)
            # Closing a dialog focuses the one below at once, in the same frame: one press closes
            # only the first dialog it reaches.
            and imgui.get_frame_count() != _escape_frame[0]
        ):
            clicked = index
            _escape_frame[0] = imgui.get_frame_count()
    return clicked


# The size of the screen each floating window was last shown on, by window title.
_window_screens: dict[str, tuple[float, float]] = {}


def follow_screen(title: str, width: float, height: float = 0.0) -> int:
    """Keep the floating window ``title`` that is begun next within the screen (as a dialog): at
    most as large as the main window, and when the screen changed its size since its last frame
    (a browser window resized, a phone turned) centred again at its size of ``width`` x ``height``
    (unscaled pixels; a height of 0 fits the content). Call it after the window's own placement and
    pass the flags it returns to ``imgui.begin``. A docked window stays where it is docked."""
    screen = _screen_size()
    changed = _window_screens.get(title, screen) != screen
    _window_screens[title] = screen
    flags = _fit_screen(width=px(width))
    window = imgui.internal.find_window_by_name(title)
    # The bindings type these as non-optional, but they can be None.
    docked = window is not None and window.dock_node is not None  # pyright: ignore[reportUnnecessaryComparison]
    if changed and not docked:
        imgui.set_next_window_pos(
            imgui.get_main_viewport().get_center(),
            imgui.Cond_.always,
            imgui.ImVec2(0.5, 0.5),
        )
        imgui.set_next_window_size(px_vec2(width, height), imgui.Cond_.always)
        # Scrolling sideways, a window keeps the width its content had.
        flags = 0
    return flags


def begin_tool_window(
    title: str, p_open: bool | None, *, width: float, height: float, flags: int = 0
) -> tuple[bool, bool | None]:
    """Begin a movable, dockable, non-modal window.

    It opens centred while it floats, first at ``width`` x ``height`` (unscaled pixels), and is
    centred at that size again when the screen changes its size (see :func:`follow_screen`).
    Returns ``imgui.begin``'s ``(expanded, open)``; call ``imgui.end()`` in any case.
    """
    _center_next_window()
    imgui.set_next_window_size(px_vec2(width, height), imgui.Cond_.first_use_ever)
    flags |= follow_screen(title, width, height)
    with floating_window_border():
        return imgui.begin(title, p_open, flags)

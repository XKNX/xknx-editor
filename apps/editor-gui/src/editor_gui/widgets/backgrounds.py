"""The two backgrounds of the editor's areas, derived from the theme's window color.

Content (the device and building trees, the parameters of a page) stands out from the window
around it: lighter in a light theme (about white, as Windows tools draw it), darker in a dark one
(lighter again in an almost black one).
The page tree and the help keep the window's background; a theme whose windows are white already
draws them a little darker instead, so the two still differ.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from imgui_bundle import imgui
from imgui_bundle.imgui import internal

_WHITE = imgui.ImVec4(1.0, 1.0, 1.0, 1.0)
_BLACK = imgui.ImVec4(0.0, 0.0, 0.0, 1.0)
# How far content moves from the window color: toward white in a light theme, toward black in a
# dark one, and toward white in an almost black one, which has too little room for darker.
_LIGHT_CONTENT = 0.85
_DARK_CONTENT = 0.35
_BLACK_CONTENT = 0.07
# Luminance below which a window counts as almost black.
_ALMOST_BLACK = 0.08
# How far the page tree and help move from a window color content cannot differ from.
_SIDE = 0.05
# How far a group heading moves from the side background toward the text color, and a thin line
# from it toward the separator color.
_GROUP = 0.025
_BOX_LINE = 0.5
# The least a group heading and a thin line differ from the background, in luminance: a light
# separator (Microsoft Style) would leave the lines and headings of the page tree invisible.
_GROUP_CONTRAST = 0.04
_LINE_CONTRAST = 0.09
# The least the border of a floating window (a dialog) differs from the window, in luminance:
# the light field borders of Microsoft Style would leave a dialog without a visible outline.
_WINDOW_BORDER_CONTRAST = 0.35


def _mix(a: imgui.ImVec4, b: imgui.ImVec4, t: float) -> imgui.ImVec4:
    return imgui.ImVec4(
        a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, 1.0
    )


def _luminance(color: imgui.ImVec4) -> float:
    return 0.2126 * color.x + 0.7152 * color.y + 0.0722 * color.z


def _window() -> imgui.ImVec4:
    """The theme's window color, opaque and within range (some themes exceed white)."""
    color = imgui.get_style().color_(imgui.Col_.window_bg)
    return imgui.ImVec4(
        min(max(color.x, 0.0), 1.0),
        min(max(color.y, 0.0), 1.0),
        min(max(color.z, 0.0), 1.0),
        1.0,
    )


def content_background() -> imgui.ImVec4:
    """The background of content: the trees and the parameters of a page."""
    window = _window()
    luminance = _luminance(window)
    if luminance >= 0.5:
        return _mix(window, _WHITE, _LIGHT_CONTENT)
    if luminance < _ALMOST_BLACK:
        return _mix(window, _WHITE, _BLACK_CONTENT)
    return _mix(window, _BLACK, _DARK_CONTENT)


def side_background() -> imgui.ImVec4:
    """The background beside content: the page tree and the help."""
    window = _window()
    content = content_background()
    if (
        max(
            abs(window.x - content.x),
            abs(window.y - content.y),
            abs(window.z - content.z),
        )
        > 0.02
    ):
        return window
    return _mix(window, _BLACK if _luminance(window) >= 0.5 else _WHITE, _SIDE)


def group_background() -> imgui.ImVec4:
    """The background of a group's heading row beside content: a little toward the text color,
    at least visibly apart from the rows below it."""
    side = side_background()
    text = imgui.get_style().color_(imgui.Col_.text)
    return _apart(side, _mix(side, text, _GROUP), text, _GROUP_CONTRAST)


def box_line() -> imgui.ImVec4:
    """The thin lines around group headings and the selected row: half a separator, but at
    least visible against the background (a theme's separator can be nearly as light)."""
    side = side_background()
    separator = imgui.get_style().color_(imgui.Col_.separator)
    text = imgui.get_style().color_(imgui.Col_.text)
    return _apart(side, _mix(side, separator, _BOX_LINE), text, _LINE_CONTRAST)


def window_border() -> imgui.ImVec4:
    """The border of a floating window: the theme's border, but in a light theme at least
    clearly apart from the window's background."""
    style = imgui.get_style()
    return _apart(
        _window(),
        style.color_(imgui.Col_.border),
        style.color_(imgui.Col_.text),
        _WINDOW_BORDER_CONTRAST,
    )


def _apart(
    background: imgui.ImVec4,
    color: imgui.ImVec4,
    toward: imgui.ImVec4,
    contrast: float,
) -> imgui.ImVec4:
    """``color``, or in a light theme a color from ``background`` toward ``toward`` that
    differs from the background by ``contrast`` in luminance when ``color`` differs less."""
    if (
        _luminance(background) < 0.5
        or abs(_luminance(color) - _luminance(background)) >= contrast
    ):
        return color
    span = abs(_luminance(toward) - _luminance(background))
    if span == 0:
        return color
    return _mix(background, toward, min(contrast / span, 1.0))


def fill_content_background() -> None:
    """Draw the content background from the cursor's line to the bottom of the current window,
    under what is drawn next (a tree drawn into its panel's window)."""
    inner = internal.get_current_window().inner_rect
    top = max(imgui.get_cursor_screen_pos().y, inner.min.y)
    if top >= inner.max.y:
        return
    draw_list = imgui.get_window_draw_list()
    draw_list.push_clip_rect(inner.min, inner.max, False)
    draw_list.add_rect_filled(
        imgui.ImVec2(inner.min.x, top),
        inner.max,
        imgui.get_color_u32(content_background()),
    )
    draw_list.pop_clip_rect()


@contextmanager
def child_background(color: imgui.ImVec4) -> Generator[None]:
    """Child windows begun inside get ``color`` as background, with square corners (an area of
    the window rather than a box in it)."""
    imgui.push_style_color(imgui.Col_.child_bg, color)
    imgui.push_style_var(imgui.StyleVar_.child_rounding, 0.0)
    try:
        yield
    finally:
        imgui.pop_style_var()
        imgui.pop_style_color()

"""The editor's look: the theme with its adjustments, the editor's own style on top of it, and the
window to customize both."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import darkdetect
from imgui_bundle import hello_imgui, imgui

from editor_gui.strings import S
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.dialog import begin_tool_window
from editor_gui.widgets.dpi import label_column_width
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.tabs import begin_tab_bar
from editor_gui.widgets.text_util import text_clipped_tooltip

# Display names of the themes; one missing here (a newer imgui_bundle) shows its own name.
_THEME_NAMES: dict[hello_imgui.ImGuiTheme_, str] = {
    hello_imgui.ImGuiTheme_.imgui_colors_classic: "ImGui Classic",
    hello_imgui.ImGuiTheme_.imgui_colors_dark: "ImGui Dark",
    hello_imgui.ImGuiTheme_.imgui_colors_light: "ImGui Light",
    hello_imgui.ImGuiTheme_.material_flat: "Material Flat",
    hello_imgui.ImGuiTheme_.photoshop_style: "Photoshop Style",
    hello_imgui.ImGuiTheme_.gray_variations: "Gray Variations",
    hello_imgui.ImGuiTheme_.gray_variations_darker: "Gray Variations (Darker)",
    hello_imgui.ImGuiTheme_.microsoft_style: "Microsoft Style",
    hello_imgui.ImGuiTheme_.cherry: "Cherry",
    hello_imgui.ImGuiTheme_.darcula: "Darcula",
    hello_imgui.ImGuiTheme_.darcula_darker: "Darcula (Darker)",
    hello_imgui.ImGuiTheme_.light_rounded: "Light Rounded",
    hello_imgui.ImGuiTheme_.so_dark_accent_blue: "So Dark (Blue Accent)",
    hello_imgui.ImGuiTheme_.so_dark_accent_yellow: "So Dark (Yellow Accent)",
    hello_imgui.ImGuiTheme_.so_dark_accent_red: "So Dark (Red Accent)",
    hello_imgui.ImGuiTheme_.black_is_black: "Black Is Black",
    hello_imgui.ImGuiTheme_.white_is_white: "White Is White",
}

# Words of imgui's color names spelled out, and the states shown after the element.
_COLOR_WORDS = {
    "Bg": "Background",
    "Nav": "Navigation",
    "Alt": "Alternate",
    "Frame": "Field",
    "Grab": "Grip",
}
_COLOR_STATES = {"Hovered", "Active", "Collapsed", "Selected", "Dimmed"}


# The light and the dark theme: first in the Theme menu, and the one for the system's preference
# when no theme was chosen yet.
LIGHT_THEME = hello_imgui.ImGuiTheme_.microsoft_style
DARK_THEME = hello_imgui.ImGuiTheme_.darcula_darker


def themes() -> list[hello_imgui.ImGuiTheme_]:
    return [
        hello_imgui.ImGuiTheme_(i) for i in range(int(hello_imgui.ImGuiTheme_.count))
    ]


def system_theme() -> hello_imgui.ImGuiTheme_:
    """The theme to start with when none was chosen yet: the light theme when the system prefers
    light apps, the dark theme when it prefers dark ones or does not tell."""
    try:
        light = darkdetect.isLight()
    except Exception:
        light = None
    if light:
        return LIGHT_THEME
    return DARK_THEME


def theme_name(theme: hello_imgui.ImGuiTheme_) -> str:
    return _THEME_NAMES.get(theme) or hello_imgui.imgui_theme_name(theme)


def theme_menu_groups() -> list[list[tuple[str, hello_imgui.ImGuiTheme_]]]:
    """The themes of the Theme menu with their labels, in the groups between its separators: the
    light and the dark theme ("Light Theme - Microsoft Style"), then the others."""
    return [
        [
            (f"{S.MENU_THEME_LIGHT} - {theme_name(LIGHT_THEME)}", LIGHT_THEME),
            (f"{S.MENU_THEME_DARK} - {theme_name(DARK_THEME)}", DARK_THEME),
        ],
        [(theme_name(t), t) for t in themes() if t not in (LIGHT_THEME, DARK_THEME)],
    ]


def color_name(name: str) -> str:
    """imgui's name of a style color as words: ``FrameBgHovered`` is "Frame Background (Hovered)"."""
    parts = cast(list[str], re.findall(r"[A-Z][a-z]*", name))
    words = [_COLOR_WORDS.get(w, w) for w in parts]
    states: list[str] = []
    while len(words) > 1 and words[-1] in _COLOR_STATES:
        states.insert(0, words.pop())
    text = " ".join(words)
    return f"{text} ({' '.join(states)})" if states else text


# How far table lines move from the window color toward the text color in a light theme: the
# lines between cells, and the outer ones.
_TABLE_LINE = 0.15
_TABLE_LINE_STRONG = 0.25
# The text color of Windows dialogs (#333333).
_WINDOWS_TEXT = imgui.ImVec4(0.2, 0.2, 0.2, 1.0)


def _rgb(r: int, g: int, b: int) -> imgui.ImVec4:
    return imgui.ImVec4(r / 255, g / 255, b / 255, 1.0)


# Microsoft Style with the colors of Windows dialogs: hovered items light blue, pressed ones a
# little darker, the selected tab white like the page beside it with a blue line on top. The
# theme's own tab colors barely differ, so neither hovering nor the selected tab showed. Field
# borders and lines are the light grays of Windows dialogs, the scrollbars those of Windows.
_WINDOWS_THEMES = (
    hello_imgui.ImGuiTheme_.microsoft_style,
    hello_imgui.ImGuiTheme_.white_is_white,
)
_WINDOWS_COLORS = {
    imgui.Col_.text: _WINDOWS_TEXT,
    # Ticked checkboxes and chosen radio buttons in the dark gray of the text, not near black.
    imgui.Col_.check_mark: _WINDOWS_TEXT,
    imgui.Col_.border: _rgb(204, 204, 204),
    imgui.Col_.separator: _rgb(229, 229, 229),
    imgui.Col_.scrollbar_bg: _rgb(240, 240, 240),
    imgui.Col_.scrollbar_grab: _rgb(205, 205, 205),
    imgui.Col_.scrollbar_grab_hovered: _rgb(166, 166, 166),
    imgui.Col_.scrollbar_grab_active: _rgb(96, 96, 96),
    imgui.Col_.tab: _rgb(228, 228, 228),
    imgui.Col_.tab_hovered: _rgb(229, 241, 251),
    imgui.Col_.tab_selected: _rgb(255, 255, 255),
    imgui.Col_.tab_selected_overline: _rgb(0, 120, 215),
    imgui.Col_.tab_dimmed: _rgb(228, 228, 228),
    imgui.Col_.tab_dimmed_selected: _rgb(255, 255, 255),
    imgui.Col_.tab_dimmed_selected_overline: _rgb(160, 160, 160),
    # Buttons in the light gray of Windows dialogs.
    imgui.Col_.button: _rgb(242, 242, 242),
    imgui.Col_.button_hovered: _rgb(229, 241, 251),
    imgui.Col_.button_active: _rgb(204, 228, 247),
    imgui.Col_.header_hovered: _rgb(229, 241, 251),
    imgui.Col_.header_active: _rgb(204, 228, 247),
}

# A disabled button in Windows dialogs: nearly white with a faint border and text, not the enabled
# button faded (which could never get lighter than the window it is drawn on).
_WINDOWS_DISABLED_BUTTON: dict[imgui.Col_, imgui.ImVec4] = {
    imgui.Col_.button: _rgb(253, 253, 253),
    imgui.Col_.border: _rgb(247, 247, 247),
    imgui.Col_.text: _rgb(222, 222, 222),
}
# In the dark theme a disabled button moves as far towards the window: its fill, border and text
# that share of the way from the window to the enabled button's.
_DARK_DISABLED_SHARE: dict[imgui.Col_, float] = {
    imgui.Col_.button: 0.25,
    imgui.Col_.border: 0.3,
    imgui.Col_.text: 0.3,
}
# The colors disabled buttons are drawn with in the current theme; empty: imgui's faded ones.
_disabled_button: dict[imgui.Col_, imgui.ImVec4] = {}

# The window background of Microsoft Style.
_MICROSOFT_WINDOW = _rgb(245, 246, 247)


# The Windows selection blue over a white field: (179, 214, 243), dark text stays readable on it.
_SELECTION = imgui.ImVec4(0.0, 120 / 255, 215 / 255, 0.3)
# The least a selection differs from a light field, in luminance (Light Rounded has 0.16).
_SELECTION_MIN_CONTRAST = 0.08
# Links: the blue of Windows hyperlinks on light windows, a lighter blue on dark ones.
_LINK_ON_LIGHT = imgui.ImVec4(0.0, 102 / 255, 204 / 255, 1.0)
_LINK_ON_DARK = imgui.ImVec4(110 / 255, 168 / 255, 254 / 255, 1.0)
# Texts that report a warning, an error, a success or a note: (on a light window, on a dark one).
_STATUS_COLORS = {
    "info": (_LINK_ON_LIGHT, _LINK_ON_DARK),
    "warning": (_rgb(176, 96, 0), _rgb(240, 170, 70)),
    "error": (_rgb(196, 30, 30), _rgb(240, 100, 100)),
    "success": (_rgb(16, 124, 16), _rgb(110, 200, 110)),
}


# A minimum width no tab reaches.
_NEVER = 1e30


def apply_theme(tweaked: hello_imgui.ImGuiTweakedTheme) -> None:
    """Apply ``tweaked`` with the editor's own style on top, as the editor starts with it."""
    hello_imgui.apply_tweaked_theme(tweaked)
    apply_style()


def apply_style() -> None:
    """Refine the imgui style on top of the theme: rounded corners + roomier spacing. A rounding
    the theme was adjusted to stays."""
    style = imgui.get_style()
    tweaked = hello_imgui.get_runner_params().imgui_window_params.tweaked_theme
    # White Is White is Microsoft Style a little darker: it gets the same Windows colors, but
    # keeps rounded corners.
    windows = tweaked.theme in _WINDOWS_THEMES
    square = tweaked.theme == hello_imgui.ImGuiTheme_.microsoft_style
    if tweaked.tweaks.rounding < 0:
        # Square corners in Microsoft Style, like Windows dialogs; rounded ones elsewhere.
        rounding = 0.0 if square else 4.0
        style.window_rounding = 0.0 if square else 6.0
        style.child_rounding = rounding
        style.frame_rounding = rounding
        style.popup_rounding = rounding
        style.grab_rounding = rounding
        style.tab_rounding = rounding
        style.scrollbar_rounding = rounding
    style.frame_border_size = 1.0
    # imgui's close buttons on tabs never show: ``dock_menus`` draws them, closer to a tab's right
    # edge (imgui keeps the frame padding there, as left of the label).
    style.tab_close_button_min_width_selected = _NEVER
    style.tab_close_button_min_width_unselected = _NEVER
    style.window_padding = imgui.ImVec2(10.0, 10.0)
    style.frame_padding = imgui.ImVec2(8.0, 4.0)
    style.item_spacing = imgui.ImVec2(8.0, 6.0)
    style.item_inner_spacing = imgui.ImVec2(6.0, 4.0)
    style.scrollbar_size = 12.0
    # The grab fills the scrollbar, so that it starts at the top of the list beside it.
    style.scrollbar_padding = 0.0
    style.cell_padding = imgui.ImVec2(6.0, 4.0)
    if windows:
        _clamp_colors(style)
        for col, color in _WINDOWS_COLORS.items():
            style.set_color_(col, color)
    if square:
        # A light gray with a touch of blue: the page tree and the help have it (see
        # backgrounds.side_background), and with them the windows around.
        style.set_color_(imgui.Col_.window_bg, _MICROSOFT_WINDOW)
    # The themes leave the text cursor at imgui's default, the white of its dark colors, which
    # disappears in the white fields of the light themes. imgui's own color sets draw it in the
    # text color.
    style.set_color_(imgui.Col_.input_text_cursor, style.color_(imgui.Col_.text))
    _apply_text_selection(style)
    _apply_link_color(style)
    _apply_table_lines(style)
    _apply_row_selection(style)
    _apply_resize_lines(style)
    _apply_disabled_buttons(style, windows, tweaked.theme == DARK_THEME)
    apply_window_colors()


def _apply_resize_lines(style: imgui.Style) -> None:
    """Every line that resizes something lights up in one color while hovered or dragged: the
    splitters between docked windows have the resize grip's colors, so the lines beside the page
    tree and above the help, and table column borders, get them too."""
    style.set_color_(
        imgui.Col_.separator_hovered, style.color_(imgui.Col_.resize_grip_hovered)
    )
    style.set_color_(
        imgui.Col_.separator_active, style.color_(imgui.Col_.resize_grip_active)
    )


def _apply_disabled_buttons(style: imgui.Style, windows: bool, dark: bool) -> None:
    """The colors disabled buttons get in the light and the dark theme (see
    _WINDOWS_DISABLED_BUTTON); other themes keep imgui's faded ones."""
    _disabled_button.clear()
    if windows:
        _disabled_button.update(_WINDOWS_DISABLED_BUTTON)
    elif dark:
        window = style.color_(imgui.Col_.window_bg)

        def shown(col: imgui.Col_) -> imgui.ImVec4:
            # A color as drawn on the window: its alpha mixed in.
            c = style.color_(col)
            return imgui.ImVec4(
                window.x + (c.x - window.x) * c.w,
                window.y + (c.y - window.y) * c.w,
                window.z + (c.z - window.z) * c.w,
                1.0,
            )

        for col, share in _DARK_DISABLED_SHARE.items():
            enabled = shown(col)
            _disabled_button[col] = imgui.ImVec4(
                window.x + (enabled.x - window.x) * share,
                window.y + (enabled.y - window.y) * share,
                window.z + (enabled.z - window.z) * share,
                1.0,
            )
    _install_disabled_buttons()


def _disabled_now() -> bool:
    """Whether the next item is drawn disabled (inside imgui.begin_disabled)."""
    return bool(
        imgui.get_current_context().current_item_flags & imgui.ItemFlags_.disabled
    )


def _install_disabled_buttons() -> None:
    """Draw imgui.button and imgui.small_button in the theme's disabled colors while disabled:
    at full opacity with those colors instead of imgui's faded enabled ones."""
    if getattr(imgui.button, "_disabled_colors", False):
        return
    for name in ("button", "small_button"):
        original = getattr(imgui, name)

        def drawn(*args: object, _original: Any = original, **kwargs: object) -> bool:
            if not _disabled_button or not _disabled_now():
                return _original(*args, **kwargs)
            context = imgui.get_current_context()
            imgui.push_style_var(imgui.StyleVar_.alpha, context.disabled_alpha_backup)
            fill = _disabled_button[imgui.Col_.button]
            for col in (
                imgui.Col_.button,
                imgui.Col_.button_hovered,
                imgui.Col_.button_active,
            ):
                imgui.push_style_color(col, fill)
            imgui.push_style_color(
                imgui.Col_.border, _disabled_button[imgui.Col_.border]
            )
            imgui.push_style_color(imgui.Col_.text, _disabled_button[imgui.Col_.text])
            try:
                return _original(*args, **kwargs)
            finally:
                imgui.pop_style_color(5)
                imgui.pop_style_var()

        drawn._disabled_colors = True  # type: ignore[attr-defined]
        setattr(imgui, name, drawn)


def _clamp_colors(style: imgui.Style) -> None:
    """Every color within 0..1: White Is White gives its window backgrounds about 6.6 (drawn as
    white), which the backgrounds mixed from them would carry along."""
    for col in range(imgui.Col_.count):
        c = style.color_(col)
        style.set_color_(
            col,
            imgui.ImVec4(*(min(max(v, 0.0), 1.0) for v in (c.x, c.y, c.z, c.w))),
        )


def _luminance(color: imgui.ImVec4) -> float:
    r, g, b = (min(max(c, 0.0), 1.0) for c in (color.x, color.y, color.z))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _apply_text_selection(style: imgui.Style) -> None:
    """Selected text in a light field in the light blue of Windows text boxes when the theme's
    selection barely differs from the field (Microsoft Style, White Is White, Gray Variations)."""
    field = style.color_(imgui.Col_.frame_bg)
    selection = style.color_(imgui.Col_.text_selected_bg)
    if _luminance(field) < 0.8:
        return
    shown = abs(_luminance(selection) - _luminance(field)) * min(
        max(selection.w, 0.0), 1.0
    )
    if shown < _SELECTION_MIN_CONTRAST:
        style.set_color_(imgui.Col_.text_selected_bg, _SELECTION)


def _apply_link_color(style: imgui.Style) -> None:
    """Links in a link blue that stands out from the window: the themes derive the link color from
    a header shade, near white on the white windows of the light themes and grey in dark ones."""
    window = style.color_(imgui.Col_.window_bg)
    light = _luminance(window) >= 0.5
    style.set_color_(imgui.Col_.text_link, _LINK_ON_LIGHT if light else _LINK_ON_DARK)


def status_color(kind: str) -> imgui.ImVec4:
    """The color of a text that reports a ``kind`` ("warning", "error", "success" or "info"), dark enough
    on the theme's window when it is light, and light enough when it is dark."""
    on_light, on_dark = _STATUS_COLORS[kind]
    window = imgui.get_style().color_(imgui.Col_.window_bg)
    return on_light if _luminance(window) >= 0.5 else on_dark


def _apply_table_lines(style: imgui.Style) -> None:
    """In a light theme, table lines a little darker than the window rather than the near black
    some light themes ship (dark themes draw them subtly already)."""
    window = style.color_(imgui.Col_.window_bg)
    w = [min(max(c, 0.0), 1.0) for c in (window.x, window.y, window.z)]
    if 0.2126 * w[0] + 0.7152 * w[1] + 0.0722 * w[2] < 0.5:
        return
    text = style.color_(imgui.Col_.text)
    for col, share in (
        (imgui.Col_.table_border_light, _TABLE_LINE),
        (imgui.Col_.table_border_strong, _TABLE_LINE_STRONG),
    ):
        mixed = [
            a + (b - a) * share
            for a, b in zip(w, (text.x, text.y, text.z), strict=True)
        ]
        style.set_color_(col, imgui.ImVec4(mixed[0], mixed[1], mixed[2], 1.0))


def _apply_row_selection(style: imgui.Style) -> None:
    """In a light theme, a selected row or item in the gray of the table headers where that is
    darker: some light themes select in a gray barely darker than their alternate table rows."""
    window = style.color_(imgui.Col_.window_bg)
    if _luminance(window) < 0.5:
        return
    selection = style.color_(imgui.Col_.header)
    # How light the selection is drawn over the window (it may be partly transparent).
    alpha = min(max(selection.w, 0.0), 1.0)
    shown = _luminance(window) + (_luminance(selection) - _luminance(window)) * alpha
    header = style.color_(imgui.Col_.table_header_bg)
    if _luminance(header) < shown:
        style.set_color_(imgui.Col_.header, header)


def apply_window_colors() -> None:
    """Opaque windows, popups and title bars, and a dark dim behind modal dialogs.

    The theme ships translucent backgrounds and a translucent active title bar, which lets the
    content behind dialogs and tool windows show through."""
    style = imgui.get_style()
    for col in (imgui.Col_.window_bg, imgui.Col_.popup_bg):
        color = style.color_(col)
        style.set_color_(col, imgui.ImVec4(color.x, color.y, color.z, 1.0))
    # The active title bar keeps the shade it had over the window background, now opaque.
    title = style.color_(imgui.Col_.title_bg_active)
    window = style.color_(imgui.Col_.window_bg)
    alpha = title.w
    style.set_color_(
        imgui.Col_.title_bg_active,
        imgui.ImVec4(
            title.x * alpha + window.x * (1.0 - alpha),
            title.y * alpha + window.y * (1.0 - alpha),
            title.z * alpha + window.z * (1.0 - alpha),
            1.0,
        ),
    )
    style.set_color_(imgui.Col_.modal_window_dim_bg, imgui.ImVec4(0.0, 0.0, 0.0, 0.45))


@dataclass(frozen=True)
class _Setting:
    """A style value edited with a slider: its attribute, label and range."""

    attribute: str
    label: Callable[[], str]
    minimum: float
    maximum: float
    format: str = "%.0f"


# The theme's adjustments (ImGuiThemeTweaks); a negative value keeps the theme's own.
_ADJUSTMENTS = (
    _Setting("rounding", lambda: S.THEME_ROUNDING, 0.0, 20.0, "%.1f"),
    _Setting(
        "rounding_scrollbar_ratio",
        lambda: S.THEME_SCROLLBAR_ROUNDING_RATIO,
        1.0,
        20.0,
        "%.1f",
    ),
    _Setting("alpha_multiplier", lambda: S.THEME_OPACITY, 0.2, 1.0, "%.2f"),
    _Setting("hue", lambda: S.THEME_HUE, 0.0, 1.0, "%.2f"),
    _Setting("saturation_multiplier", lambda: S.THEME_SATURATION, 0.0, 2.0, "%.2f"),
    _Setting(
        "value_multiplier_front", lambda: S.THEME_BRIGHTNESS_WIDGETS, 0.0, 2.0, "%.2f"
    ),
    _Setting(
        "value_multiplier_bg", lambda: S.THEME_BRIGHTNESS_BACKGROUNDS, 0.0, 2.0, "%.2f"
    ),
    _Setting(
        "value_multiplier_text", lambda: S.THEME_BRIGHTNESS_TEXT, 0.0, 2.0, "%.2f"
    ),
    _Setting(
        "value_multiplier_frame_bg", lambda: S.THEME_BRIGHTNESS_FIELDS, 0.0, 2.0, "%.2f"
    ),
)

# The sizes of the current style, by section; a pair is edited as its two values.
_SIZES: tuple[tuple[Callable[[], str], tuple[_Setting, ...]], ...] = (
    (
        lambda: S.THEME_SECTION_SPACING,
        (
            _Setting("window_padding", lambda: S.THEME_WINDOW_PADDING, 0.0, 20.0),
            _Setting("frame_padding", lambda: S.THEME_FRAME_PADDING, 0.0, 20.0),
            _Setting("cell_padding", lambda: S.THEME_CELL_PADDING, 0.0, 20.0),
            _Setting("item_spacing", lambda: S.THEME_ITEM_SPACING, 0.0, 20.0),
            _Setting(
                "item_inner_spacing", lambda: S.THEME_ITEM_INNER_SPACING, 0.0, 20.0
            ),
            _Setting("touch_extra_padding", lambda: S.THEME_TOUCH_PADDING, 0.0, 10.0),
            _Setting("indent_spacing", lambda: S.THEME_INDENT, 0.0, 30.0),
            _Setting("scrollbar_size", lambda: S.THEME_SCROLLBAR_SIZE, 1.0, 20.0),
            _Setting("grab_min_size", lambda: S.THEME_GRAB_SIZE, 1.0, 20.0),
        ),
    ),
    (
        lambda: S.THEME_SECTION_BORDERS,
        (
            _Setting("window_border_size", lambda: S.THEME_WINDOWS, 0.0, 1.0),
            _Setting("child_border_size", lambda: S.THEME_CHILD_WINDOWS, 0.0, 1.0),
            _Setting("popup_border_size", lambda: S.THEME_POPUPS, 0.0, 1.0),
            _Setting("frame_border_size", lambda: S.THEME_FIELDS, 0.0, 1.0),
            _Setting("tab_border_size", lambda: S.THEME_TABS, 0.0, 1.0),
            _Setting("tab_bar_border_size", lambda: S.THEME_TAB_BAR, 0.0, 2.0),
        ),
    ),
    (
        lambda: S.THEME_ROUNDING,
        (
            _Setting("window_rounding", lambda: S.THEME_WINDOWS, 0.0, 12.0),
            _Setting("child_rounding", lambda: S.THEME_CHILD_WINDOWS, 0.0, 12.0),
            _Setting("frame_rounding", lambda: S.THEME_FIELDS, 0.0, 12.0),
            _Setting("popup_rounding", lambda: S.THEME_POPUPS, 0.0, 12.0),
            _Setting("scrollbar_rounding", lambda: S.THEME_SCROLLBARS, 0.0, 12.0),
            _Setting("grab_rounding", lambda: S.THEME_SLIDER_GRIPS, 0.0, 12.0),
            _Setting("tab_rounding", lambda: S.THEME_TABS, 0.0, 12.0),
        ),
    ),
    (
        lambda: S.THEME_SECTION_ALIGNMENT,
        (
            _Setting(
                "window_title_align",
                lambda: S.THEME_WINDOW_TITLE_ALIGN,
                0.0,
                1.0,
                "%.2f",
            ),
            _Setting(
                "button_text_align", lambda: S.THEME_BUTTON_TEXT_ALIGN, 0.0, 1.0, "%.2f"
            ),
            _Setting(
                "selectable_text_align",
                lambda: S.THEME_SELECTABLE_TEXT_ALIGN,
                0.0,
                1.0,
                "%.2f",
            ),
            _Setting(
                "separator_text_align",
                lambda: S.THEME_SEPARATOR_TEXT_ALIGN,
                0.0,
                1.0,
                "%.2f",
            ),
        ),
    ),
)

_RENDERING = (
    _Setting("font_scale_main", lambda: S.THEME_FONT_SCALE, 0.5, 2.0, "%.2f"),
    _Setting(
        "curve_tessellation_max_error", lambda: S.THEME_CURVE_ERROR, 0.1, 5.0, "%.2f"
    ),
    _Setting(
        "circle_tessellation_max_error", lambda: S.THEME_CIRCLE_ERROR, 0.1, 5.0, "%.2f"
    ),
    _Setting("alpha", lambda: S.THEME_GLOBAL_OPACITY, 0.2, 1.0, "%.2f"),
    _Setting("disabled_alpha", lambda: S.THEME_DISABLED_OPACITY, 0.0, 1.0, "%.2f"),
)


def _column(*labels: str) -> float:
    """``same_line`` offset of the value column after ``labels``: as wide as the widest label,
    but at most a little more than half the width, so the values keep room."""
    limit = imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x * 0.55
    return min(label_column_width(*labels, minimum=100.0), limit)


def _label(text: str, column: float) -> None:
    """``text`` in front of the next item, which fills the row after ``column``; a label wider
    than the column is clipped and shown in full on hover."""
    imgui.align_text_to_frame_padding()
    width = column - imgui.get_cursor_pos_x() - imgui.get_style().item_spacing.x
    text_clipped_tooltip(text, width=width)
    imgui.same_line(column)
    imgui.set_next_item_width(-1.0)


def _color_field(index: int, color: imgui.ImVec4, flags: int) -> imgui.ImVec4 | None:
    """A color's swatch, which opens a picker, and its hex field; the changed color, if any.
    Drawn from parts, as the built-in color field's menu and picker labels are untranslated."""
    changed_color: imgui.ImVec4 | None = None
    popup = f"##color_picker{index}"
    if imgui.color_button(f"##swatch{index}", color, flags):
        imgui.open_popup(popup)
    imgui.same_line(0.0, imgui.get_style().item_inner_spacing.x)
    imgui.set_next_item_width(-1.0)
    changed, edited = imgui.color_edit4(
        f"##color{index}",
        color,
        flags
        | imgui.ColorEditFlags_.no_picker
        | imgui.ColorEditFlags_.no_options
        | imgui.ColorEditFlags_.no_small_preview,
    )
    if changed:
        changed_color = edited
    if imgui.begin_popup(popup):
        changed, picked = imgui.color_picker4(
            f"##picker{index}",
            color,
            flags
            | imgui.ColorEditFlags_.no_side_preview
            | imgui.ColorEditFlags_.no_options,
        )
        if changed:
            changed_color = picked
        imgui.end_popup()
    return changed_color


def _slider(owner: object, setting: _Setting, column: float) -> bool:
    """The slider of ``setting`` on ``owner``; whether it changed."""
    _label(setting.label(), column)
    value = getattr(owner, setting.attribute)
    widget_id = f"##{setting.attribute}"
    if isinstance(value, imgui.ImVec2):
        changed, pair = imgui.slider_float2(
            widget_id,
            [value.x, value.y],
            setting.minimum,
            setting.maximum,
            setting.format,
        )
        if changed:
            setattr(owner, setting.attribute, imgui.ImVec2(pair[0], pair[1]))
        return changed
    # A negative adjustment keeps the theme's value: the slider says so until it is moved.
    text = S.THEME_DEFAULT.replace("%", "%%") if value < 0 else setting.format
    changed, number = imgui.slider_float(
        widget_id, float(value), setting.minimum, setting.maximum, text
    )
    if changed:
        setattr(owner, setting.attribute, number)
    return changed


class ThemeWindow:
    """The theme and its adjustments, and every size, color and rendering setting of the style,
    each with its label in front, as everywhere in the editor."""

    def __init__(self) -> None:
        self.open = False
        self._demo = False
        self._color_filter = ""

    def render(self) -> None:
        if self._demo:
            self._demo = bool(imgui.show_demo_window(self._demo))
        if not self.open:
            return
        expanded, open_state = begin_tool_window(
            f"{S.THEME_WINDOW_TITLE}###theme", self.open, width=380, height=600
        )
        self.open = bool(open_state)
        if expanded:
            tweaked = hello_imgui.get_runner_params().imgui_window_params.tweaked_theme
            if begin_tab_bar("##theme_tabs"):
                if imgui.begin_tab_item(S.SETTING_THEME)[0]:
                    self._render_theme(tweaked)
                    imgui.end_tab_item()
                if imgui.begin_tab_item(S.THEME_TAB_SIZES)[0]:
                    self._render_sizes()
                    imgui.end_tab_item()
                if imgui.begin_tab_item(S.THEME_TAB_COLORS)[0]:
                    self._render_colors()
                    imgui.end_tab_item()
                if imgui.begin_tab_item(S.THEME_TAB_RENDERING)[0]:
                    self._render_rendering()
                    imgui.end_tab_item()
                imgui.end_tab_bar()
        imgui.end()

    def _render_theme(self, tweaked: hello_imgui.ImGuiTweakedTheme) -> None:
        column = _column(S.SETTING_THEME, *(s.label() for s in _ADJUSTMENTS))
        _label(S.SETTING_THEME, column)
        if begin_combo("##theme", theme_name(tweaked.theme)):
            for theme in themes():
                if imgui.selectable(theme_name(theme), theme == tweaked.theme)[0]:
                    tweaked.theme = theme
                    apply_theme(tweaked)
            imgui.end_combo()
        imgui.separator_text(S.THEME_SECTION_ADJUSTMENTS)
        changed = False
        for setting in _ADJUSTMENTS:
            changed |= _slider(tweaked.tweaks, setting, column)
        if changed:
            apply_theme(tweaked)
        imgui.spacing()
        if imgui.button(S.THEME_RESET):
            tweaked.tweaks = hello_imgui.ImGuiThemeTweaks()
            apply_theme(tweaked)

    def _render_sizes(self) -> None:
        style = imgui.get_style()
        column = _column(*(s.label() for _, settings in _SIZES for s in settings))
        for section, settings in _SIZES:
            imgui.separator_text(section())
            for setting in settings:
                _slider(style, setting, column)

    def _render_colors(self) -> None:
        style = imgui.get_style()
        self._color_filter = filter_box(
            "##theme_colors", S.THEME_FILTER_COLORS, self._color_filter
        )
        names = [
            (index, color_name(imgui.get_style_color_name(index)))
            for index in range(int(imgui.Col_.count))
        ]
        wanted = self._color_filter.strip().lower()
        shown = [(i, n) for i, n in names if wanted in n.lower()]
        # One hex field per color (#RRGGBBAA) instead of four narrow number fields; the swatch
        # opens the picker.
        flags = (
            imgui.ColorEditFlags_.display_hex
            | imgui.ColorEditFlags_.alpha_bar
            | imgui.ColorEditFlags_.alpha_preview_half
        )
        if imgui.begin_child("##theme_color_list"):
            column = _column(*(n for _, n in shown))
            for index, name in shown:
                _label(name, column)
                color = _color_field(index, style.color_(index), flags)
                if color is not None:
                    style.set_color_(index, color)
        imgui.end_child()

    def _render_rendering(self) -> None:
        style = imgui.get_style()
        column = _column(
            S.THEME_ANTI_ALIASED_LINES,
            S.THEME_ANTI_ALIASED_FILL,
            S.THEME_DEMO_WINDOW,
            *(s.label() for s in _RENDERING),
        )
        for setting in _RENDERING:
            _slider(style, setting, column)
        _label(S.THEME_ANTI_ALIASED_LINES, column)
        style.anti_aliased_lines = checkbox(
            "##anti_aliased_lines", style.anti_aliased_lines
        )[1]
        _label(S.THEME_ANTI_ALIASED_FILL, column)
        style.anti_aliased_fill = checkbox(
            "##anti_aliased_fill", style.anti_aliased_fill
        )[1]
        _label(S.THEME_DEMO_WINDOW, column)
        self._demo = checkbox("##demo_window", self._demo)[1]

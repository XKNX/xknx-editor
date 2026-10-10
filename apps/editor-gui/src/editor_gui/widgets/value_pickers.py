"""Colour and date parameter fields with a picker popup.

A colour is stored as ``#RRGGBB``, a date as ``YYYY-MM-DD``. A date is shown in a date pattern
(``yyyy``/``yy`` year, ``MMMM`` month name, ``MM``/``M`` month, ``dd``/``d`` day, quoted
literals), without the year in a day-and-month pattern.
"""

from __future__ import annotations

import calendar
import datetime
import re
from collections.abc import Callable, Sequence
from functools import cache

from imgui_bundle import imgui

from editor_gui.regional import RegionalFormat, regional_format
from editor_gui.widgets.dpi import px
from editor_gui.widgets.strings import S

# Theme colours (top row of the palette) and the standard colours row.
_THEME_COLORS = (
    0xFFFFFF,
    0x000000,
    0xEEECE1,
    0x1F497D,
    0x4F81BD,
    0xC0504D,
    0x9BBB59,
    0x8064A2,
    0x4BACC6,
    0xF79646,
)
_STANDARD_COLORS = (
    0xC00000,
    0xFF0000,
    0xFFC000,
    0xFFFF00,
    0x92D050,
    0x00B050,
    0x00B0F0,
    0x0070C0,
    0x002060,
    0x7030A0,
)
# Shades below each theme colour: positive lightens towards white, negative darkens.
_SHADES = (0.8, 0.6, 0.4, -0.25, -0.5)
_WHITE_SHADES = (-0.05, -0.15, -0.25, -0.35, -0.5)
_BLACK_SHADES = (0.5, 0.35, 0.25, 0.15, 0.05)

_HEX = re.compile(r"#?([0-9A-Fa-f]{6})")
ISO_DATE = "yyyy-MM-dd"
_DATE_TOKEN = re.compile(r"'[^']*'|yyyy|yy|MMMM|MMM|MM|M|dddd|ddd|dd|d")
# The year of a date pattern with the separator next to it.
_YEAR_PART = re.compile(r"[^dMy']?y{2,4}$|^y{2,4}[^dMy']?")


def parse_color(text: str) -> str | None:
    """``#RRGGBB`` for a hex colour entered with or without ``#``; None if invalid."""
    match = _HEX.fullmatch(text.strip())
    return f"#{match[1].upper()}" if match else None


def shade(rgb: int, amount: float) -> int:
    """``rgb`` lightened (positive ``amount``) or darkened (negative) by that fraction."""
    channels = ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
    if amount >= 0:
        shaded = [round(c + (255 - c) * amount) for c in channels]
    else:
        shaded = [round(c * (1 + amount)) for c in channels]
    return (shaded[0] << 16) | (shaded[1] << 8) | shaded[2]


def format_date(stored: str, year_shown: bool, regional: RegionalFormat) -> str:
    """The stored date in the short date pattern, or in the day-and-month pattern when the
    type does not show the year; text that is not a date is returned unchanged."""
    try:
        date = datetime.date.fromisoformat(stored)
    except ValueError:
        return stored
    month_name = regional.months[date.month - 1]
    fields = {
        "yyyy": f"{date.year:04d}",
        "yy": f"{date.year % 100:02d}",
        "MMMM": month_name,
        "MMM": month_name[:3],
        "MM": f"{date.month:02d}",
        "M": str(date.month),
        "dd": f"{date.day:02d}",
        "d": str(date.day),
    }
    pattern = regional.short_date if year_shown else regional.day_month
    return _DATE_TOKEN.sub(
        lambda m: m[0][1:-1] if m[0].startswith("'") else fields.get(m[0], ""),
        pattern,
    )


def parse_date(
    text: str, year_shown: bool, stored: str, regional: RegionalFormat
) -> str | None:
    """``YYYY-MM-DD`` for an entered date; None if invalid.

    Accepts the short date pattern and ``YYYY-MM-DD``; without a shown year the day-and-month
    pattern or the short date pattern without its year, keeping the year of ``stored``."""
    if year_shown:
        patterns = (regional.short_date, ISO_DATE)
    else:
        patterns = (
            regional.day_month,
            _YEAR_PART.sub("", regional.short_date),
            _YEAR_PART.sub("", ISO_DATE),
        )
    text = text.strip()
    for pattern in patterns:
        match = _date_regex(pattern, regional.months).fullmatch(text)
        if match is None:
            continue
        if match["y"]:
            year = int(match["y"])
        elif match["yy"]:
            year = 2000 + int(match["yy"])
        else:
            year = _date_of(stored).year
        if match["name"]:
            names = [name.casefold() for name in regional.months]
            month = names.index(match["name"].casefold()) + 1
        else:
            month = int(match["m"])
        try:
            return datetime.date(year, month, int(match["d"])).isoformat()
        except ValueError:
            return None
    return None


@cache
def _date_regex(pattern: str, months: Sequence[str]) -> re.Pattern[str]:
    names = "|".join(re.escape(name) for name in months)
    groups = {
        "yyyy": r"(?P<y>\d{4})",
        "yy": r"(?P<yy>\d{2})",
        "MMMM": rf"(?P<name>{names})",
        "MMM": rf"(?P<name>{names})",
        "MM": r"(?P<m>\d{1,2})",
        "M": r"(?P<m>\d{1,2})",
        "dd": r"(?P<d>\d{1,2})",
        "d": r"(?P<d>\d{1,2})",
    }
    parts: list[str] = []
    position = 0
    for token in _DATE_TOKEN.finditer(pattern):
        parts.append(re.escape(pattern[position : token.start()]))
        if token[0].startswith("'"):
            parts.append(re.escape(token[0][1:-1]))
        else:
            parts.append(groups.get(token[0], r"\S*"))
        position = token.end()
    parts.append(re.escape(pattern[position:]))
    regex = "".join(parts).replace(r"\ ", r"\s*")
    for name in ("y", "yy", "name", "m"):
        if f"(?P<{name}>" not in regex:
            regex += f"(?P<{name}>)"
    return re.compile(regex, re.IGNORECASE)


def render_color_param(
    widget_id: str, value: str, on_change: Callable[[str], None], differs: bool
) -> None:
    """A hex colour entry filled with its colour, with an arrow that opens the colour picker."""
    width = imgui.calc_item_width()
    arrow = imgui.get_frame_height()
    rgb = int(value[1:], 16) if not differs and parse_color(value) else None
    imgui.set_next_item_width(max(px(40.0), width - arrow))
    if rgb is not None:
        imgui.push_style_color(imgui.Col_.frame_bg, _vec4(rgb))
        imgui.push_style_color(imgui.Col_.frame_bg_hovered, _vec4(rgb))
        imgui.push_style_color(imgui.Col_.frame_bg_active, _vec4(rgb))
        imgui.push_style_color(imgui.Col_.text, _vec4(_contrast(rgb)))
    if differs:
        _, text = imgui.input_text_with_hint(f"##{widget_id}", S.PARAM_DIFFERS, "")
    else:
        _, text = imgui.input_text(f"##{widget_id}", value)
    if rgb is not None:
        imgui.pop_style_color(4)
    if imgui.is_item_deactivated_after_edit() and text != value:
        color = parse_color(text)
        if color is not None:
            on_change(color)
    imgui.same_line(0, 0)
    popup = f"##color_popup_{widget_id}"
    if imgui.arrow_button(f"##color_open_{widget_id}", imgui.Dir.down):
        imgui.open_popup(popup)
    if imgui.begin_popup(popup):
        picked = _render_color_popup(rgb if rgb is not None else 0xFFFFFF)
        if picked is not None:
            on_change(f"#{picked:06X}")
        imgui.end_popup()


def _contrast(rgb: int) -> int:
    """Black or white, whichever is easier to read on ``rgb``."""
    r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    return 0x000000 if 0.299 * r + 0.587 * g + 0.114 * b > 140 else 0xFFFFFF


def _render_color_popup(current: int) -> int | None:
    picked: int | None = None
    if imgui.begin_tab_bar("##color_tabs"):
        if imgui.begin_tab_item(S.COLOR_STANDARD)[0]:
            imgui.text_disabled(S.COLOR_THEME_COLORS)
            for col, base in enumerate(_THEME_COLORS):
                if col:
                    imgui.same_line()
                picked = _swatch(base, current) or picked
            shades = {0xFFFFFF: _WHITE_SHADES, 0x000000: _BLACK_SHADES}
            for row in range(len(_SHADES)):
                for col, base in enumerate(_THEME_COLORS):
                    if col:
                        imgui.same_line()
                    amount = shades.get(base, _SHADES)[row]
                    picked = _swatch(shade(base, amount), current) or picked
            imgui.text_disabled(S.COLOR_STANDARD_COLORS)
            for col, rgb in enumerate(_STANDARD_COLORS):
                if col:
                    imgui.same_line()
                picked = _swatch(rgb, current) or picked
            imgui.end_tab_item()
        if imgui.begin_tab_item(S.COLOR_ADVANCED)[0]:
            color = [((current >> s) & 0xFF) / 255.0 for s in (16, 8, 0)]
            changed, color = imgui.color_picker3(
                "##color_picker", color, imgui.ColorEditFlags_.display_hex
            )
            if imgui.is_item_deactivated_after_edit() or (
                changed and not imgui.is_mouse_down(imgui.MouseButton_.left)
            ):
                r, g, b = (round(c * 255) for c in color)
                picked = (r << 16) | (g << 8) | b
            imgui.end_tab_item()
        imgui.end_tab_bar()
    if picked is not None:
        imgui.close_current_popup()
    return picked


def _swatch(rgb: int, current: int) -> int | None:
    size = imgui.ImVec2(px(18.0), px(18.0))
    if rgb == current:
        imgui.push_style_var(imgui.StyleVar_.frame_border_size, px(2.0))
    clicked = imgui.color_button(
        f"#{rgb:06X}##swatch", _vec4(rgb), imgui.ColorEditFlags_.no_tooltip, size
    )
    if rgb == current:
        imgui.pop_style_var()
    if imgui.is_item_hovered():
        imgui.set_tooltip(f"#{rgb:06X}")
    return rgb if clicked else None


def _vec4(rgb: int) -> imgui.ImVec4:
    return imgui.ImVec4(
        ((rgb >> 16) & 0xFF) / 255.0,
        ((rgb >> 8) & 0xFF) / 255.0,
        (rgb & 0xFF) / 255.0,
        1.0,
    )


# Month shown by each open date popup, keyed by widget id.
_shown_month: dict[str, tuple[int, int]] = {}


def render_date_param(
    widget_id: str,
    value: str,
    year_shown: bool,
    on_change: Callable[[str], None],
    differs: bool,
) -> None:
    """A date entry with a button that opens a calendar."""
    regional = regional_format()
    width = imgui.calc_item_width()
    button = imgui.calc_text_size("...").x + 2 * imgui.get_style().frame_padding.x
    spacing = imgui.get_style().item_inner_spacing.x
    imgui.set_next_item_width(max(px(60.0), width - button - spacing))
    shown = "" if differs else format_date(value, year_shown, regional)
    if differs:
        _, text = imgui.input_text_with_hint(f"##{widget_id}", S.PARAM_DIFFERS, "")
    else:
        _, text = imgui.input_text(f"##{widget_id}", shown)
    if imgui.is_item_deactivated_after_edit() and text != shown:
        date = parse_date(text, year_shown, value, regional)
        if date is not None:
            on_change(date)
    imgui.same_line(0, spacing)
    popup = f"##date_popup_{widget_id}"
    if imgui.button(f"...##date_{widget_id}"):
        current = _date_of(value)
        _shown_month[widget_id] = (current.year, current.month)
        imgui.open_popup(popup)
    if imgui.begin_popup(popup):
        picked = _render_calendar(widget_id, _date_of(value), regional.months)
        if picked is not None:
            on_change(picked.isoformat())
            imgui.close_current_popup()
        imgui.end_popup()


def _date_of(stored: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(stored)
    except ValueError:
        return datetime.date.today()


def _render_calendar(
    widget_id: str, selected: datetime.date, months: Sequence[str]
) -> datetime.date | None:
    year, month = _shown_month.get(widget_id, (selected.year, selected.month))
    if imgui.arrow_button("##prev_month", imgui.Dir.left):
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    imgui.same_line()
    imgui.text(f"{months[month - 1]} {year}")
    imgui.same_line()
    if imgui.arrow_button("##next_month", imgui.Dir.right):
        year, month = (year, month + 1) if month < 12 else (year + 1, 1)
    year = min(max(year, datetime.MINYEAR), datetime.MAXYEAR)
    _shown_month[widget_id] = (year, month)

    picked: datetime.date | None = None
    flags = imgui.TableFlags_.sizing_fixed_same | imgui.TableFlags_.no_saved_settings
    if imgui.begin_table("##calendar", 7, flags):
        for name in S.CALENDAR_WEEKDAYS.split():
            imgui.table_next_column()
            imgui.text_disabled(name)
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
            imgui.table_next_row()
            for day in week:
                imgui.table_next_column()
                if day.month != month:
                    imgui.push_style_color(
                        imgui.Col_.text,
                        imgui.get_style_color_vec4(imgui.Col_.text_disabled),
                    )
                clicked = imgui.selectable(
                    f"{day.day:2d}##{day.isoformat()}",
                    day == selected,
                    0,
                    imgui.ImVec2(imgui.calc_text_size("00").x, 0),
                )[0]
                if day.month != month:
                    imgui.pop_style_color()
                if clicked:
                    picked = day
        imgui.end_table()
    return picked

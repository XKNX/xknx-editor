"""Selection of a data point type from the KNX master data: a "-" entry for none, each main
type as ``1.* 1-bit`` and its subtypes indented as ``1.001 switch``."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.dpi import px

_SUBTYPE = re.compile(r"^DPST-(\d+)-(\d+)$")
_MAIN = re.compile(r"^DPT-(\d+)$")


@dataclass(frozen=True)
class DptChoice:
    token: str  # DPT-1 / DPST-1-1
    label: str  # 1.* 1-bit / 1.001 switch
    subtype: bool


def dpt_choices(names: Mapping[str, str]) -> list[DptChoice]:
    """Every main type followed by its subtypes, sorted by number."""
    mains: dict[int, str] = {}
    subs: dict[int, list[tuple[int, str]]] = {}
    for token, name in names.items():
        if match := _MAIN.match(token):
            mains[int(match.group(1))] = name
        elif match := _SUBTYPE.match(token):
            subs.setdefault(int(match.group(1)), []).append((int(match.group(2)), name))
    choices: list[DptChoice] = []
    for main in sorted(mains.keys() | subs.keys()):
        choices.append(
            DptChoice(f"DPT-{main}", f"{main}.* {mains.get(main, '')}".strip(), False)
        )
        for minor, name in sorted(subs.get(main, [])):
            choices.append(
                DptChoice(f"DPST-{main}-{minor}", f"{main}.{minor:03d} {name}", True)
            )
    return choices


def dpt_choice_label(token: str | None, names: Mapping[str, str]) -> str:
    """The label of ``token`` as listed (``1.001 switch``); ``-`` for none, else the token."""
    if not token:
        return "-"
    if match := _SUBTYPE.match(token):
        main, minor = int(match.group(1)), int(match.group(2))
        return f"{main}.{minor:03d} {names.get(token, '')}".strip()
    if match := _MAIN.match(token):
        return f"{match.group(1)}.* {names.get(token, '')}".strip()
    return token


_filters: dict[str, str] = {}
_cached: tuple[int, list[DptChoice]] = (0, [])


def _cached_choices(names: Mapping[str, str]) -> list[DptChoice]:
    global _cached
    if _cached[0] != id(names):
        _cached = (id(names), dpt_choices(names))
    return _cached[1]


# The rows the list shows at once; how far subtypes are indented (at 100 % scaling).
_ROWS = 15
_INDENT = 16.0


def _popup_size(choices: list[DptChoice]) -> imgui.ImVec2:
    """The size of the open drop-down: as wide as the drop-down or its widest type, whichever
    is wider, and high enough for the filter and :data:`_ROWS` rows. Fixed, so that it keeps
    its size while the list is filtered."""
    style = imgui.get_style()
    widest = max(
        (
            imgui.calc_text_size(c.label).x + (px(_INDENT) if c.subtype else 0.0)
            for c in choices
        ),
        default=0.0,
    )
    width = max(
        imgui.calc_item_width(),
        widest
        + 2 * (style.window_padding.x + style.frame_padding.x)
        + style.scrollbar_size,
    )
    height = (
        imgui.get_frame_height_with_spacing()
        + _ROWS * imgui.get_text_line_height_with_spacing()
        + 2 * style.window_padding.y
    )
    return imgui.ImVec2(width, height)


def dpt_combo(
    widget_id: str, token: str | None, names: Mapping[str, str], filter_hint: str
) -> tuple[bool, str]:
    """A drop-down of all data point types; returns ``(changed, token)`` with ``""`` for none.
    A filter above the list narrows it."""
    current = token or ""
    changed = False
    choices = _cached_choices(names)
    size = _popup_size(choices)
    imgui.set_next_window_size_constraints(size, size)
    if begin_combo(f"##{widget_id}", dpt_choice_label(current, names)):
        appearing = imgui.is_window_appearing()
        if appearing:
            _filters[widget_id] = ""
            imgui.set_keyboard_focus_here()
        imgui.set_next_item_width(-1)
        _, text = imgui.input_text_with_hint(
            f"##{widget_id}_filter", filter_hint, _filters.get(widget_id, "")
        )
        _filters[widget_id] = text
        needle = text.strip().lower()
        # The list scrolls below the filter, which stays in place.
        if imgui.begin_child(f"##{widget_id}_list"):
            if not needle and imgui.selectable("-", not current)[0]:
                current, changed = "", True
            for choice in choices:
                if needle and needle not in choice.label.lower():
                    continue
                selected = choice.token == current
                if choice.subtype:
                    imgui.indent(px(_INDENT))
                if imgui.selectable(f"{choice.label}##{choice.token}", selected)[0]:
                    current, changed = choice.token, True
                    imgui.close_current_popup()
                if choice.subtype:
                    imgui.unindent(px(_INDENT))
                if selected and appearing:
                    imgui.set_scroll_here_y()
        imgui.end_child()
        imgui.end_combo()
    return changed, current

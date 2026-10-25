"""The drop-downs for an element's "Status", a room's "Use" and a space's "Current Line", filling
the width of their cell."""

from __future__ import annotations

from imgui_bundle import imgui

from editor_gui.plugins.project.element_status import (
    status_label,
    status_options,
    usage_label,
    usage_options,
)
from editor_gui.widgets.combo import begin_combo, combo

# Rows the "Use" list shows before it scrolls (it has 28 entries).
USAGE_ROWS = 16


def _choose(
    label: str,
    options: list[tuple[str, str]],
    current: str,
    current_text: str,
    rows: int = -1,
) -> str | None:
    values = [value for value, _text in options]
    texts = [text for _value, text in options]
    index = values.index(current) if current in values else len(values)
    if index == len(values):
        # A value the list lacks (e.g. a use of newer master data) is shown as it is.
        texts.append(current_text)
    imgui.set_next_item_width(-1)
    changed, chosen = combo(label, index, texts, rows)
    if not changed or chosen >= len(values):
        return None
    return values[chosen]


def choice_combo(
    label: str, options: list[tuple[str, str]], current: str
) -> str | None:
    """A drop-down of ``options`` (value, text) with ``current`` chosen; the value chosen, or
    None while it stays. A value the options lack is shown as it is."""
    return _choose(label, options, current, current)


def status_combo(label: str, current: str) -> str | None:
    """The "Status" drop-down; the status chosen, or None while it stays."""
    return _choose(label, status_options(), current, status_label(current))


def usage_combo(label: str, current: str, current_text: str = "") -> str | None:
    """The "Use" drop-down of a room ("-" for none); the SpaceUsage id chosen ("" for none), or
    None while it stays."""
    return _choose(
        label,
        usage_options(),
        current,
        usage_label(current, current_text),
        USAGE_ROWS,
    )


def line_combo(
    label: str,
    own: int | None,
    preview: str,
    choices: list[tuple[int, str]],
    none_text: str,
) -> tuple[bool, int | None]:
    """The "Current Line" drop-down: ``none_text`` for none, then each line of ``choices`` (its
    id and text), with the space's ``own`` line marked; ``preview`` is shown while it is closed.
    Returns whether another choice was made, and the line chosen (None for none)."""
    chosen: tuple[bool, int | None] = (False, own)
    imgui.set_next_item_width(-1)
    if begin_combo(label, preview):
        options: list[tuple[int | None, str]] = [(None, none_text), *choices]
        for line_id, text in options:
            selected = line_id == own
            if imgui.selectable(f"{text}##{line_id}", selected)[0] and not selected:
                chosen = (True, line_id)
            if selected:
                imgui.set_item_default_focus()
        imgui.end_combo()
    return chosen

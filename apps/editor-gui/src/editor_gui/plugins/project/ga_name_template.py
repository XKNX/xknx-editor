"""A user-editable template for auto-naming a new group address when a com-object is linked.

The template uses ``{placeholder}`` tokens (see :data:`PLACEHOLDERS`). Empty placeholders collapse
so no dangling separators are left behind. The chosen template is stored program-wide in ``app.json``
under ``ga_name_template`` (not per project)."""

from __future__ import annotations

import re
from collections.abc import Mapping

DEFAULT_GA_NAME_TEMPLATE = "{room}-{device}-{object}-{function}"

# Placeholder name -> short description (shown in the editor hint; also the documented vocabulary).
PLACEHOLDERS: dict[str, str] = {
    "building": "Building name",
    "floor": "Floor name",
    "room": "Room name",
    "device": "Device description or name",
    "devicename": "Device name",
    "devicedescription": "Device description",
    "product": "Product name",
    "manufacturer": "Manufacturer name",
    "order": "Order number",
    "address": "Individual address",
    "object": "Group object name",
    "ganame": "Group object name",
    "function": "Object function",
    "number": "Object number",
    "dpt": "Datapoint type",
}

_TOKEN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::([^{}]*))?\}")
# A run of two or more separators (optionally spaced) left when a placeholder between them is empty.
_SEP_RUN = re.compile(r"\s*[-/:|,](?:\s*[-/:|,])+\s*")
# Leading/trailing whitespace and separators left when an edge placeholder is empty.
_EDGE = re.compile(r"^[\s\-/:|,]+|[\s\-/:|,]+$")


def _render_token(name: str, spec: str | None, values: Mapping[str, object]) -> str:
    if name not in values:
        return ""
    value = values[name]
    if (
        spec
    ):  # e.g. {n:02} -> apply the Python format spec (zero-padding etc.) to the value
        try:
            return format(value, spec)
        except (ValueError, TypeError):
            return str(value).strip()
    return str(value).strip()


def _collapse_run(match: re.Match[str]) -> str:
    """Collapse a run of separators to a single one, keeping the run's outer spacing so a
    space-styled template ("a - b") and a tight one ("a-b") each keep their own look."""
    run = match.group(0)
    sep = run.strip()[0]
    lead = " " if run[:1] == " " else ""
    trail = " " if run[-1:] == " " else ""
    return f"{lead}{sep}{trail}"


def render_name_template(template: str, values: Mapping[str, object]) -> str:
    """Render ``template`` with ``values``.

    Unknown placeholders become empty (never raising), ``{name:spec}`` applies a Python format spec
    (e.g. ``{n:02}`` zero-pads), and separators left by an empty placeholder are collapsed so a
    missing part does not leave a dangling ``-``/``/`` or doubled separator."""
    text = _TOKEN.sub(lambda m: _render_token(m.group(1), m.group(2), values), template)
    text = re.sub(r"\s+", " ", text)
    text = _SEP_RUN.sub(_collapse_run, text)
    text = _EDGE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()

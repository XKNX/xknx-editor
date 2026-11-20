"""Shared property layout for classic Siemens DALI configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any


@dataclass(frozen=True)
class Property:
    """One property array and its destination in the configuration XML."""

    container: str
    object_index: int
    per_line: bool
    pid: int
    size: int
    count: int
    item: str
    leaf: str
    occurrence: int
    kind: str


@cache
def _mapping() -> dict[str, Any]:
    """Load the fixed property layout."""
    return json.loads(
        files("editor_gui.plugin_blob_readers.siemens_dali")
        .joinpath("recovery_properties.json")
        .read_text(encoding="utf-8")
    )


def properties() -> tuple[Property, ...]:
    """Return the property layouts for classic gateways."""
    return tuple(Property(*row) for row in _mapping()["properties"])


def enum_values(name: str) -> dict[str, int]:
    """Return runtime values for a property enumeration."""
    return _mapping()["enums"][name]


def line_count(app_id: str) -> int:
    """Return the number of lines for the classic application family."""
    family = app_id.upper().split("-")
    if len(family) >= 3 and family[0:2] == ["M", "0001_A"]:
        if family[2] == "9837":
            return 1
        if family[2] == "9834":
            return 2
    return 0

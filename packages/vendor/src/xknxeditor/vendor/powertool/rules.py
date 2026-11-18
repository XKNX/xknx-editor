"""Evaluate shared decision tables for configuration-dependent properties."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

type Value = int | float | str | bool | None


def decide(
    table: dict[str, Any],
    rule: int,
    read: Callable[[list[str | int]], Value],
    active: Callable[[str], bool],
) -> Value:
    """Follow predicates lazily; negative references identify result values."""

    def value(index: int) -> Value:
        spec = table["values"][index]
        kind = spec[0]
        if kind == "literal":
            return spec[1]
        if kind == "field":
            return read(spec[1])
        if kind == "scaled":
            shift = read(spec[2])
            if type(shift) is not int:
                raise ValueError("Invalid PowerTool field shift")
            return int(spec[1]) << shift
        raise ValueError("Unknown PowerTool rule value")

    def matches(index: int) -> bool:
        spec = table["predicates"][index]
        operation = spec[0]
        if operation == "attached":
            return active(spec[1])
        left = value(spec[1])
        if operation == "truth":
            return bool(left)
        right = value(spec[2])
        if operation == "equal":
            return left == right
        if operation == "different":
            return left != right
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            raise ValueError("Invalid PowerTool numeric comparison")
        if operation == "above":
            return left > right
        if operation == "at_least":
            return left >= right
        if operation == "below":
            return left < right
        if operation == "at_most":
            return left <= right
        raise ValueError("Unknown PowerTool rule predicate")

    branches = table["branches"]
    remaining = len(branches) + 1
    while rule >= 0:
        if remaining == 0:
            raise ValueError("Cyclic PowerTool decision table")
        remaining -= 1
        predicate, yes, no = branches[rule]
        rule = yes if matches(predicate) else no
    return value(-rule - 1)

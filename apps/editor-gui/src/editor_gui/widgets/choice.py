"""The colors of a chosen item where the theme gives it its own: the open drop-down, its chosen
and hovered entries and the dot of a chosen radio button (the light theme's blue). The theme sets
them (``theme.apply_style``); None keeps the theme's own colors."""

from __future__ import annotations

from dataclasses import dataclass

from imgui_bundle import imgui


@dataclass(frozen=True)
class ChoiceColors:
    # The open drop-down's field, its chosen and hovered entries and a chosen radio button's dot.
    fill: imgui.ImVec4
    # The background of a drop-down's list.
    list_bg: imgui.ImVec4


_colors: ChoiceColors | None = None


def set_choice_colors(colors: ChoiceColors | None) -> None:
    global _colors
    _colors = colors


def choice_colors() -> ChoiceColors | None:
    return _colors

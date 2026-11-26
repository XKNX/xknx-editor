"""The colors of the information and error boxes of a parameter page where the theme gives them
its own (Microsoft Style: those of Windows dialogs). The theme sets them
(``theme.apply_style``); None: a box's border color tints the page."""

from __future__ import annotations

from dataclasses import dataclass

from imgui_bundle import imgui


@dataclass(frozen=True)
class BoxColors:
    """The border, the background and the icon of a box."""

    border: imgui.ImVec4
    fill: imgui.ImVec4
    icon: imgui.ImVec4


@dataclass(frozen=True)
class HintColors:
    info: BoxColors
    error: BoxColors


_colors: HintColors | None = None


def set_hint_colors(colors: HintColors | None) -> None:
    global _colors
    _colors = colors


def hint_colors() -> HintColors | None:
    return _colors

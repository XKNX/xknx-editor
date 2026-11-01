"""The editor's one loading animation: a short arc turning on a faint ring, beside a text that
says what loads. The arc has the link color (the accent blue of Windows on light themes, a lighter
blue on dark ones) and the ring the text color, faint, so both show on any background."""

from __future__ import annotations

from imgui_bundle import imgui, imspinner

from editor_gui.widgets.dpi import px

# Radius and line width of the ring at 100 % scaling.
_RADIUS = 6.0
_THICKNESS = 2.0
# How much of the text color the ring shows.
_RING_ALPHA = 0.18


def spinner(str_id: str) -> None:
    """The loading animation, as an item on the current line."""
    style = imgui.get_style()
    arc = style.color_(imgui.Col_.text_link)
    text = style.color_(imgui.Col_.text)
    ring = imgui.ImVec4(text.x, text.y, text.z, _RING_ALPHA)
    imspinner.spinner_ang(
        str_id,
        px(_RADIUS),
        px(_THICKNESS),
        color=imgui.ImColor(arc),
        bg=imgui.ImColor(ring),
    )


def loading(str_id: str, text: str) -> None:
    """The loading animation with ``text`` (what loads) after it."""
    spinner(str_id)
    imgui.same_line()
    imgui.text_disabled(text)

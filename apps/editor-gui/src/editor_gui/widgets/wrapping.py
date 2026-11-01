"""Wrapped text with its lines as far apart as in Windows dialogs, and vertical room between items.

The font's own line height leaves less room between lines than the text of Windows dialogs has,
and ImGui's wrapped text has no line spacing, so the text is broken into lines here (where a WPF
TextBlock breaks it, see ``text_layout``).
"""

from __future__ import annotations

from imgui_bundle import imgui

from editor_gui.widgets import text_layout
from editor_gui.widgets.dpi import px

# Gap at 100 % scaling between the lines of a wrapped text, beyond the font's line height.
LINE_GAP = 2.0


def wrap_lines(text: str, width: float) -> list[str]:
    """``text`` broken into lines at most ``width`` wide, in the current font, where a WPF
    TextBlock breaks it (see ``text_layout.wrap``). The spaces a line starts with stay (an
    indent)."""
    return text_layout.wrap(text, width)


def text_height(lines: int) -> float:
    """Height of ``lines`` lines of wrapped text in the current font, with the gaps between."""
    return lines * imgui.get_text_line_height() + (lines - 1) * px(LINE_GAP)


def wrapped_text(text: str, width: float) -> None:
    """``text`` wrapped at ``width`` from the cursor, as one item."""
    spacing = imgui.get_style().item_spacing
    imgui.push_style_var(
        imgui.StyleVar_.item_spacing, imgui.ImVec2(spacing.x, px(LINE_GAP))
    )
    imgui.begin_group()
    for line in wrap_lines(text, width):
        text_layout.draw_text(line)
    imgui.end_group()
    imgui.pop_style_var()


def vertical_space(height: float) -> None:
    """``height`` more room below the item above than the usual spacing."""
    spacing = imgui.get_style().item_spacing
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(spacing.x, 0.0))
    imgui.dummy(imgui.ImVec2(0.0, height))
    imgui.pop_style_var()

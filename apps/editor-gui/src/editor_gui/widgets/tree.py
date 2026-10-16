"""Rows of the panel trees: a narrow margin around a node's arrow, and rows without an arrow
(devices, group addresses) just inside the label of the node they belong to."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from imgui_bundle import imgui

from editor_gui.widgets.dpi import px

# Space left and right of a node's arrow, at 100 % scaling.
_ARROW_MARGIN = 3.0
# How far a row without an arrow starts right of its parent's label, at 100 % scaling.
_LEAF_INSET = 8.0


def tree_node(label: str, flags: int = 0) -> bool:
    """``imgui.tree_node_ex`` with the narrow arrow margin."""
    padding = imgui.ImVec2(px(_ARROW_MARGIN), imgui.get_style().frame_padding.y)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, padding)
    try:
        return imgui.tree_node_ex(label, flags)
    finally:
        imgui.pop_style_var()


def label_offset() -> float:
    """Distance from the left of a :func:`tree_node` row to its label."""
    return imgui.get_font_size() + 2 * px(_ARROW_MARGIN)


def leaf_shift(*, nodes: bool = False) -> float:
    """Indent that moves rows without an arrow under a :func:`tree_node` to just inside its
    label: selectables, or leaf tree nodes with ``nodes`` (they keep the room of an arrow)."""
    shift = px(_LEAF_INSET) - imgui.get_style().indent_spacing
    return shift if nodes else shift + label_offset()


@contextmanager
def leaf_rows(*, nodes: bool = False) -> Generator[None]:
    """Draw the rows without an arrow of the node just opened (see :func:`leaf_shift`)."""
    shift = leaf_shift(nodes=nodes)
    # ``indent(0)`` would indent by the default spacing.
    if shift:
        imgui.indent(shift)
    try:
        yield
    finally:
        if shift:
            imgui.unindent(shift)

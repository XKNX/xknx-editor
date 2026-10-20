"""Rows of the panel trees: a narrow margin around a node's arrow, and rows without an arrow
(devices, group addresses) just inside the label of the node they belong to. Section headers with
their title close to the arrow."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from imgui_bundle import imgui

from editor_gui.widgets.dpi import px

# Space left and right of a node's arrow, at 100 % scaling.
_ARROW_MARGIN = 3.0
# How far a row without an arrow starts right of its parent's label, at 100 % scaling.
_LEAF_INSET = 8.0
# Space between a section header's arrow box and its title, at 100 % scaling.
_HEADER_GAP = 10.0


def tree_node(label: str, flags: int = 0) -> bool:
    """``imgui.tree_node_ex`` with the narrow arrow margin."""
    padding = imgui.ImVec2(px(_ARROW_MARGIN), imgui.get_style().frame_padding.y)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, padding)
    try:
        return imgui.tree_node_ex(label, flags)
    finally:
        imgui.pop_style_var()


def collapsing_header(label: str, flags: int = 0) -> bool:
    """``imgui.collapsing_header`` with its title closer to the arrow (imgui leaves two frame
    paddings between them). Same id rules as imgui: ``##`` hides the rest, ``###`` sets the id."""
    title = label.split("##", 1)[0]
    ident = label[label.index("###") :] if "###" in label else label
    imgui.push_id(ident)
    try:
        opened = imgui.collapsing_header("##header", flags)
    finally:
        imgui.pop_id()
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    style = imgui.get_style()
    x = low.x + style.frame_padding.x + imgui.get_font_size() + px(_HEADER_GAP)
    draw = imgui.get_window_draw_list()
    draw.push_clip_rect(low, high, True)
    draw.add_text(
        imgui.ImVec2(x, low.y + style.frame_padding.y),
        imgui.get_color_u32(imgui.Col_.text),
        title,
    )
    draw.pop_clip_rect()
    return opened


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

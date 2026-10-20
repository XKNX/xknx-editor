"""Rows of the panel trees: a narrow margin around a node's arrow, and rows without an arrow
(devices, group addresses) just inside the label of the node they belong to. Section headers with
their title close to the arrow."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.widgets.dpi import px

# Space left and right of a node's arrow, at 100 % scaling.
_ARROW_MARGIN = 3.0
# How far a row without an arrow starts right of its parent's label, at 100 % scaling.
_LEAF_INSET = 8.0
# imgui's arrow in a box of the font size: the closed one reaches from 0.2 to 0.8 of it
# across and has 0.15 of it free above and below (a triangle of radius 0.4, see RenderArrow).
_ARROW_START = 0.2
_ARROW_END = 0.8
_ARROW_FREE_HEIGHT = 0.5 - 0.4 * 0.866


def tree_node(label: str, flags: int = 0) -> bool:
    """``imgui.tree_node_ex`` with the narrow arrow margin."""
    padding = imgui.ImVec2(px(_ARROW_MARGIN), imgui.get_style().frame_padding.y)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, padding)
    try:
        return imgui.tree_node_ex(label, flags)
    finally:
        imgui.pop_style_var()


def collapsing_header(label: str, flags: int = 0) -> bool:
    """``imgui.collapsing_header`` with the arrow as far from the left as from the top and bottom,
    and its title as far from the arrow (imgui leaves two frame paddings and more there). Same id
    rules as imgui: ``##`` hides the rest, ``###`` sets the id."""
    title = label.split("##", 1)[0]
    ident = label[label.index("###") :] if "###" in label else label
    style = imgui.get_style()
    font = imgui.get_font_size()
    margin = style.frame_padding.y + _ARROW_FREE_HEIGHT * font
    # imgui widens the frame into the window padding by this much, but places the arrow from where
    # the content starts (the cursor).
    reach = int(internal.get_current_window().window_padding.x * 0.5 - 1.0)
    content_x = imgui.get_cursor_screen_pos().x
    arrow_box_x = max(0.0, margin - reach - _ARROW_START * font)
    imgui.push_id(ident)
    imgui.push_style_var(
        imgui.StyleVar_.frame_padding, imgui.ImVec2(arrow_box_x, style.frame_padding.y)
    )
    try:
        opened = imgui.collapsing_header("##header", flags)
    finally:
        imgui.pop_style_var()
        imgui.pop_id()
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    x = content_x + arrow_box_x + _ARROW_END * font + margin
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

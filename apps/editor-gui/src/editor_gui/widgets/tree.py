"""Rows of the panel trees: a narrow margin around a node's arrow, and rows without an arrow
(devices, group addresses) just inside the label of the node they belong to, highlighted while
their context menu is open. Section headers with their title close to the arrow."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.widgets.backgrounds import child_background, content_background
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


def begin_tree_area(str_id: str, height: float = 0.0) -> bool:
    """The area a panel's tree scrolls in below its filter box: the rest of the panel (or
    ``height``), scrolling sideways too when names are wider than the panel (a phone). End it
    with ``imgui.end_child()`` whatever this returns."""
    with child_background(content_background()):
        opened = imgui.begin_child(
            str_id,
            imgui.ImVec2(0.0, height),
            0,
            imgui.WindowFlags_.horizontal_scrollbar,
        )
    if opened:
        _scroll_while_dragging()
        _highlight_menu_row()
    return opened


# The row whose context menu is open: the id of its tree area's window, its rectangle relative to
# the window's scrolled content, and the frame it was seen in.
_menu_row: tuple[int, tuple[float, float, float, float], int] | None = None


def context_menu(str_id: str) -> bool:
    """``imgui.begin_popup_context_item`` for the row just drawn in a tree area: while its menu is
    open, the row keeps its hover highlight, so it is clear whose menu it is. End the menu with
    ``imgui.end_popup()`` when this returns True."""
    global _menu_row
    window = internal.get_current_window()
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    if not imgui.begin_popup_context_item(str_id):
        return False
    # A tree node's highlight reaches to the right edge of the area, past its label.
    right = max(high.x, window.work_rect.max.x)
    x, y = window.pos.x - window.scroll.x, window.pos.y - window.scroll.y
    _menu_row = (
        window.id_,
        (low.x - x, low.y - y, right - x, high.y - y),
        imgui.get_frame_count(),
    )
    return True


def _highlight_menu_row() -> None:
    """Behind the rows of the tree area being drawn, the hover highlight of the row whose context
    menu was open in the last frame."""
    if _menu_row is None:
        return
    window = internal.get_current_window()
    ident, (left, top, right, bottom), frame = _menu_row
    if ident != window.id_ or frame < imgui.get_frame_count() - 1:
        return
    x, y = window.pos.x - window.scroll.x, window.pos.y - window.scroll.y
    imgui.get_window_draw_list().add_rect_filled(
        imgui.ImVec2(left + x, top + y),
        imgui.ImVec2(right + x, bottom + y),
        imgui.get_color_u32(imgui.Col_.header_hovered),
    )


# While something is dragged, the area scrolls when the pointer is this close to its top or bottom
# (rows, at most a third of the area), the faster the closer, up to this many rows per second.
_DRAG_EDGE_ROWS = 1.5
_DRAG_SCROLL_ROWS = 20.0


def _scroll_while_dragging() -> None:
    """Scroll the tree area being drawn while a drag nears its top or bottom, so that every row
    can be reached as a place to drop on."""
    if imgui.get_drag_drop_payload_py_id() is None:  # pyright: ignore[reportUnnecessaryComparison]
        return
    window = internal.get_current_window()
    low, high = window.inner_rect.min, window.inner_rect.max
    mouse = imgui.get_io().mouse_pos
    if not low.x <= mouse.x <= high.x or not low.y <= mouse.y <= high.y:
        return
    row = imgui.get_frame_height_with_spacing()
    edge = min(row * _DRAG_EDGE_ROWS, (high.y - low.y) / 3)
    if edge <= 0:
        return
    if mouse.y < low.y + edge:
        closeness = (low.y + edge - mouse.y) / edge
    elif mouse.y > high.y - edge:
        closeness = -(mouse.y - (high.y - edge)) / edge
    else:
        return
    step = closeness * row * _DRAG_SCROLL_ROWS * imgui.get_io().delta_time
    imgui.set_scroll_y(
        min(max(imgui.get_scroll_y() - step, 0.0), imgui.get_scroll_max_y())
    )


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

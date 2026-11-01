"""The area a panel's tree scrolls in: while something is dragged near its top or bottom, it
scrolls that way, so that every row can be reached to drop on; a row whose context menu is open
keeps its highlight."""

from __future__ import annotations

import itertools
from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import tree
from editor_gui.widgets.tree import (
    begin_tree_area,
    context_menu,
    row_selectable,
    same_row,
    tree_node,
)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(400, 300)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frame() -> float:
    """One frame of a 200 px high tree area with 100 rows; its scroll position."""
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(300, 200))
    imgui.begin("Panel")
    if begin_tree_area("##area"):
        for i in range(100):
            imgui.text(f"Row {i}")
    scroll = imgui.get_scroll_y()
    imgui.end_child()
    imgui.end()
    imgui.end_frame()
    return scroll


def _menu_frame(rows: dict[int, tuple[float, float]]) -> None:
    """One frame of a tree area with 10 rows, each with a context menu; ``rows`` gets each row's
    top and bottom."""
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(300, 200))
    imgui.begin("Panel")
    if begin_tree_area("##area"):
        for i in range(10):
            imgui.selectable(f"Row {i}", False)
            rows[i] = (imgui.get_item_rect_min().y, imgui.get_item_rect_max().y)
            if context_menu(f"##ctx{i}"):
                imgui.text("Menu")
                imgui.end_popup()
    imgui.end_child()
    imgui.end()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_a_row_keeps_its_highlight_while_its_menu_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drawn: list[tuple[float, float]] = []

    class Recorder:
        def add_rect_filled(
            self, low: imgui.ImVec2, high: imgui.ImVec2, _col: int
        ) -> None:
            drawn.append((low.y, high.y))

    monkeypatch.setattr(tree.imgui, "get_window_draw_list", Recorder)
    rows: dict[int, tuple[float, float]] = {}
    io = imgui.get_io()
    _menu_frame(rows)
    top, bottom = rows[3]
    io.add_mouse_pos_event(100, (top + bottom) / 2)
    _menu_frame(rows)
    io.add_mouse_button_event(1, True)
    _menu_frame(rows)
    io.add_mouse_button_event(1, False)
    _menu_frame(rows)
    assert drawn == []
    # The menu is open: from the next frame on, row 3 is highlighted behind the rows.
    for _ in range(3):
        _menu_frame(rows)
    assert drawn
    assert drawn[-1] == pytest.approx(rows[3])
    # A click elsewhere closes it, and the highlight goes.
    io.add_mouse_pos_event(100, 190)
    io.add_mouse_button_event(0, True)
    _menu_frame(rows)
    io.add_mouse_button_event(0, False)
    _menu_frame(rows)
    _menu_frame(rows)
    count = len(drawn)
    _menu_frame(rows)
    assert len(drawn) == count


@pytest.mark.usefixtures("context")
def test_the_area_scrolls_while_dragging_near_its_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    io = imgui.get_io()
    io.delta_time = 0.1
    io.add_mouse_pos_event(100, 185)
    for _ in range(3):
        _frame()
    # Without a drag the pointer at the bottom scrolls nothing.
    assert _frame() == 0.0
    monkeypatch.setattr(tree.imgui, "get_drag_drop_payload_py_id", lambda: object())
    for _ in range(5):
        _frame()
    assert _frame() > 0.0
    # In the middle it stays; near the top it scrolls back up.
    io.add_mouse_pos_event(100, 100)
    _frame()
    _frame()
    down = _frame()
    assert _frame() == down
    io.add_mouse_pos_event(100, 40)
    for _ in range(3):
        _frame()
    assert _frame() < down


@pytest.mark.usefixtures("context")
def test_rows_touch_so_that_selected_rows_make_one_area() -> None:
    rows: list[tuple[float, float]] = []
    imgui.new_frame()
    imgui.begin("Panel")
    if begin_tree_area("##area"):
        style = imgui.get_style()
        pitch = imgui.get_text_line_height() + style.item_spacing.y + 4.0
        tree_node("Area", imgui.TreeNodeFlags_.leaf)
        rows.append((imgui.get_item_rect_min().y, imgui.get_item_rect_max().y))
        imgui.tree_pop()
        # A note after the label on its line takes no room below the row.
        with same_row():
            imgui.text("note")
            note = imgui.get_item_rect_min().y
        row_selectable("Device", True)
        rows.append((imgui.get_item_rect_min().y, imgui.get_item_rect_max().y))
        tree_node("Line", imgui.TreeNodeFlags_.leaf)
        rows.append((imgui.get_item_rect_min().y, imgui.get_item_rect_max().y))
        imgui.tree_pop()
    imgui.end_child()
    imgui.end()
    imgui.end_frame()
    for (_top, bottom), (top, _bottom) in itertools.pairwise(rows):
        assert top == pytest.approx(bottom)
    # 4 px further apart than text rows with the usual spacing (24 px at 100 %), and the note
    # on the label's line, in the middle of its row.
    assert note == pytest.approx(
        rows[0][0] + (pitch - imgui.get_text_line_height()) / 2
    )
    assert [bottom - top for top, bottom in rows] == pytest.approx([pitch] * 3)

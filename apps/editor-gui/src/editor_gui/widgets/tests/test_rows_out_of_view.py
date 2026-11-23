"""Rows of a tree far outside its visible part take their room without being drawn: the rows
below them and the scroll range stay where they are, the rows near the visible part and the
selected one are drawn, and a closed dialog costs nothing."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.dialog import begin_dialog
from editor_gui.widgets.tree import row_out_of_view, row_selectable

_ROWS = 200


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _tree(
    *, skip: bool, keep: int | None = None
) -> tuple[list[int], list[float], float]:
    """One frame of a 200-row tree in a 200 px high window: the rows drawn, the top of every
    row and the height of the content."""
    drawn: list[int] = []
    tops: list[float] = []
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(300, 200))
    imgui.begin("tree")
    for row in range(_ROWS):
        tops.append(imgui.get_cursor_pos_y())
        if skip and row_out_of_view(keep=row == keep):
            continue
        row_selectable(f"Row {row}##{row}", row == keep)
        drawn.append(row)
    height = imgui.get_cursor_pos_y()
    imgui.end()
    imgui.end_frame()
    return drawn, tops, height


@pytest.mark.usefixtures("context")
def test_rows_out_of_view_keep_their_room() -> None:
    all_rows, all_tops, all_height = _tree(skip=False)
    drawn, tops, height = _tree(skip=True)
    assert all_rows == list(range(_ROWS))
    # The visible rows and a few below them are drawn, the rest only takes its room.
    assert drawn[0] == 0
    assert 5 < len(drawn) < 30
    assert drawn == list(range(len(drawn)))
    assert tops == pytest.approx(all_tops)
    assert height == pytest.approx(all_height)


@pytest.mark.usefixtures("context")
def test_the_selected_row_is_drawn_wherever_it_is() -> None:
    drawn, _tops, _height = _tree(skip=True, keep=150)
    assert 150 in drawn
    assert 149 not in drawn


@pytest.mark.usefixtures("context")
def test_a_closed_dialog_is_not_begun() -> None:
    imgui.new_frame()
    assert not begin_dialog("Closed")
    imgui.open_popup("Opened")
    assert begin_dialog("Opened")
    imgui.end_popup()
    imgui.end_frame()

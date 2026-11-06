"""A note in place of a tree row (e.g. a product being downloaded) sits where the row would: as
high, its text as far in; it is grey and nothing happens on it."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.tree import leaf_rows, note_row, tree_node

_LEAF = (
    imgui.TreeNodeFlags_.leaf
    | imgui.TreeNodeFlags_.no_tree_push_on_open
    | imgui.TreeNodeFlags_.span_avail_width
)


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(400, 500))
    imgui.begin("Catalog", None, imgui.WindowFlags_.no_saved_settings)
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


@dataclass
class _Drawn:
    text: imgui.ImVec4
    alpha: float
    disabled: bool


def _row(draw: Callable[[], object]) -> tuple[float, float, float]:
    """Draw a row; its left edge, its label's left edge and its height with the spacing."""
    top = imgui.get_cursor_screen_pos()
    draw()
    end = imgui.get_cursor_screen_pos()
    return top.x, imgui.get_item_rect_min().x, end.y - top.y


@pytest.mark.usefixtures("frame")
def test_a_note_takes_a_rows_place(monkeypatch: pytest.MonkeyPatch) -> None:
    drawn: list[_Drawn] = []
    tree_node_ex = imgui.tree_node_ex

    def record(label: str, flags: int) -> bool:
        drawn.append(
            _Drawn(
                text=imgui.get_style_color_vec4(imgui.Col_.text),
                alpha=imgui.get_style().alpha,
                disabled=imgui.get_current_context().disabled_stack_size > 0,
            )
        )
        return tree_node_ex(label, flags)

    monkeypatch.setattr(imgui, "tree_node_ex", record)
    style = imgui.get_style()
    normal = style.color_(imgui.Col_.text)
    grey = style.color_(imgui.Col_.text_disabled)
    with leaf_rows(nodes=True):
        row = _row(lambda: tree_node("Product##row", _LEAF))
        note = _row(lambda: note_row("Downloading…##note", _LEAF))
    # Where a row is and as high: the text lands where the row's label is.
    assert note == pytest.approx(row)
    product, downloading = drawn
    assert not product.disabled
    assert (product.text.x, product.text.w) == (normal.x, normal.w)
    # Disabled, so neither highlighted nor clicked, but grey as a disabled text, not dimmed.
    assert downloading.disabled
    assert (downloading.text.x, downloading.text.w) == (grey.x, grey.w)
    assert downloading.alpha == pytest.approx(product.alpha)
    # Nothing it pushed is left behind.
    assert imgui.get_style_color_vec4(imgui.Col_.text).x == normal.x
    assert imgui.get_current_context().disabled_stack_size == 0

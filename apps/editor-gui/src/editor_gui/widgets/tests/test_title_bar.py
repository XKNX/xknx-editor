"""The title bar of a tool window while it floats: its arrow and close button as far from its left
and right edges as from its top and bottom, its title as far from the arrow as in a section
header; the content keeps the usual paddings."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import dialog
from editor_gui.widgets.dialog import begin_tool_window
from editor_gui.widgets.tree import arrow_margin, title_gap


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _hovered(mouse: tuple[float, float]) -> tuple[int, int, int, imgui.ImVec2, float]:
    """With the mouse at ``mouse``: the item it is over, the ids of the window's close and
    collapse buttons, and the window's position and width."""
    imgui.get_io().add_mouse_pos_event(*mouse)
    ids = (0, 0)
    pos, width = imgui.ImVec2(0, 0), 0.0
    for _ in range(3):
        imgui.new_frame()
        begin_tool_window("Tool", True, width=400.0, height=200.0)
        window = imgui.internal.get_current_window()
        ids = (window.get_id("#CLOSE"), window.get_id("#COLLAPSE"))
        pos, width = imgui.get_window_pos(), imgui.get_window_width()
        imgui.end()
        imgui.end_frame()
    hovered = imgui.get_current_context().hovered_id_previous_frame
    return hovered, *ids, pos, width


@pytest.mark.usefixtures("context")
def test_title_bar_buttons_as_far_from_the_sides_as_from_top_and_bottom() -> None:
    _, close, collapse, pos, width = _hovered((0, 0))
    gap = imgui.get_style().frame_padding.y
    font = imgui.get_font_size()
    right = pos.x + width
    middle = pos.y + gap + font / 2
    # The close button: a box of the font size, ``gap`` from the right edge.
    assert _hovered((right - gap - 0.5, middle))[0] == close
    assert _hovered((right - gap - font + 0.5, middle))[0] == close
    assert _hovered((right - gap + 0.5, middle))[0] != close
    # The arrow: ``gap`` from the left edge.
    assert _hovered((pos.x + gap + 0.5, middle))[0] == collapse
    assert _hovered((pos.x + gap + font - 0.5, middle))[0] == collapse
    assert _hovered((pos.x + gap - 0.5, middle))[0] != collapse


@pytest.mark.usefixtures("context")
def test_title_bar_gaps_only_while_the_window_begins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[float, float, float]] = []
    begin = imgui.begin

    def record(*args: object) -> tuple[bool, bool | None]:
        style = imgui.get_style()
        seen.append(
            (style.frame_padding.x, style.frame_padding.y, style.item_inner_spacing.x)
        )
        return begin(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(dialog.imgui, "begin", record)
    style = imgui.get_style()
    before = (style.frame_padding.x, style.frame_padding.y, style.item_inner_spacing.x)
    imgui.new_frame()
    begin_tool_window("Tool", True, width=400.0, height=200.0)
    inside = (style.frame_padding.x, style.frame_padding.y, style.item_inner_spacing.x)
    imgui.end()
    # Inside the 1 px border at the sides; the title as far from the arrow's ink as in a section
    # header (imgui adds the inner spacing twice).
    gap = imgui.get_style().frame_padding.y
    assert seen == [pytest.approx((gap - 1, gap, title_gap() / 2))]
    assert title_gap() + 0.2 * imgui.get_font_size() == pytest.approx(arrow_margin())
    # The content keeps the theme's paddings.
    assert inside == before
    # Docked, the window's section draws its tab bar with the usual padding.
    seen.clear()
    monkeypatch.setattr(
        imgui.internal,
        "find_window_by_name",
        lambda _name: SimpleNamespace(dock_node=object()),
    )
    begin_tool_window("Tool", True, width=400.0, height=200.0)
    imgui.end()
    assert seen == [before]
    imgui.end_frame()

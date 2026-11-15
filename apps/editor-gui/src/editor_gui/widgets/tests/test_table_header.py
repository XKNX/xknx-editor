"""A sorted column's header has its sort arrow in the middle of the text's line (imgui puts the
smaller arrow at the top of it), pointing up or down as the column is sorted."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.widgets import tables


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def test_the_sort_arrow_is_in_the_middle_of_the_header_text(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    arrows: list[tuple[float, Any]] = []
    starts: list[float] = []
    real_arrow = internal.render_arrow
    real_header = imgui.table_header

    def arrow(
        draw: Any, pos: imgui.ImVec2, color: int, direction: Any, scale: float
    ) -> None:
        real_arrow(draw, pos, color, direction, scale)
        arrows.append((pos.y, direction))

    def header(label: str) -> None:
        starts.append(imgui.get_cursor_screen_pos().y)
        real_header(label)

    monkeypatch.setattr(internal, "render_arrow", arrow)
    monkeypatch.setattr(imgui, "table_header", header)
    for _ in range(3):
        arrows.clear()
        starts.clear()
        imgui.new_frame()
        imgui.begin("Table")
        if imgui.begin_table("##sorted", 2, imgui.TableFlags_.sortable):
            imgui.table_setup_column("Address", imgui.TableColumnFlags_.default_sort)
            imgui.table_setup_column("Name")
            tables.headers_row()
            imgui.end_table()
        imgui.end()
        imgui.end_frame()
    font = imgui.get_font_size()
    # Only the sorted column has an arrow, pointing up (ascending).
    assert len(arrows) == 1
    y, direction = arrows[0]
    assert direction == imgui.Dir.up
    # The arrow's box (65 % of the font) centred on the text's line.
    assert y - starts[0] == pytest.approx(font * 0.5 * (1 - 0.65))

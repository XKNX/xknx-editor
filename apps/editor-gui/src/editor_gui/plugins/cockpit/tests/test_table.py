"""Device Overview can scroll past the pane without shrinking neighboring columns."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.plugins.cockpit.service import CockpitService
from editor_gui.plugins.cockpit.ui import CockpitPanel


@pytest.mark.parametrize(
    ("pane_width", "starts_scrollable"), [(520, True), (680, True), (1000, False)]
)
def test_resizing_name_moves_the_columns_to_its_right(
    pane_width: int, starts_scrollable: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename(None)
    io.display_size = imgui.ImVec2(1200, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    service = Mock(spec=CockpitService)
    service.skipped_count.return_value = 0
    service.rows.return_value = []
    panel = CockpitPanel(service, lambda _node: None, lambda: True)
    end_table = imgui.end_table
    cells: list[internal.ImRect] = []
    header_y = 0.0
    scroll_max = 0.0
    scroll_to_end = False
    viewport_right = 0.0

    def record_table() -> None:
        nonlocal cells, header_y, scroll_max, viewport_right
        table = internal.get_current_table()
        cells = [
            internal.table_get_cell_bg_rect(table, index)
            for index in range(table.columns_count)
        ]
        header_y = table.outer_rect.min.y + 8
        scroll_max = imgui.get_scroll_max_x()
        viewport_right = table.inner_clip_rect.max.x
        if scroll_to_end:
            imgui.set_scroll_x(scroll_max)
        end_table()

    monkeypatch.setattr(imgui, "end_table", record_table)

    def draw() -> None:
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(20, 20))
        imgui.set_next_window_size(imgui.ImVec2(pane_width, 350))
        imgui.begin("Device Overview")
        panel.render()
        imgui.end()
        imgui.end_frame()

    try:
        for _ in range(4):
            draw()
        before = cells
        initial_scroll = scroll_max
        divider = before[1].max.x
        io.add_mouse_pos_event(divider, header_y)
        draw()
        io.add_mouse_button_event(0, True)
        draw()
        io.add_mouse_pos_event(divider + 140, header_y)
        draw()
        draw()
        io.add_mouse_button_event(0, False)
        draw()

        assert cells[1].get_width() == before[1].get_width() + 140
        assert cells[0].get_width() == before[0].get_width()
        for original, moved in zip(before[2:], cells[2:], strict=True):
            assert moved.get_width() == original.get_width()
            assert moved.min.x == original.min.x + 140
        assert (initial_scroll > 0) == starts_scrollable
        assert scroll_max > initial_scroll
        assert cells[-1].max.x > viewport_right

        scroll_to_end = True
        draw()
        draw()
        assert cells[-1].max.x <= viewport_right + 1
    finally:
        imgui.destroy_context(context)

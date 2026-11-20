"""A control's hint sits closer to its control than to the next control."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.text_util import control_hint


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.mark.usefixtures("context")
def test_hint_belongs_to_the_control_above() -> None:
    imgui.new_frame()
    imgui.begin("Settings")
    imgui.checkbox("Connect at Startup", True)
    control_bottom = imgui.get_item_rect_max().y
    control_hint("At start, the editor connects to the interface used last.")
    hint_top, hint_bottom = imgui.get_item_rect_min().y, imgui.get_item_rect_max().y
    imgui.checkbox("Block Bus Writes", False)
    next_top = imgui.get_item_rect_min().y
    imgui.end()
    imgui.end_frame()
    spacing = imgui.get_style().item_spacing.y
    above = hint_top - control_bottom
    below = next_top - hint_bottom
    # close to its control, the next control at the usual distance
    assert 0 <= above < spacing
    assert abs(below - spacing) <= 1

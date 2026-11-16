"""The Labels tool's field checkboxes wrap within the width, and what follows them starts on a
line of its own."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui import tools
from editor_gui.plugins.project.ui.tools import ToolsPanel
from editor_gui.widgets.checkbox import checkbox


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1600, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _panel() -> ToolsPanel:
    def unused(*_args: Any) -> Any:
        raise AssertionError("not called")

    return ToolsPanel(
        get_devices=list,
        get_device_info=lambda _n: None,
        is_open=lambda: True,
        on_extended_copy=unused,
        on_shift_addresses=unused,
        on_navigate=unused,
        on_replace_device=unused,
        get_space_path=lambda _n: "",
        get_device_gas=lambda _d: [],
        get_group_addresses=list,
        get_group_address_style=unused,
        on_shift_group_addresses=unused,
        on_diff_projects=unused,
    )


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("width", [300.0, 700.0, 1500.0])
def test_the_fields_wrap_and_the_next_item_starts_a_line(
    width: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    boxes: list[tuple[imgui.ImVec2, imgui.ImVec2]] = []

    def recorded(label: str, value: bool, **options: Any) -> tuple[bool, bool]:
        result = checkbox(label, value, **options)
        boxes.append((imgui.get_item_rect_min(), imgui.get_item_rect_max()))
        return result

    monkeypatch.setattr(tools, "checkbox", recorded)
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(width, 500))
    imgui.begin("Labels")
    right = imgui.get_window_pos().x + width
    _panel()._render_field_picker()  # pyright: ignore[reportPrivateUsage]
    imgui.text("after")
    after = imgui.get_item_rect_min()
    imgui.end()
    imgui.end_frame()

    assert boxes
    # Each checkbox within the window, after the first beside one or on a new line.
    assert all(high.x <= right for _low, high in boxes)
    rows = {round(low.y) for low, _high in boxes}
    assert (len(rows) > 1) == (width < 1500.0)
    # The next item below the last checkbox, at the start of its line.
    last_high = boxes[-1][1]
    assert after.y >= last_high.y
    assert after.x <= boxes[0][0].x

"""A device's "Compare with…" opens the "Compare Devices" tab of the Tools panel, with that
device as the first one."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui.tools import ToolsPanel


class _Compare:
    def __init__(self) -> None:
        self.primary: int | None = None
        self.rendered = 0

    def set_primary(self, node_id: int) -> None:
        self.primary = node_id

    def render(self) -> None:
        self.rendered += 1


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _panel(compare: _Compare) -> ToolsPanel:
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
        compare=compare,  # type: ignore[arg-type]
    )


def _frame(panel: ToolsPanel) -> None:
    imgui.new_frame()
    imgui.begin("Tools")
    panel.render()
    imgui.end()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_compare_with_shows_the_compare_tab() -> None:
    compare = _Compare()
    panel = _panel(compare)
    _frame(panel)
    # The first tab ("Extended Copy") shows until compare is asked for.
    assert compare.rendered == 0
    panel.open_compare(7)
    for _ in range(2):
        _frame(panel)
    assert compare.primary == 7
    assert compare.rendered >= 1

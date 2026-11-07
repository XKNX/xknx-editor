"""The results table of the recover panel scrolls under its header and leaves room for the action
buttons below it, which change with the phase."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.recover.ui import RecoverPanel


@pytest.fixture(autouse=True)
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    yield
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _panel(project: bool = True) -> RecoverPanel:
    service = SimpleNamespace(
        busy=False,
        entries=[],
        connected=lambda: False,
        chosen=lambda: [],
        can_search_again=lambda: False,
        has_project=lambda: project,
    )
    panel = RecoverPanel.__new__(RecoverPanel)
    panel._service = service  # pyright: ignore[reportAttributeAccessIssue,reportPrivateUsage]
    panel._actions_h = 0.0  # pyright: ignore[reportPrivateUsage]
    return panel


def _actions(panel: RecoverPanel, width: float) -> float:
    imgui.set_next_window_size(imgui.ImVec2(width, 400))
    imgui.begin(f"Recover {width}")
    panel._render_actions()  # pyright: ignore[reportPrivateUsage]
    imgui.end()
    return panel._actions_height()  # pyright: ignore[reportPrivateUsage]


def test_the_actions_take_one_row_where_they_fit() -> None:
    row = imgui.get_frame_height_with_spacing()
    spacing = imgui.get_style().item_spacing.y
    assert _actions(_panel(), 2000) == pytest.approx(row + spacing)
    # A narrow window (a phone) goes on in more rows, and leaves room for them.
    assert _actions(_panel(), 260) >= 3 * row

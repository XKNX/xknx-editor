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


def _height(phase: str, recovered: bool, project: bool) -> float:
    service = SimpleNamespace(
        phase=phase,
        entries=[SimpleNamespace(recovered=object() if recovered else None)],
        has_project=lambda: project,
    )
    panel = RecoverPanel.__new__(RecoverPanel)
    panel._service = service  # pyright: ignore[reportAttributeAccessIssue,reportPrivateUsage]
    return panel._actions_height()  # pyright: ignore[reportPrivateUsage]


def test_room_for_the_actions() -> None:
    row = imgui.get_frame_height_with_spacing()
    line = imgui.get_text_line_height_with_spacing()
    spacing = imgui.get_style().item_spacing.y
    assert _height("idle", recovered=False, project=True) == 0.0
    assert _height("scanned", recovered=False, project=True) == row + spacing
    assert _height("recovered", recovered=True, project=True) == 2 * row + spacing
    assert _height("recovered", recovered=True, project=False) == (
        2 * row + line + spacing
    )

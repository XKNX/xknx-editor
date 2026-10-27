"""The Project Diff tab lists the parameters and group objects of a changed device under it,
collapsed, with their values in the open and in the other project."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import tools
from editor_gui.plugins.project.ui.project_diff import DetailRow, DiffEntry, DiffResult
from editor_gui.plugins.project.ui.tools import ToolsPanel


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _panel() -> ToolsPanel:
    panel = object.__new__(ToolsPanel)
    panel._diff_other_name = "old.xknx"  # pyright: ignore[reportPrivateUsage]
    return panel


def _frame(panel: ToolsPanel, result: DiffResult) -> None:
    imgui.new_frame()
    imgui.begin("Tools")
    panel._render_diff_section(S.TOOLS_DIFF_DEVICES, result.devices)  # pyright: ignore[reportPrivateUsage]
    imgui.end()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_a_changed_device_lists_its_parameters_and_group_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (
        DetailRow("parameter", "Channel A > Mode", "changed", "On", "Off"),
        DetailRow("group_object", "Switch", "changed", "1.001 -> 1/0/1", ""),
    )
    result = DiffResult(
        devices=[DiffEntry("1.1.5", "changed", "name 'A' -> 'B'", rows)],
        group_addresses=[],
    )
    panel = _panel()
    shown: list[str] = []
    real_text_wrapped = imgui.text_wrapped

    def text_wrapped(text: str) -> None:
        shown.append(text)
        real_text_wrapped(text)

    monkeypatch.setattr(tools.imgui, "text_wrapped", text_wrapped)
    _frame(panel, result)
    # Collapsed at first: only the device line.
    assert shown == ["1.1.5  name 'A' -> 'B'"]
    monkeypatch.setattr(tools.imgui, "tree_node", lambda *_a: True)
    monkeypatch.setattr(tools.imgui, "tree_pop", lambda: None)
    shown.clear()
    _frame(panel, result)
    assert shown == [
        "1.1.5  name 'A' -> 'B'",
        "Channel A > Mode",
        "On",
        "Off",
        "Switch",
        "1.001 -> 1/0/1",
        "-",
    ]

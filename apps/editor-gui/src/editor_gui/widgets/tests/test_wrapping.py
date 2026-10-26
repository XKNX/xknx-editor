"""Text broken into lines for the width it has: at spaces, keeping the indent a line starts with
(separator texts indent sub-headlines with leading spaces)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.wrapping import wrap_lines


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(400, 300)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    yield
    imgui.end_frame()
    imgui.destroy_context(ctx)


@pytest.mark.usefixtures("context")
def test_lines_keep_their_indent() -> None:
    assert wrap_lines("  Missing Support", 1000) == ["  Missing Support"]
    assert wrap_lines("First\n    second", 1000) == ["First", "    second"]
    assert wrap_lines("   ", 1000) == ["   "]


@pytest.mark.usefixtures("context")
def test_lines_break_at_spaces() -> None:
    width = imgui.calc_text_size("three four").x
    assert wrap_lines("one two three four five", width) == [
        "one two",
        "three four",
        "five",
    ]
    # Only the first line is indented; the following ones start at the edge.
    assert wrap_lines("  one two three", imgui.calc_text_size("  one two").x) == [
        "  one two",
        "three",
    ]

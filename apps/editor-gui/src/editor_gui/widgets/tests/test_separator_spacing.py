"""The room a parameter page's separators take: a headline is followed by its first row one row
pitch further down, with no extra gap."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from xknxeditor.prod.parser_v2.ui import UiSeparator


@pytest.fixture
def frame(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    # The bold font is loaded with the app's fonts; the default one does here.
    monkeypatch.setattr(
        parameter_widgets, "bold", lambda _scale=1.0: contextlib.nullcontext()
    )
    imgui.new_frame()
    imgui.begin("Parameters")
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


@pytest.mark.usefixtures("frame")
def test_a_headline_takes_no_more_than_a_line() -> None:
    top = imgui.get_cursor_pos_y()
    imgui.align_text_to_frame_padding()
    imgui.text("Startverzögerung")
    line = imgui.get_cursor_pos_y() - top
    top = imgui.get_cursor_pos_y()
    parameter_widgets._render_separator(  # pyright: ignore[reportPrivateUsage]
        UiSeparator(id="S-1", text="Allgemein", hint="Headline")
    )
    # No more room than a line of text beside a field: no gap of its own below it.
    assert imgui.get_cursor_pos_y() - top <= line

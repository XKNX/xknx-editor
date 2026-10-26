"""The room a parameter page's separators take: a headline is followed by its first row one row
pitch further down, with no extra gap."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from editor_gui.widgets.wrapping import LINE_GAP, text_height
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


def _lines(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The lines drawn on the page (their top), recorded."""
    lines: list[float] = []

    class Recorder:
        def add_rect_filled(
            self, low: imgui.ImVec2, _high: imgui.ImVec2, _col: int
        ) -> None:
            lines.append(low.y)

    monkeypatch.setattr(parameter_widgets.imgui, "get_window_draw_list", Recorder)
    return lines


@pytest.mark.usefixtures("frame")
@pytest.mark.parametrize("hint", [None, "HorizontalRuler", "Headline", "Information"])
def test_a_separator_without_text_is_a_line(
    monkeypatch: pytest.MonkeyPatch, hint: str | None
) -> None:
    lines = _lines(monkeypatch)
    parameter_widgets._render_separator(  # pyright: ignore[reportPrivateUsage]
        UiSeparator(id="S-1", text="", hint=hint)
    )
    assert len(lines) == 1


@pytest.mark.usefixtures("frame")
def test_a_ruler_with_text_is_the_text_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = _lines(monkeypatch)
    parameter_widgets._render_separator(  # pyright: ignore[reportPrivateUsage]
        UiSeparator(id="S-1", text="Known Limitations", hint="HorizontalRuler")
    )
    assert lines == []


@pytest.mark.usefixtures("frame")
def test_texts_in_a_row_have_room_between() -> None:
    top = imgui.get_cursor_pos_y()
    parameter_widgets._render_separator(  # pyright: ignore[reportPrivateUsage]
        UiSeparator(id="S-1", text="Note")
    )
    # One line of text (the window is narrow) with 4 px above and the line gap and 4 px below:
    # two texts in a row are 10 px apart.
    pitch = 4.0 + imgui.get_text_line_height() + LINE_GAP + 4.0
    assert imgui.get_cursor_pos_y() - top == pytest.approx(pitch)
    assert pitch - imgui.get_text_line_height() == 10.0


@pytest.mark.usefixtures("frame")
def test_grid_labels_break_after_hyphens() -> None:
    sep = UiSeparator(id="S-1", text="Kanaltransfer-Operation")
    gap = parameter_widgets._CELL_GAP  # pyright: ignore[reportPrivateUsage]
    narrow = imgui.calc_text_size("Kanaltransfer-").x + gap + 1.0
    height = parameter_widgets._celltext_height  # pyright: ignore[reportPrivateUsage]
    assert height(sep, narrow) == pytest.approx(text_height(2))
    assert height(sep, None) == pytest.approx(text_height(1))

"""Rows of a grid: a row of labels only is as high as one with fields, so the one-row grids a page
stacks (e.g. a header row of headlines, then rows of a label and fields) are evenly spaced; the
columns' percentages refer to the width of a separator."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from xknxeditor.prod.parser_v2.ui import (
    EnumWidget,
    UiParameter,
    UiParameterBlock,
    UiSeparator,
)
from xknxeditor.prod.parser_v2.ui.parameter import EnumChoice
from xknxeditor.prod.parser_v2.ui.parameter_block import ParameterBlockLayout


@pytest.fixture
def frame(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(900, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    monkeypatch.setattr(
        parameter_widgets, "bold", lambda _scale=1.0: contextlib.nullcontext()
    )
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(900, 600))
    imgui.begin("Parameters")
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _grid(
    *texts: str,
    widths: tuple[str, ...] = ("2%", "20%", "40%"),
    field: UiParameter | None = None,
) -> UiParameterBlock:
    children: list[UiSeparator | UiParameter] = [
        UiSeparator(id=f"PS-{i}", text=text, cell=f"1,{i + 2}")
        for i, text in enumerate(texts)
    ]
    if field is not None:
        children.append(field)
    return UiParameterBlock(
        id="PB-1",
        layout=ParameterBlockLayout.GRID,
        row_labels=("",),
        column_widths=widths,
        children=tuple(children),
    )


def _height(block: UiParameterBlock) -> float:
    top = imgui.get_cursor_pos_y()
    parameter_widgets._render_grid_block(  # pyright: ignore[reportPrivateUsage]
        Any, block, lambda *_: None, False, "grid"
    )
    return imgui.get_cursor_pos_y() - top


@pytest.mark.usefixtures("frame")
def test_a_row_of_labels_is_as_high_as_a_row_of_fields() -> None:
    choices = (EnumChoice(0, "Deaktiviert", "C-0"), EnumChoice(1, "An", "C-1"))
    field = UiParameter(
        ref_id="P-1",
        label="",
        value="0",
        default_value="0",
        widget=EnumWidget(choices=choices, base=None),  # type: ignore[arg-type]
        cell="1,3",
    )
    device: Any = SimpleNamespace(node_id="D-1", param_errors={})
    top = imgui.get_cursor_pos_y()
    parameter_widgets._render_grid_block(  # pyright: ignore[reportPrivateUsage]
        device, _grid("Info 1", field=field), lambda *_: None, False, "fields"
    )
    with_field = imgui.get_cursor_pos_y() - top
    assert _height(_grid("Prog", "Prog-LED")) == pytest.approx(with_field)


@pytest.mark.usefixtures("frame")
def test_columns_refer_to_the_width_of_a_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starts: list[float] = []
    draw = parameter_widgets._render_cell_separator  # pyright: ignore[reportPrivateUsage]

    def record(sep: UiSeparator, origin: float, row: float, height: float) -> float:
        starts.append(imgui.get_cursor_screen_pos().x)
        return draw(sep, origin, row, height)

    monkeypatch.setattr(parameter_widgets, "_render_cell_separator", record)
    left = imgui.get_cursor_screen_pos().x
    _height(_grid("LED", "Funktion"))
    # 2 % and 20 % of 532 px, on whole pixels.
    assert [x - left for x in starts] == pytest.approx([10.64, 117.04], abs=1.0)

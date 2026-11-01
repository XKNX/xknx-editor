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
    NumberWidget,
    UiButton,
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

    def record(sep: UiSeparator, row: float, height: float) -> tuple[float, float]:
        starts.append(imgui.get_cursor_screen_pos().x)
        return draw(sep, row, height)

    monkeypatch.setattr(parameter_widgets, "_render_cell_separator", record)
    left = imgui.get_cursor_screen_pos().x
    _height(_grid("LED", "Funktion"))
    # 2 % and 20 % of 532 px, on whole pixels.
    assert [x - left for x in starts] == pytest.approx([10.64, 117.04], abs=1.0)


@pytest.mark.usefixtures("frame")
def test_a_row_with_a_button_is_as_high_as_a_row_of_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A button is 2 px higher than a field; it reaches into the row's spacing instead of making
    # the row higher, so a grid of buttons stacks as evenly as the grids around it.
    drawn: list[tuple[float, float, float]] = []
    button = imgui.button

    def record(label: str, size: imgui.ImVec2) -> bool:
        clicked = button(label, size)
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        drawn.append((low.x, high.x, high.y - low.y))
        return clicked

    monkeypatch.setattr(imgui, "button", record)
    block = _grid("Stichprobe 1", widths=("24%", "24%", "24%"))
    block = UiParameterBlock(
        id=block.id,
        layout=block.layout,
        row_labels=block.row_labels,
        column_widths=block.column_widths,
        children=(
            *block.children,
            UiButton(id="B-1", text="Ermitteln (sofort)", cell="1,3"),
        ),
    )
    left = imgui.get_cursor_screen_pos().x
    assert _height(block) == pytest.approx(_height(_grid("Maximum")))
    # 24 px high, 4 px into its cell (at 48 % of 532 px) and 10 px before its end.
    [(low, high, height)] = drawn
    assert height == 24
    assert low - left == pytest.approx(255.36 + 4, abs=1.0)
    assert high - left == pytest.approx(255.36 + 127.68 - 10, abs=1.0)


@pytest.mark.usefixtures("frame")
def test_a_row_with_a_number_field_is_as_high_as_a_row_of_labels() -> None:
    # A number field with spin arrows is 2 px higher than other fields; it reaches into the
    # row's spacing instead of making the row higher.
    style = imgui.get_style()
    # The editor's sizes.
    style.frame_padding = imgui.ImVec2(8.0, 4.0)
    style.item_spacing = imgui.ImVec2(8.0, 6.0)
    style.cell_padding = imgui.ImVec2(6.0, 4.0)
    style.frame_border_size = 1.0
    field = UiParameter(
        ref_id="P-1",
        label="",
        value="4477",
        default_value="4477",
        widget=NumberWidget(min=0, max=65535),
        cell="1,3",
    )
    block = _grid("Trigger", widths=("25%", "12%", "12%"), field=field)
    device: Any = SimpleNamespace(node_id="D-1", param_errors={})
    top = imgui.get_cursor_pos_y()
    parameter_widgets._render_grid_block(  # pyright: ignore[reportPrivateUsage]
        device, block, lambda *_: None, False, "numbers"
    )
    assert imgui.get_cursor_pos_y() - top == pytest.approx(_height(_grid("Halten")))


@pytest.mark.usefixtures("frame")
def test_leading_spaces_move_a_label_as_far_as_they_are_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "    minus": authors line labels up with spaces, which are drawn as spaces.
    drawn: list[str] = []
    wrapped = parameter_widgets.wrapped_text

    def record(text: str, width: float) -> None:
        drawn.append(text)
        wrapped(text, width)

    monkeypatch.setattr(parameter_widgets, "wrapped_text", record)
    _height(_grid("    minus", "  bis"))
    assert drawn == ["    minus", "  bis"]


@pytest.mark.usefixtures("frame")
def test_of_several_things_in_one_cell_the_last_shows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The HF-Sensor page puts a read-only flag and the label "leer" into one cell, and the flag
    # of another and a button into the next: the label and the button show.
    shown: list[str] = []
    label = parameter_widgets._render_cell_separator  # pyright: ignore[reportPrivateUsage]

    def record_label(
        sep: UiSeparator, row: float, height: float
    ) -> tuple[float, float]:
        shown.append(f"label {sep.text.strip() if sep.text else ''}")
        return label(sep, row, height)

    def record_param(_device: Any, param: UiParameter, *_args: Any) -> None:
        shown.append(f"param {param.ref_id}")

    def record_button(_device: Any, button: UiButton, *_args: Any, **_kw: Any) -> None:
        shown.append(f"button {button.id}")

    monkeypatch.setattr(parameter_widgets, "_render_cell_separator", record_label)
    monkeypatch.setattr(parameter_widgets, "_render_grid_param", record_param)
    monkeypatch.setattr(parameter_widgets, "_render_button", record_button)

    def flag(ref: str, cell: str) -> UiParameter:
        choices = (EnumChoice(0, "Nein", "C-0"), EnumChoice(1, "Ja", "C-1"))
        widget = EnumWidget(choices=choices, base=None)  # type: ignore[arg-type]
        return UiParameter(
            ref_id=ref, label="", value="0", default_value="0", widget=widget, cell=cell
        )

    block = UiParameterBlock(
        id="PB-M",
        layout=ParameterBlockLayout.GRID,
        row_labels=("",),
        column_widths=("25%", "14%", "14%"),
        children=(
            flag("P-99901", "1,2"),
            flag("P-99902", "1,3"),
            UiButton(id="B-1", text="", cell="1,2"),
            UiSeparator(id="PS-1", text="       leer", cell="1,3"),
        ),
    )
    _height(block)
    assert shown == ["button B-1", "label leer"]

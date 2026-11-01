"""The rows of a parameter page as the reference tool lays them out: a text row (a read-only value,
or a TypeNone parameter such as an empty line) is a line high, a field row a field high, a
multi-line label makes its row higher, label and value are centred in their row, and a TypeNone
parameter gets no field."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from editor_gui.widgets.dpi import px
from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.parameter_type_t_type_float_encoding import (
    ParameterTypeTypeFloatEncoding,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction_base import (
    ParameterTypeTypeRestrictionBase,
)
from xknxeditor.prod.parser_v2.ui import UiParameter
from xknxeditor.prod.parser_v2.ui.parameter import (
    EnumChoice,
    EnumWidget,
    FloatWidget,
    TextWidget,
)


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    # The app's 14 px font.
    imgui.get_style().font_size_base = 14.0
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(700, 500))
    imgui.begin("Parameters", None, imgui.WindowFlags_.no_saved_settings)
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


_ENUM = EnumWidget(
    (EnumChoice(value=0, label="immer AUS / immer EIN", id="EN-0"),),
    ParameterTypeTypeRestrictionBase.VALUE,
)


def _parameter(
    ref: str, widget: Any, access: Access = Access.READ_WRITE
) -> UiParameter:
    return UiParameter(ref_id=ref, label=ref, value="0", widget=widget, access=access)


def _height(params: list[UiParameter]) -> float:
    device = cast(Any, SimpleNamespace(node_id=1, param_errors={}, param_inputs={}))
    top = imgui.get_cursor_pos_y()
    parameter_widgets._render_param_table(  # pyright: ignore[reportPrivateUsage]
        device, list(params), lambda *_a: None, False, "p"
    )
    # Without the spacing after the table.
    return imgui.get_cursor_pos_y() - top - imgui.get_style().item_spacing.y


@pytest.mark.usefixtures("frame")
def test_a_parameter_without_a_field_draws_none() -> None:
    top = imgui.get_cursor_pos_y()
    parameter_widgets.render_param_widget(
        _parameter("P-5906", None), "w", lambda _v: None
    )
    assert imgui.get_cursor_pos_y() == top
    parameter_widgets.render_param_widget(
        _parameter("P-1", TextWidget(max_length=14)), "t", lambda _v: None
    )
    assert imgui.get_cursor_pos_y() > top


@pytest.mark.usefixtures("frame")
def test_rows_are_as_high_as_in_the_reference_tool() -> None:
    # A text row is 25 px, a field row 31 px, a row with a two-line label 41 px, as the reference
    # tool lays them out at 100 %.
    texts = [
        _parameter("P-5900", _ENUM, Access.READ),
        _parameter("P-5901", _ENUM, Access.READ),
        _parameter("P-5906", None),
    ]
    fields = [
        _parameter(f"P-{n}", TextWidget(max_length=14)) for n in (5900, 5901, 5906)
    ]
    info = replace(
        _parameter("P-5899", _ENUM, Access.READ), label="Info:\r\nWippenfunktion"
    )
    assert _height(texts) == pytest.approx(3 * px(25))
    assert _height(fields) == pytest.approx(3 * px(31))
    assert _height([info]) == pytest.approx(px(41))


@pytest.mark.usefixtures("frame")
def test_label_and_value_are_centred_in_their_row() -> None:
    info = replace(
        _parameter("P-5899", _ENUM, Access.READ), label="Info:\r\nWippenfunktion"
    )
    layout = parameter_widgets._row_layout(info, True, 300.0)  # pyright: ignore[reportPrivateUsage]
    # The value 8 px below the label's first line, in the middle of its two lines.
    assert (layout.height, layout.value_top - layout.label_top) == (px(41), px(8))
    field = parameter_widgets._row_layout(  # pyright: ignore[reportPrivateUsage]
        _parameter("P-1643", _ENUM), False, 300.0
    )
    # A one-line label is level with the field's text.
    frame_padding = imgui.get_style().frame_padding.y
    assert field.label_top == field.value_top + frame_padding


_RADIO = EnumWidget(
    (
        EnumChoice(value=0, label="Nein", id="EN-0"),
        EnumChoice(value=1, label="Ja", id="EN-1"),
    ),
    ParameterTypeTypeRestrictionBase.VALUE,
    radio=True,
)


def _float(increment: float | None) -> FloatWidget:
    return FloatWidget(
        -90.0, 90.0, ParameterTypeTypeFloatEncoding.IEEE_754_SINGLE, increment
    )


@pytest.mark.usefixtures("frame")
@pytest.mark.parametrize(
    ("widget", "height", "control", "offset"),
    [
        # Radio buttons are 24 px, a float without steps a plain text box of 18 px, other
        # fields 22 px; each with 9 px around it.
        (_RADIO, 33, 24, 0),
        (_float(None), 27, 18, 2),
        (_float(0.5), 31, 22, 0),
        (TextWidget(max_length=14), 31, 22, 0),
    ],
)
def test_a_row_is_as_high_as_its_control(
    widget: Any, height: float, control: float, offset: float
) -> None:
    param = _parameter("P-1", widget)
    layout = parameter_widgets._row_layout(param, False, 300.0)  # pyright: ignore[reportPrivateUsage]
    assert _height([param]) == pytest.approx(px(height))
    assert layout.height == pytest.approx(px(height))
    # The label is level with the control's text (a stepless float's field has less padding),
    # half the control's room above a line of text below the content's top.
    frame_padding = imgui.get_style().frame_padding.y
    assert layout.label_top == layout.value_top + frame_padding - px(offset)
    assert layout.label_top == px(4) + (px(control) - px(16)) // 2

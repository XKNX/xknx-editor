"""Rows of a parameter page that show only texts: a read-only value and a parameter without a field
(TypeNone, e.g. an empty line between parameters) take the height of a line of text, not of a
field, and a TypeNone parameter gets no field."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction_base import (
    ParameterTypeTypeRestrictionBase,
)
from xknxeditor.prod.parser_v2.ui import UiParameter
from xknxeditor.prod.parser_v2.ui.parameter import EnumChoice, EnumWidget, TextWidget


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
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
    return imgui.get_cursor_pos_y() - top


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
def test_rows_of_texts_are_as_high_as_a_line() -> None:
    texts = [
        _parameter("P-5900", _ENUM, Access.READ),
        _parameter("P-5901", _ENUM, Access.READ),
        _parameter("P-5906", None),
    ]
    fields = [
        _parameter(f"P-{n}", TextWidget(max_length=14)) for n in (5900, 5901, 5906)
    ]
    text_height, field_height = _height(texts), _height(fields)
    padding = imgui.get_style().frame_padding.y
    assert text_height == pytest.approx(field_height - 3 * 2 * padding, abs=1.0)

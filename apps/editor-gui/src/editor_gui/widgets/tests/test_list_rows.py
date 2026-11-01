"""A parameter list's row is as wide as a separator's row (535 px): its label takes 45 % of it in
whole pixels, and its field ends where the separators end, 3 px before the row's end."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from xknxeditor.prod.parser_v2.ui import EnumWidget, UiParameter
from xknxeditor.prod.parser_v2.ui.parameter import EnumChoice


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


@pytest.mark.usefixtures("frame")
def test_the_field_starts_after_45_percent_and_ends_with_the_separators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rects: list[tuple[float, float]] = []
    draw = parameter_widgets.begin_combo

    def record(label: str, preview: str, flags: int = 0) -> bool:
        left = imgui.get_cursor_screen_pos().x
        rects.append((left, left + imgui.calc_item_width()))
        return draw(label, preview, flags)

    monkeypatch.setattr(parameter_widgets, "begin_combo", record)
    choices = (EnumChoice(0, "Const. Voltage", "C-0"), EnumChoice(1, "Other", "C-1"))
    param = UiParameter(
        ref_id="P-1",
        label="Hardware-Variante",
        value="0",
        default_value="0",
        widget=EnumWidget(choices=choices, base=None),  # type: ignore[arg-type]
    )
    device: Any = SimpleNamespace(node_id="D-1", param_errors={})
    left = imgui.get_cursor_screen_pos().x
    parameter_widgets._render_param_table(  # pyright: ignore[reportPrivateUsage]
        device, [param], lambda *_: None, False, "list"
    )
    (start, end), *_ = rects
    assert start - left == 241.0
    separator = parameter_widgets._SEPARATOR_WIDTH  # pyright: ignore[reportPrivateUsage]
    assert end - left == pytest.approx(separator)

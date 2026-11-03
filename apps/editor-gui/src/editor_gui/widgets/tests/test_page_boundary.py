"""Rendering a parameter page must not trip ImGui's "SetCursorPos used to extend boundaries"
assert at the end of the page child. page() moves the cursor (the top line) and the page's
pixel-exact layout can end on a cursor move too; without a trailing item end_child asserts.
Regression for the crash that opened the Configure view in 0.9.5-0.9.9.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from xknxeditor.prod.parser_v2.ui import NumberWidget, UiParameter


@pytest.fixture
def frame(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(1000, 700)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
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
def test_a_page_that_submits_no_item_does_not_assert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A page whose layout ends on a cursor move and submits no further item: page() moves the
    # cursor for the top line, then the page body adds nothing. end_child must not assert.
    monkeypatch.setattr(parameter_widgets, "_render_page", lambda *a, **k: None)
    device = cast(Any, SimpleNamespace(node_id=1))
    nodes = [
        UiParameter(
            ref_id="P-1",
            label="Startup delay",
            value="0",
            default_value="0",
            widget=NumberWidget(min=0, max=10),
        )
    ]
    parameter_widgets.render_ui_tree(device, nodes, lambda *_: None)

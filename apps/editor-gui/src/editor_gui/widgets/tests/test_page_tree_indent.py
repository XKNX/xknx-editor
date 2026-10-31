"""The page tree's indents: the pages of a channel start where the channel starts, a page below a
page is indented, and spaces in front of a page title indent it."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from editor_gui.widgets.parameter_widgets import build_pages
from xknxeditor.prod.parser_v2.ui import (
    NumberWidget,
    UiParameter,
    UiParameterBlock,
    UiTab,
)


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(400, 500))
    imgui.begin("Pages", None, imgui.WindowFlags_.no_saved_settings)
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _parameter(ref: str) -> UiParameter:
    return UiParameter(
        ref_id=ref,
        label=ref,
        value="0",
        default_value="0",
        widget=NumberWidget(min=0, max=9),
    )


def _page(
    block_id: str, text: str, *children: Any, indent: int = 0
) -> UiParameterBlock:
    return UiParameterBlock(
        id=block_id, text=text, children=children, text_indent=indent
    )


@pytest.mark.usefixtures("frame")
def test_the_pages_of_a_channel_are_not_indented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = UiTab(
        id="CH-0",
        text="Generic",
        children=(
            _page("PB-1", "Allgemein", _parameter("P-1")),
            _page(
                "PB-2",
                "TSM - Wippe 1",
                _parameter("P-2"),
                _page("PB-3", "Sub", _parameter("P-3")),
                indent=3,
            ),
        ),
    )
    pages = build_pages([channel], "1")
    starts: dict[str, float] = {}
    labels: dict[str, str] = {}
    tree_node_ex = imgui.tree_node_ex

    def record(label: str, flags: int) -> bool:
        shown = label.split("###")[0]
        starts[shown.strip()] = imgui.get_cursor_screen_pos().x
        labels[shown.strip()] = shown
        return tree_node_ex(label, flags)

    monkeypatch.setattr(parameter_widgets.imgui, "tree_node_ex", record)
    parameter_widgets._render_page_tree(  # pyright: ignore[reportPrivateUsage]
        pages, None, frozenset(), True, None
    )
    assert starts["Allgemein"] == starts["Generic"]
    assert starts["TSM - Wippe 1"] == starts["Generic"]
    # The levels below the channel's pages are indented.
    assert starts["Sub"] > starts["TSM - Wippe 1"]
    # Spaces in front of a page title indent its text.
    assert labels["TSM - Wippe 1"] == "   TSM - Wippe 1"
    assert labels["Allgemein"] == "Allgemein"

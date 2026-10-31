"""A page in the page tree is bold while the mouse is over it, and while it is selected; its icon
is 16 px."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from editor_gui.widgets.icons import Icon
from editor_gui.widgets.parameter_widgets import build_pages
from xknxeditor.prod.parser_v2.ui import NumberWidget, UiParameter, UiParameterBlock


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


def _page(block_id: str, text: str) -> UiParameterBlock:
    parameter = UiParameter(
        ref_id=f"P-{block_id}",
        label="p",
        value="0",
        default_value="0",
        widget=NumberWidget(min=0, max=9),
    )
    return UiParameterBlock(id=block_id, text=text, children=(parameter,))


@pytest.mark.usefixtures("frame")
def test_hovered_and_selected_pages_are_bold(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = build_pages(
        [
            _page("PB-1", "Allgemein"),
            _page("PB-2", "Kanalkopie"),
            _page("PB-3", "Import"),
        ],
        "1",
    )
    keys = {page.label: page.key for page in pages}
    bold = imgui.get_io().fonts.add_font_default()
    monkeypatch.setattr(parameter_widgets, "bold_font", lambda: bold)
    drawn: dict[str, bool] = {}
    tree_node_ex = imgui.tree_node_ex

    def record(label: str, flags: int) -> bool:
        drawn[label.split("###")[0].strip()] = imgui.get_font() is bold
        return tree_node_ex(label, flags)

    monkeypatch.setattr(parameter_widgets.imgui, "tree_node_ex", record)
    # The mouse is over "Kanalkopie" in the first frame ...
    monkeypatch.setattr(
        parameter_widgets.imgui,
        "is_item_hovered",
        lambda *_args: (
            imgui.get_item_rect_size().x > 0 and _last(drawn) == "Kanalkopie"
        ),
    )
    tree = parameter_widgets._render_page_tree  # pyright: ignore[reportPrivateUsage]
    tree(pages, keys["Import"], frozenset(), True, None)
    assert drawn == {"Allgemein": False, "Kanalkopie": False, "Import": True}
    # ... so it is bold in the next one, as is the selected page.
    tree(pages, keys["Import"], frozenset(), True, None)
    assert drawn == {"Allgemein": False, "Kanalkopie": True, "Import": True}


def _last(drawn: dict[str, bool]) -> str:
    return next(reversed(drawn))


@pytest.mark.usefixtures("frame")
def test_page_icons_are_16_px(monkeypatch: pytest.MonkeyPatch) -> None:
    page = UiParameterBlock(
        id="PB-1",
        text="Kanalkopie",
        children=_page("PB-9", "x").children,
        icon="copy",
    )
    pages = build_pages([page], "1")
    sizes: list[float] = []
    monkeypatch.setattr(
        parameter_widgets, "draw_icon", lambda _icon, _pos, size: sizes.append(size)
    )
    tree = parameter_widgets._render_page_tree  # pyright: ignore[reportPrivateUsage]
    tree(pages, None, frozenset(), True, lambda _name: cast(Icon, object()))
    # As large as in the reference, whatever the text size.
    assert sizes == [16.0]

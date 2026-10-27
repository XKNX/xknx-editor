"""The Editor's "Show in Bus Diagram" on a channel page: the page tree's context menu of a
channel, and the item the Editor puts in it."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.tests._clicks import Clicks
from editor_gui.plugins.project.ui import configure
from editor_gui.plugins.project.ui.configure import ConfigurePanel
from editor_gui.widgets import parameter_widgets
from editor_gui.widgets.parameter_widgets import build_pages
from xknxeditor.prod.parser_v2.ui import NumberWidget, UiParameter, UiTab


@pytest.fixture
def clicks(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clicks]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield Clicks(monkeypatch, parameter_widgets, configure)
    imgui.destroy_context(ctx)


def _frame(draw: Any) -> None:
    imgui.new_frame()
    imgui.begin("Editor")
    draw()
    imgui.end()
    imgui.end_frame()
    imgui.render()


def _pages() -> Any:
    parameter = UiParameter(
        ref_id="P-1",
        label="Mode",
        value="0",
        default_value="0",
        widget=NumberWidget(min=0, max=10),
    )
    return build_pages([UiTab(id="CH-9", text="Logic", children=(parameter,))], "1")


def test_a_channel_page_has_a_context_menu(clicks: Clicks) -> None:
    pages = _pages()
    asked: list[str] = []

    def draw() -> None:
        parameter_widgets._render_page_tree(  # pyright: ignore[reportPrivateUsage]
            pages, None, frozenset(), False, None, channel_menu=asked.append
        )

    _frame(draw)
    clicks.context = f"##page_ctx_{pages[0].key}"
    _frame(draw)
    assert asked == ["CH-9"]


def test_the_editor_shows_a_channel_in_the_bus_diagram(clicks: Clicks) -> None:
    shown: list[tuple[int, str]] = []

    def unused(*_args: Any) -> None:
        raise AssertionError("not expected")

    def panel(on_show_links: Any) -> ConfigurePanel:
        return ConfigurePanel(
            get_devices=list,
            get_selected_device=lambda: None,
            set_selected_device=unused,
            on_param_change=unused,
            on_individual_address_change=unused,
            on_name_change=unused,
            on_description_change=unused,
            set_flag=unused,
            get_links_for_com_object=cast(Any, unused),
            get_all_group_addresses=cast(Any, unused),
            on_link_com_object=unused,
            on_unlink_com_object=unused,
            on_auto_create_gas=unused,
            get_device_info=lambda _node_id: None,
            on_show_links=on_show_links,
        )

    device = cast(Any, SimpleNamespace(node_id=7))
    assert panel(None)._channel_menu(device) is None  # pyright: ignore[reportPrivateUsage]
    editor = panel(lambda node_id, channel: shown.append((node_id, channel)))
    menu = editor._channel_menu(device)  # pyright: ignore[reportPrivateUsage]
    assert menu is not None
    _frame(lambda: menu("CH-9"))
    assert shown == []
    clicks.items.add(S.CONTEXT_SHOW_IN_BUS_DIAGRAM)
    _frame(lambda: menu("CH-9"))
    assert shown == [(7, "CH-9")]

"""The line above a page's help is drawn in the color of the page's separator lines."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui.configure import ConfigurePanel
from editor_gui.widgets.backgrounds import page_line


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.begin("Help")
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _panel() -> ConfigurePanel:
    def unused(*_args: Any) -> None:
        raise AssertionError("not expected")

    device = cast(Any, SimpleNamespace(node_id=1, app=None, name="", description=""))
    return ConfigurePanel(
        get_devices=lambda: [device],
        get_selected_device=lambda: device,
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
        get_ip_secure=cast(Any, unused),
        application_ready=lambda _d: True,
    )


@pytest.mark.usefixtures("frame")
def test_the_line_above_the_help_has_the_color_of_the_page_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    colors: list[int] = []
    draw = imgui.ImDrawList.add_rect_filled

    def record(
        self: imgui.ImDrawList,
        low: imgui.ImVec2,
        high: imgui.ImVec2,
        col: int,
        *args: Any,
    ) -> None:
        colors.append(col)
        draw(self, low, high, col, *args)

    monkeypatch.setattr(imgui.ImDrawList, "add_rect_filled", record)
    _panel()._render_help_splitter()  # pyright: ignore[reportPrivateUsage]
    assert colors[0] == imgui.get_color_u32(page_line())


@pytest.mark.usefixtures("frame")
def test_the_help_text_starts_17_px_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    insets: list[tuple[float, float]] = []

    def record(_text: str) -> None:
        cursor = imgui.get_cursor_screen_pos()
        window = imgui.get_window_pos()
        insets.append((cursor.x - window.x, cursor.y - window.y))
        imgui.dummy(imgui.ImVec2(1, 1))

    monkeypatch.setattr(
        "editor_gui.plugins.project.ui.configure.render_markdown", record
    )
    _panel()._render_help("# Informationen zum Sensor", 200)  # pyright: ignore[reportPrivateUsage]
    # 17 px from the help's left edge, 13 px below its top.
    assert insets == [(17, 13)]

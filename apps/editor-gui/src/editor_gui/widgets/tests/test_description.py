from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.description import (
    ICON_DOWNLOAD,
    MIN_DESCRIPTION_LENGTH,
    render_device_marks,
    set_pending_downloads,
    shows_description_icon,
)


def test_none_and_empty_get_no_icon() -> None:
    assert shows_description_icon(None) is False
    assert shows_description_icon("") is False


def test_short_description_gets_no_icon() -> None:
    assert shows_description_icon("x" * MIN_DESCRIPTION_LENGTH) is False
    assert shows_description_icon("Küche EG") is False


def test_long_description_gets_an_icon() -> None:
    assert shows_description_icon("x" * (MIN_DESCRIPTION_LENGTH + 1)) is True
    assert shows_description_icon("Taster links, Dimmen langer Tastendruck") is True


def test_multi_line_description_gets_an_icon() -> None:
    assert shows_description_icon("EG\nKüche") is True
    assert shows_description_icon("Küche EG\n") is False


def test_whitespace_only_gets_no_icon() -> None:
    assert shows_description_icon("   " * 10) is False


def test_pending_marker_shows_details_on_click_and_disappears_when_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    pending = {7: "Download pending: Parameters, Group Addresses"}
    shown: list[str] = []
    positions: list[tuple[float, float]] = []
    real_colored, real_text = imgui.text_colored, imgui.text_unformatted

    def colored(color: imgui.ImVec4, text: str) -> None:
        if text == ICON_DOWNLOAD:
            pos = imgui.get_cursor_screen_pos()
            positions.append((pos.x + 2, pos.y + 2))
        real_colored(color, text)

    def text(value: str, *args: Any) -> None:
        shown.append(value)
        real_text(value, *args)

    monkeypatch.setattr(imgui, "text_colored", colored)
    monkeypatch.setattr(imgui, "text_unformatted", text)
    set_pending_downloads(lambda node_id: pending.get(node_id, ""))

    def frame() -> bool:
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(500, 400))
        imgui.begin("Tree")
        imgui.selectable("Device", False)
        render_device_marks(7, "", show_pending=True)
        opened = imgui.is_popup_open("##pending_download_7")
        imgui.end()
        imgui.end_frame()
        return opened

    try:
        frame()
        frame()
        assert positions
        io.add_mouse_pos_event(*positions[-1])
        frame()
        assert pending[7] in shown
        io.add_mouse_button_event(0, True)
        assert frame()
        io.add_mouse_button_event(0, False)
        io.add_mouse_pos_event(400, 300)
        shown.clear()
        assert frame()
        assert pending[7] in shown
        pending.clear()
        positions.clear()
        frame()
        assert positions == []
    finally:
        set_pending_downloads(None)
        imgui.destroy_context(ctx)

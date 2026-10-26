"""A swipe on a menu: it scrolls the menu under the finger, never the menu a submenu opened
from."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.web.touch import ImGuiTouchUi


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(390, 300)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


# Where the frame drew the items a test moves the finger to.
_spots: dict[str, imgui.ImVec2] = {}


def _frame() -> None:
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(390, 300))
    imgui.begin(
        "Main", flags=imgui.WindowFlags_.menu_bar | imgui.WindowFlags_.no_title_bar
    )
    if imgui.begin_menu_bar():
        view = imgui.begin_menu("View")
        _spots["View"] = imgui.get_item_rect_min()
        if view:
            # Longer than the screen: the menu scrolls.
            for i in range(40):
                imgui.menu_item(f"Item {i}", "", False)
            theme = imgui.begin_menu("Theme")
            _spots["Theme"] = imgui.get_item_rect_min()
            if theme:
                imgui.menu_item("Light", "", False)
                imgui.menu_item("Dark", "", False)
                imgui.end_menu()
            imgui.end_menu()
        imgui.end_menu_bar()
    imgui.end()
    imgui.end_frame()
    imgui.render()


def _point(spot: imgui.ImVec2, frames: int = 3) -> None:
    imgui.get_io().add_mouse_pos_event(spot.x + 5, spot.y + 5)
    for _ in range(frames):
        _frame()


def _click(spot: imgui.ImVec2) -> None:
    io = imgui.get_io()
    _point(spot, 1)
    io.add_mouse_button_event(0, True)
    _frame()
    io.add_mouse_button_event(0, False)
    _frame()


def _menus() -> list[imgui.internal.Window]:
    return [
        w
        for w in imgui.get_current_context().windows
        if w.flags & imgui.WindowFlags_.child_menu and w.active
    ]


def test_swipe_on_a_submenu_scrolls_nothing_behind_it(context: None) -> None:
    _frame()
    _click(_spots["View"])
    (view,) = _menus()
    assert view.scroll_max.y > 0.0
    ui = ImGuiTouchUi(imgui.get_io())
    _point(view.pos)
    assert ui.scroll_target(vertical=True) == view.name
    # Scrolled to its end, the menu shows the Theme submenu's item.
    view.scroll.y = view.scroll_max.y
    _frame()
    _point(_spots["Theme"])
    (submenu,) = [w for w in _menus() if w.name != view.name]
    assert submenu.scroll_max.y == 0.0
    _point(imgui.ImVec2(submenu.pos.x + 10, submenu.pos.y + 10))
    assert ui.pointer_window() == submenu.root_window.id_
    # The swipe stays on the submenu (it has nothing to scroll), not on the View menu.
    assert ui.scroll_target(vertical=True) == submenu.name
    assert not ui.scroll(submenu.name, 0.0, 80.0)
    assert view.scroll.y == view.scroll_max.y

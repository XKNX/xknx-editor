"""With a finger, a tab bar whose tabs do not fit shows no arrow buttons, and a sideways swipe on
it moves its tabs."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui import web
from editor_gui.web.touch import ImGuiTouchUi
from editor_gui.widgets.tabs import begin_tab_bar, shown_tab_bar_ids


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frame() -> imgui.internal.TabBar:
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(160, 200))
    imgui.begin("Panel")
    if begin_tab_bar("##tabs"):
        for name in ("First", "Second", "Third", "Fourth", "Fifth", "Sixth"):
            if imgui.begin_tab_item(name)[0]:
                imgui.end_tab_item()
        imgui.end_tab_bar()
    imgui.end()
    imgui.end_frame()
    imgui.render()
    (tab_bar_id,) = shown_tab_bar_ids()
    return imgui.internal.tab_bar_find_by_id(tab_bar_id)


def test_swipe_moves_the_tabs_of_a_full_tab_bar(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "touch_used", lambda: True)
    for _ in range(3):
        tab_bar = _frame()
    assert not tab_bar.scroll_button_enabled
    bar = tab_bar.bar_rect
    imgui.get_io().add_mouse_pos_event(bar.min.x + 20, (bar.min.y + bar.max.y) / 2)
    tab_bar = _frame()
    ui = ImGuiTouchUi(imgui.get_io())
    target = ui.scroll_target(vertical=False)
    assert target is not None
    assert ui.scroll_target(vertical=True) != target
    # The finger moves left: the tabs follow it, and stop at the end.
    assert ui.scroll(target, -40.0, 0.0)
    assert tab_bar.scrolling_target == tab_bar.scrolling_anim == 40.0
    assert ui.scroll(target, -10000.0, 0.0)
    end = tab_bar.scrolling_target
    assert end == tab_bar.width_all_tabs - (bar.max.x - bar.min.x)
    assert not ui.scroll(target, -10.0, 0.0)
    assert ui.scroll(target, 10000.0, 0.0)
    assert tab_bar.scrolling_target == 0.0


def test_the_mouse_keeps_the_arrow_buttons(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "touch_used", lambda: False)
    for _ in range(3):
        tab_bar = _frame()
    assert tab_bar.scroll_button_enabled

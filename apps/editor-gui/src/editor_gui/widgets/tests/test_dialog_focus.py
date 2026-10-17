"""A dialog asking for a password has its field focused when it appears, so typing goes into it."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.dialog import begin_dialog, end_dialog, focus_when_appearing


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _field_active(focus: bool) -> list[bool]:
    active: list[bool] = []
    for frame in range(3):
        imgui.new_frame()
        if frame == 0:
            imgui.open_popup("Password")
        if begin_dialog("Password", width=300):
            if focus:
                focus_when_appearing()
            imgui.input_text("##password", "", imgui.InputTextFlags_.password)
            field = imgui.get_item_id()
            end_dialog()
            active.append(imgui.get_current_context().active_id == field)
        imgui.end_frame()
        imgui.render()
    return active


def test_the_field_has_the_focus_once_the_dialog_is_shown(context: None) -> None:
    assert _field_active(focus=True)[1:] == [True, True]


def test_without_it_nothing_is_focused(context: None) -> None:
    assert not any(_field_active(focus=False))

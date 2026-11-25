"""Animations are cheap in the browser view: they move in steps there, and the spinner and the
bar of a task without a total are drawn in draw lists of their own."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui import web
from editor_gui.widgets import animation, spinner


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def test_the_clock_runs_smoothly_on_the_desktop_and_in_steps_in_the_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(imgui, "get_time", lambda: 1.23)
    monkeypatch.setattr(web, "active_hub", None)
    assert animation.animation_time() == 1.23
    monkeypatch.setattr(web, "active_hub", object())
    # Twice a second, an eighth of a second on each time.
    assert animation.animation_time() == 0.25
    monkeypatch.setattr(imgui, "get_time", lambda: 1.49)
    assert animation.animation_time() == 0.25
    monkeypatch.setattr(imgui, "get_time", lambda: 1.5)
    assert animation.animation_time() == 0.375


def _child_windows(draw: Any) -> list[tuple[str, imgui.ImVec2]]:
    children: list[tuple[str, imgui.ImVec2]] = []
    real = imgui.begin_child

    def begin_child(str_id: str, size: imgui.ImVec2, *args: Any) -> bool:
        children.append((str_id, size))
        return real(str_id, size, *args)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(imgui, "begin_child", begin_child)
        imgui.new_frame()
        imgui.begin("Window")
        draw()
        imgui.end()
        imgui.end_frame()
    return children


@pytest.mark.usefixtures("context")
def test_the_spinner_has_a_draw_list_of_its_own() -> None:
    children = _child_windows(lambda: spinner.loading("##loading", "Loading"))
    assert [name for name, _ in children] == ["##loading"]


@pytest.mark.usefixtures("context")
def test_a_busy_bar_has_a_draw_list_of_its_own_as_wide_as_asked() -> None:
    def draw() -> None:
        avail = imgui.get_content_region_avail().x
        spinner.busy_bar("##busy", imgui.ImVec2(-1.0, 0.0), "Reading")
        spinner.busy_bar("##fixed", imgui.ImVec2(120.0, 20.0))
        sizes.append(avail)

    sizes: list[float] = []
    children = _child_windows(draw)
    (busy, wide), (fixed, small) = children
    assert (busy, fixed) == ("##busy", "##fixed")
    # The rest of the line less 1, a field high; or the size given.
    assert (wide.x, wide.y) == (sizes[0] - 1.0, imgui.get_frame_height())
    assert (small.x, small.y) == (120.0, 20.0)


def test_a_progress_dialog_redraws_less_often_in_the_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui import main

    monkeypatch.setattr(web, "active_hub", None)
    desktop = main.progress_fps()
    monkeypatch.setattr(web, "active_hub", object())
    assert main.progress_fps() < desktop
    # As often as its animations move.
    assert main.progress_fps() == animation.WEB_STEPS

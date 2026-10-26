"""The texts and check marks of a parameter page lean from their color towards the page's
background (lighter on a light page, darker on a dark one), headlines less so: the reference's
thin type."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import parameter_widgets
from editor_gui.widgets.backgrounds import content_background


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.begin("Parameters")
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _red(col: int) -> float:
    return imgui.get_style_color_vec4(col).x


@pytest.mark.usefixtures("frame")
@pytest.mark.parametrize("light", [True, False])
def test_page_colors_lean_towards_the_background(light: bool) -> None:
    if light:
        imgui.style_colors_light()
    else:
        imgui.style_colors_dark()
    text = _red(imgui.Col_.text)
    mark = _red(imgui.Col_.check_mark)
    background = content_background().x
    with parameter_widgets._page_colors():  # pyright: ignore[reportPrivateUsage]
        texts = _red(imgui.Col_.text)
        marks = _red(imgui.Col_.check_mark)
        with parameter_widgets._headline_color():  # pyright: ignore[reportPrivateUsage]
            headlines = _red(imgui.Col_.text)
    # Texts and check marks lean towards the background, headlines less than texts.
    assert abs(texts - background) < abs(text - background)
    assert abs(marks - background) < abs(mark - background)
    assert abs(text - headlines) < abs(text - texts)
    assert abs(headlines - background) < abs(text - background)
    # The colors are back after the page.
    assert _red(imgui.Col_.text) == text
    assert _red(imgui.Col_.check_mark) == mark

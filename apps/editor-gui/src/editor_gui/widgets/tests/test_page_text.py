"""The labels and texts of a parameter page lean from the text color towards the page's background
(lighter on a light page, darker on a dark one), headlines less so: the reference's thin type."""

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


def _text_inside(soften: float) -> float:
    with parameter_widgets._page_text(soften):  # pyright: ignore[reportPrivateUsage]
        return imgui.get_style_color_vec4(imgui.Col_.text).x


@pytest.mark.usefixtures("frame")
@pytest.mark.parametrize("light", [True, False])
def test_page_text_leans_towards_the_background(light: bool) -> None:
    if light:
        imgui.style_colors_light()
    else:
        imgui.style_colors_dark()
    text = imgui.get_style_color_vec4(imgui.Col_.text).x
    background = content_background().x
    texts = _text_inside(parameter_widgets._TEXT_SOFTEN)  # pyright: ignore[reportPrivateUsage]
    headlines = _text_inside(parameter_widgets._HEADLINE_SOFTEN)  # pyright: ignore[reportPrivateUsage]
    # Headlines lean less than texts, both towards the background, and the color is back after.
    assert abs(text - headlines) < abs(text - texts)
    assert abs(texts - background) < abs(text - background)
    assert imgui.get_style_color_vec4(imgui.Col_.text).x == text

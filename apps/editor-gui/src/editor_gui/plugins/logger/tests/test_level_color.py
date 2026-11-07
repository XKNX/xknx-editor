"""The Log panel shows warnings and errors in the status colors of the theme, so they read on
light and dark windows alike; other records in the theme's text colors."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.logger.ui import level_color
from editor_gui.theme import status_color


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    yield
    imgui.destroy_context(ctx)


def _rgb(color: imgui.ImVec4) -> tuple[float, float, float, float]:
    return (color.x, color.y, color.z, color.w)


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("light", [True, False])
@pytest.mark.parametrize(
    ("level", "kind"),
    [("warning", "warning"), ("error", "error"), ("critical", "error")],
)
def test_problems_in_status_colors(light: bool, level: str, kind: str) -> None:
    if light:
        imgui.style_colors_light()
    else:
        imgui.style_colors_dark()
    assert _rgb(level_color(level)) == _rgb(status_color(kind))


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize(
    ("level", "col"),
    [
        ("debug", imgui.Col_.text_disabled),
        ("info", imgui.Col_.text),
        ("unknown", imgui.Col_.text),
    ],
)
def test_other_records_in_theme_text_colors(level: str, col: imgui.Col_) -> None:
    imgui.style_colors_light()
    assert _rgb(level_color(level)) == _rgb(imgui.get_style().color_(col))

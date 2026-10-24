"""Accent colours follow the theme: darker where the theme draws dark text (light themes), so they
stay readable on white."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from editor_gui.color import sending_green, warning_red
from imgui_bundle import imgui


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    yield
    imgui.destroy_context(ctx)


def _luminance(color: imgui.ImVec4) -> float:
    return 0.299 * color.x + 0.587 * color.y + 0.114 * color.z


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("accent", [sending_green, warning_red])
def test_accents_are_darker_on_light_themes(
    accent: Callable[[], imgui.ImVec4],
) -> None:
    imgui.style_colors_dark()
    on_dark = accent()
    imgui.style_colors_light()
    on_light = accent()
    assert _luminance(on_light) < 0.4 < _luminance(on_dark)

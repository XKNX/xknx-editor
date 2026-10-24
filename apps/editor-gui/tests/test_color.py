"""Accent colours follow the theme: darker where the theme draws dark text (light themes), so they
stay readable on white."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from editor_gui.color import sending_green, themed, warning_red
from editor_gui.plugins.project.ui import group_addresses
from editor_gui.widgets import group_objects_widgets
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


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize(
    "pair",
    [
        # The bus values of the Group Objects list, by the object's direction.
        group_objects_widgets._SENT_COLOR,
        group_objects_widgets._RECEIVED_COLOR,
        group_objects_widgets._BOTH_COLOR,
        group_objects_widgets._LIVE_COLOR,
        # The bus value in the group address view.
        group_addresses._LIVE_COLOR,
    ],
)
def test_bus_values_are_darker_on_light_themes(
    pair: tuple[imgui.ImVec4, imgui.ImVec4],
) -> None:
    imgui.style_colors_dark()
    on_dark = themed(*pair)
    imgui.style_colors_light()
    on_light = themed(*pair)
    assert on_dark is pair[0]
    assert on_light is pair[1]
    assert _luminance(on_light) < 0.45 < _luminance(on_dark)

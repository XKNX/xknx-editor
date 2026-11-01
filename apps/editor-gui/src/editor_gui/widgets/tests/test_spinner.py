"""The loading animation shows on any background: its arc in the link color, its ring in the text
color, faint; the text of what loads follows it on its line."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import spinner


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.begin("Loading")
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _rgba(color: imgui.ImColor) -> tuple[float, float, float, float]:
    value = color.value
    return value.x, value.y, value.z, value.w


@pytest.mark.usefixtures("frame")
@pytest.mark.parametrize("light", [True, False])
def test_the_arc_and_the_ring_show_on_the_window(
    monkeypatch: pytest.MonkeyPatch, light: bool
) -> None:
    style = imgui.get_style()
    if light:
        imgui.style_colors_light(style)
    drawn: list[dict[str, Any]] = []
    monkeypatch.setattr(
        spinner.imspinner,
        "spinner_ang",
        lambda label, radius, thickness, **colors: drawn.append(colors),
    )
    spinner.spinner("##loading")
    link = style.color_(imgui.Col_.text_link)
    text = style.color_(imgui.Col_.text)
    assert _rgba(drawn[0]["color"]) == pytest.approx((link.x, link.y, link.z, link.w))
    ring = _rgba(drawn[0]["bg"])
    assert ring[:3] == pytest.approx((text.x, text.y, text.z))
    assert 0.0 < ring[3] < 0.5
    # Not the theme's selected tab, which is as light as the window in a light theme.
    window = style.color_(imgui.Col_.window_bg)
    assert abs(link.z - window.z) + abs(link.x - window.x) > 0.3


@pytest.mark.usefixtures("frame")
def test_loading_puts_the_text_after_the_animation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(spinner, "spinner", lambda str_id: calls.append(str_id))
    monkeypatch.setattr(spinner.imgui, "text_disabled", calls.append)
    spinner.loading("##loading", "Loading application program…")
    assert calls == ["##loading", "Loading application program…"]

"""The loading animation shows on any background: its arc in the link color, its ring in the text
color, faint; the text of what loads follows it on its line."""

from __future__ import annotations

import math
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


def _rgba(color: int) -> tuple[float, float, float, float]:
    value = imgui.color_convert_u32_to_float4(color)
    return value.x, value.y, value.z, value.w


@pytest.mark.usefixtures("frame")
@pytest.mark.parametrize("light", [True, False])
def test_the_arc_and_the_ring_show_on_the_window(
    monkeypatch: pytest.MonkeyPatch, light: bool
) -> None:
    style = imgui.get_style()
    if light:
        imgui.style_colors_light(style)
    rings: list[int] = []
    arcs: list[int] = []
    circle = imgui.ImDrawList.add_circle
    stroke = imgui.ImDrawList.path_stroke

    def ring(
        self: imgui.ImDrawList, centre: Any, radius: float, col: int, *a: Any
    ) -> None:
        rings.append(col)
        circle(self, centre, radius, col, *a)

    def arc(self: imgui.ImDrawList, col: int, *a: Any) -> None:
        arcs.append(col)
        stroke(self, col, *a)

    monkeypatch.setattr(imgui.ImDrawList, "add_circle", ring)
    monkeypatch.setattr(imgui.ImDrawList, "path_stroke", arc)
    spinner.spinner("##loading")
    link = style.color_(imgui.Col_.text_link)
    text = style.color_(imgui.Col_.text)
    assert _rgba(arcs[0]) == pytest.approx((link.x, link.y, link.z, link.w), abs=0.01)
    faint = _rgba(rings[0])
    assert faint[:3] == pytest.approx((text.x, text.y, text.z), abs=0.01)
    assert 0.0 < faint[3] < 0.5
    # Not the theme's selected tab, which is as light as the window in a light theme.
    window = style.color_(imgui.Col_.window_bg)
    assert abs(link.z - window.z) + abs(link.x - window.x) > 0.3


@pytest.mark.usefixtures("frame")
def test_loading_puts_the_text_after_the_animation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(spinner, "spinner", lambda str_id: calls.append(str_id))
    text = spinner.imgui.text_disabled
    monkeypatch.setattr(
        spinner.imgui, "text_disabled", lambda value: (calls.append(value), text(""))
    )
    spinner.loading("##loading", "Loading application program…")
    assert calls == ["##loading", "Loading application program…"]


@pytest.mark.usefixtures("frame")
def test_the_ring_is_centred_on_its_text(monkeypatch: pytest.MonkeyPatch) -> None:
    rings: list[float] = []
    texts: list[float] = []
    end_child = imgui.end_child

    def ring_drawn() -> None:
        # The ring's own window is the item just drawn once it ends.
        end_child()
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        rings.append((low.y + high.y) / 2)

    text = spinner.imgui.text_disabled

    def label(value: str) -> None:
        text(value)
        low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        texts.append((low.y + high.y) / 2)

    monkeypatch.setattr(spinner.imgui, "end_child", ring_drawn)
    monkeypatch.setattr(spinner.imgui, "text_disabled", label)
    # A text after the ring, and a ring after a text.
    spinner.loading("##loading", "Applikationsprogramm wird geladen…")
    spinner.imgui.text_disabled("Gefundene Gateways")
    imgui.same_line()
    spinner.spinner("##scan")
    assert rings == pytest.approx(texts, abs=0.5)


@pytest.mark.usefixtures("frame")
def test_the_arc_turns_once_a_second(monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[float] = []
    arc_to = imgui.ImDrawList.path_arc_to

    def arc(
        self: imgui.ImDrawList, centre: Any, radius: float, a_min: float, *a: Any
    ) -> None:
        starts.append(a_min)
        arc_to(self, centre, radius, a_min, *a)

    monkeypatch.setattr(imgui.ImDrawList, "path_arc_to", arc)
    for now in (0.0, 0.25, 1.0):
        monkeypatch.setattr(spinner, "animation_time", lambda now=now: now)
        spinner.spinner(f"##turn{now}")
    # A quarter of a second: a quarter turn; a second: a whole one.
    assert starts == pytest.approx([0.0, math.pi / 2, 2 * math.pi])


def _label_top() -> float:
    """Where a text drawn now begins: the cursor and the line's text offset."""
    window = spinner.internal.get_current_window()
    return imgui.get_cursor_screen_pos().y + window.dc.curr_line_text_base_offset


@pytest.mark.usefixtures("frame")
@pytest.mark.parametrize("beside_button", [True, False])
def test_the_text_is_at_the_height_of_a_buttons_label(
    monkeypatch: pytest.MonkeyPatch, beside_button: bool
) -> None:
    tops: list[float] = []
    text = spinner.imgui.text_disabled

    def record(value: str) -> None:
        tops.append(_label_top())
        text(value)

    monkeypatch.setattr(spinner.imgui, "text_disabled", record)
    line = imgui.get_cursor_screen_pos().y
    if beside_button:
        imgui.button("Open Website")
        imgui.same_line()
    spinner.loading("##loading", "Loading products...")
    # Beside a button its label's height (the frame padding down), else the line's top.
    padding = imgui.get_style().frame_padding.y if beside_button else 0.0
    assert tops == [pytest.approx(line + padding)]

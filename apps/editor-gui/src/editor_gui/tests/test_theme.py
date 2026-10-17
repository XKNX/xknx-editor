"""Theme and style color names as words, and the editor's style on top of a theme."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from imgui_bundle import hello_imgui, imgui

from editor_gui import theme


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Text", "Text"),
        ("FrameBgHovered", "Field Background (Hovered)"),
        ("TitleBgCollapsed", "Title Background (Collapsed)"),
        ("ScrollbarGrabActive", "Scrollbar Grip (Active)"),
        ("TableRowBgAlt", "Table Row Background Alternate"),
        ("NavWindowingDimBg", "Navigation Windowing Dim Background"),
        ("TabDimmedSelected", "Tab (Dimmed Selected)"),
    ],
)
def test_color_names(name: str, expected: str) -> None:
    assert theme.color_name(name) == expected


def test_every_theme_has_a_name_with_spaces() -> None:
    names = [theme.theme_name(t) for t in theme.themes()]
    assert len(names) == len(set(names))
    assert (
        theme.theme_name(hello_imgui.ImGuiTheme_.darcula_darker) == "Darcula (Darker)"
    )
    assert all("_" not in name for name in names)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    yield
    imgui.destroy_context(ctx)


@pytest.mark.parametrize("rounding", [-1.0, 9.0])
def test_the_editor_style_keeps_an_adjusted_rounding(
    context: None, monkeypatch: pytest.MonkeyPatch, rounding: float
) -> None:
    tweaks = hello_imgui.ImGuiThemeTweaks()
    tweaks.rounding = rounding
    params = SimpleNamespace(
        imgui_window_params=SimpleNamespace(
            tweaked_theme=SimpleNamespace(tweaks=tweaks)
        )
    )
    monkeypatch.setattr(hello_imgui, "get_runner_params", lambda: params)
    style = imgui.get_style()
    style.frame_rounding = 9.0
    theme.apply_style()
    assert style.frame_rounding == (4.0 if rounding < 0 else 9.0)
    assert style.frame_padding.x == 8.0

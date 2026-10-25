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


@pytest.mark.parametrize(
    ("light", "expected"),
    [
        (True, hello_imgui.ImGuiTheme_.microsoft_style),
        (False, hello_imgui.ImGuiTheme_.darcula_darker),
        (None, hello_imgui.ImGuiTheme_.darcula_darker),
    ],
)
def test_system_theme_follows_the_light_or_dark_preference(
    monkeypatch: pytest.MonkeyPatch,
    light: bool | None,
    expected: hello_imgui.ImGuiTheme_,
) -> None:
    monkeypatch.setattr(theme.darkdetect, "isLight", lambda: light)
    assert theme.system_theme() == expected


def test_system_theme_is_dark_when_the_preference_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> bool:
        raise OSError

    monkeypatch.setattr(theme.darkdetect, "isLight", fail)
    assert theme.system_theme() == hello_imgui.ImGuiTheme_.darcula_darker


def test_every_theme_has_a_name_with_spaces() -> None:
    names = [theme.theme_name(t) for t in theme.themes()]
    assert len(names) == len(set(names))
    assert (
        theme.theme_name(hello_imgui.ImGuiTheme_.darcula_darker) == "Darcula (Darker)"
    )
    assert all("_" not in name for name in names)


def test_theme_menu_offers_the_light_and_the_dark_theme_first() -> None:
    first, others = theme.theme_menu_groups()
    assert first == [
        ("Light Theme - Microsoft Style", hello_imgui.ImGuiTheme_.microsoft_style),
        ("Dark Theme - Darcula (Darker)", hello_imgui.ImGuiTheme_.darcula_darker),
    ]
    # Every other theme once, in the order of the theme list, without the two above.
    assert [t for _, t in others] == [
        t for t in theme.themes() if t not in (theme.LIGHT_THEME, theme.DARK_THEME)
    ]
    assert all(label == theme.theme_name(t) for label, t in others)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    yield
    imgui.destroy_context(ctx)


def _use_theme(
    monkeypatch: pytest.MonkeyPatch,
    tweaks: hello_imgui.ImGuiThemeTweaks,
    applied: hello_imgui.ImGuiTheme_ = hello_imgui.ImGuiTheme_.darcula_darker,
) -> None:
    params = SimpleNamespace(
        imgui_window_params=SimpleNamespace(
            tweaked_theme=SimpleNamespace(tweaks=tweaks, theme=applied)
        )
    )
    monkeypatch.setattr(hello_imgui, "get_runner_params", lambda: params)


@pytest.mark.parametrize("rounding", [-1.0, 9.0])
def test_the_editor_style_keeps_an_adjusted_rounding(
    context: None, monkeypatch: pytest.MonkeyPatch, rounding: float
) -> None:
    tweaks = hello_imgui.ImGuiThemeTweaks()
    tweaks.rounding = rounding
    _use_theme(monkeypatch, tweaks)
    style = imgui.get_style()
    style.frame_rounding = 9.0
    theme.apply_style()
    assert style.frame_rounding == (4.0 if rounding < 0 else 9.0)
    assert style.frame_padding.x == 8.0


@pytest.mark.parametrize(
    ("applied", "rounding"),
    [
        (hello_imgui.ImGuiTheme_.microsoft_style, 0.0),
        (hello_imgui.ImGuiTheme_.white_is_white, 4.0),
    ],
)
def test_windows_themes_have_the_dark_gray_text_of_windows_dialogs(
    context: None,
    monkeypatch: pytest.MonkeyPatch,
    applied: hello_imgui.ImGuiTheme_,
    rounding: float,
) -> None:
    _use_theme(monkeypatch, hello_imgui.ImGuiThemeTweaks(), applied)
    style = imgui.get_style()
    # White Is White's window background lies far above 1.0.
    style.set_color_(imgui.Col_.window_bg, imgui.ImVec4(6.6, 6.6, 6.6, 1.0))
    theme.apply_style()
    text = style.color_(imgui.Col_.text)
    assert (round(text.x * 255), round(text.y * 255), round(text.z * 255)) == (
        51,
        51,
        51,
    )
    assert style.frame_rounding == rounding
    assert style.color_(imgui.Col_.window_bg).x == 1.0

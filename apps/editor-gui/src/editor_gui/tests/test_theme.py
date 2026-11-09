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
    # imgui's close buttons on tabs never show (``dock_menus`` draws them).
    assert style.tab_close_button_min_width_selected > 1e20
    assert style.tab_close_button_min_width_unselected > 1e20


@pytest.mark.parametrize("applied", [theme.LIGHT_THEME, theme.DARK_THEME])
def test_lines_that_resize_light_up_as_the_splitters_between_docked_windows(
    context: None, monkeypatch: pytest.MonkeyPatch, applied: hello_imgui.ImGuiTheme_
) -> None:
    """The lines beside the page tree and above the help (and table column borders) use the
    hovered and dragged colors of the splitters between docked windows."""
    hello_imgui.apply_theme(applied)
    _use_theme(monkeypatch, hello_imgui.ImGuiThemeTweaks(), applied)
    theme.apply_style()
    style = imgui.get_style()

    def rgba(col: imgui.Col_) -> tuple[float, ...]:
        c = style.color_(col)
        return (c.x, c.y, c.z, c.w)

    assert rgba(imgui.Col_.separator_hovered) == rgba(imgui.Col_.resize_grip_hovered)
    assert rgba(imgui.Col_.separator_active) == rgba(imgui.Col_.resize_grip_active)
    # Hovering shows: the color differs from the line's own.
    assert rgba(imgui.Col_.separator_hovered) != rgba(imgui.Col_.separator)


@pytest.mark.parametrize(
    ("applied", "rounding", "window"),
    [
        # A light gray with a touch of blue.
        (hello_imgui.ImGuiTheme_.microsoft_style, 0.0, (245, 246, 247)),
        (hello_imgui.ImGuiTheme_.white_is_white, 4.0, (255, 255, 255)),
    ],
)
def test_windows_themes_have_the_dark_gray_text_of_windows_dialogs(
    context: None,
    monkeypatch: pytest.MonkeyPatch,
    applied: hello_imgui.ImGuiTheme_,
    rounding: float,
    window: tuple[int, int, int],
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
    background = style.color_(imgui.Col_.window_bg)
    assert (
        round(background.x * 255),
        round(background.y * 255),
        round(background.z * 255),
    ) == window


@pytest.mark.parametrize(
    ("applied", "table_header"),
    [
        (hello_imgui.ImGuiTheme_.microsoft_style, True),
        (hello_imgui.ImGuiTheme_.white_is_white, True),
        (hello_imgui.ImGuiTheme_.gray_variations, True),
        (hello_imgui.ImGuiTheme_.imgui_colors_light, False),
        (hello_imgui.ImGuiTheme_.darcula_darker, False),
    ],
)
def test_light_themes_select_in_the_gray_of_table_headers(
    context: None,
    monkeypatch: pytest.MonkeyPatch,
    applied: hello_imgui.ImGuiTheme_,
    table_header: bool,
) -> None:
    tweaked = hello_imgui.ImGuiTweakedTheme()
    tweaked.theme = applied
    hello_imgui.apply_tweaked_theme(tweaked)
    _use_theme(monkeypatch, hello_imgui.ImGuiThemeTweaks(), applied)
    style = imgui.get_style()
    before = style.color_(imgui.Col_.header)
    before = (before.x, before.y, before.z, before.w)
    theme.apply_style()
    header = style.color_(imgui.Col_.header)
    if table_header:
        assert header == style.color_(imgui.Col_.table_header_bg)
    else:
        # A blue or dark theme's selection shows clearly already.
        assert (header.x, header.y, header.z, header.w) == before


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("kind", ["warning", "error", "success", "info"])
def test_status_colors_read_on_light_and_dark_windows(kind: str) -> None:
    style = imgui.get_style()
    style.set_color_(imgui.Col_.window_bg, imgui.ImVec4(1.0, 1.0, 1.0, 1.0))
    on_light = theme.status_color(kind)
    style.set_color_(imgui.Col_.window_bg, imgui.ImVec4(0.1, 0.1, 0.1, 1.0))
    on_dark = theme.status_color(kind)
    # Dark on a white window, light on a dark one.
    assert theme._luminance(on_light) < 0.5 < theme._luminance(on_dark)  # pyright: ignore[reportPrivateUsage]


def _button_colors(disabled: bool) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """The fill and the text color a button is drawn with, from imgui's draw data."""
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(400, 200)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    # A new window is drawn from its second frame on.
    for _ in range(2):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(300, 100))
        imgui.begin("w")
        imgui.begin_disabled(disabled)
        imgui.button("Teach")
        imgui.end_disabled()
        imgui.end()
        imgui.render()
    colors = [
        v.col
        for cmd_list in imgui.get_draw_data().cmd_lists
        for v in cmd_list.vtx_buffer
    ]

    def rgb(col: int) -> tuple[int, ...]:
        return (col & 0xFF, (col >> 8) & 0xFF, (col >> 16) & 0xFF)

    return rgb(colors[0]), tuple(sorted({rgb(c) for c in colors}))


@pytest.mark.usefixtures("context")
def test_a_disabled_button_has_the_colors_of_windows_dialogs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled #F2F2F2 with a #CCCCCC border and #333333 text; disabled nearly white (#FDFDFD,
    border #F7F7F7, text #DEDEDE), not the enabled button faded."""
    _use_theme(
        monkeypatch,
        hello_imgui.ImGuiThemeTweaks(),
        hello_imgui.ImGuiTheme_.microsoft_style,
    )
    hello_imgui.apply_theme(hello_imgui.ImGuiTheme_.microsoft_style)
    theme.apply_style()
    _, enabled = _button_colors(False)
    assert (242, 242, 242) in enabled and (51, 51, 51) in enabled
    _, disabled = _button_colors(True)
    assert (253, 253, 253) in disabled
    assert (247, 247, 247) in disabled
    assert (242, 242, 242) not in disabled


@pytest.mark.usefixtures("context")
def test_a_disabled_button_is_closer_to_the_window_in_the_dark_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_theme(monkeypatch, hello_imgui.ImGuiThemeTweaks())
    hello_imgui.apply_theme(hello_imgui.ImGuiTheme_.darcula_darker)
    theme.apply_style()
    window = imgui.get_style().color_(imgui.Col_.window_bg)
    _, disabled = _button_colors(True)
    # Fill and border within a little of the window.
    assert min(abs(c[0] - round(window.x * 255)) for c in disabled) <= 6

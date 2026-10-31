"""Text in XKNX Sans is as wide and breaks into the same lines as in WPF (Segoe UI Semilight 12 and
Semibold 14, measured there), and is drawn with its kerning."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import text_layout
from editor_gui.widgets.text_layout import metrics, wrap

_REGULAR = "fonts/XKNXSans-Regular.ttf"
_BOLD = "fonts/XKNXSans-Bold.ttf"
# FreeType's NoHinting flag, as the editor loads its fonts.
_NO_HINTING = 1 << 0


@pytest.fixture
def fonts() -> Iterator[dict[str, imgui.ImFont]]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    loaded: dict[str, imgui.ImFont] = {}
    for face in (_REGULAR, _BOLD):
        config = imgui.ImFontConfig()
        config.font_loader_flags = _NO_HINTING
        path = str(text_layout._ASSETS / face)  # pyright: ignore[reportPrivateUsage]
        font = io.fonts.add_font_from_file_ttf(path, 14.0, config)
        loaded[face] = font
    imgui.new_frame()
    imgui.begin("Text")
    yield loaded
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _in(font: imgui.ImFont, size: float = 14.0) -> None:
    imgui.push_font(font, size)


def test_the_font_files_hold_widths_and_kerning() -> None:
    regular = metrics(_REGULAR)
    assert regular.units_per_em == 6144
    # Segoe UI Semilight's advance of "A" (1303 of 2048 units), three times.
    assert regular.advances[ord("A")] == 3 * 1303
    assert regular.kerning[ord("T"), ord("o")] < 0
    # A hyphen-minus is never kerned with the character before it.
    assert not any(right == ord("-") for _left, right in regular.kerning)
    assert metrics(_BOLD).advances[ord("A")] != regular.advances[ord("A")]


@pytest.mark.parametrize(
    ("text", "width"),
    [
        # FormattedText.WidthIncludingTrailingWhitespace in Segoe UI Semilight 12.
        ("AV", 14.2967),
        ("To", 11.8533),
        ("Verhalten bei Busspannungswiederkehr", 201.4967),
        ("ii", 5.3667),
        # A character Segoe UI lacks, as wide as WPF's fallback font draws it.
        ("31℃", 22.7767),
    ],
)
def test_widths_are_those_of_wpf(
    fonts: dict[str, imgui.ImFont], text: str, width: float
) -> None:
    _in(fonts[_REGULAR])
    assert text_layout.text_width(text) == pytest.approx(width, abs=0.0001)
    imgui.pop_font()


def test_bold_widths_are_those_of_wpf(fonts: dict[str, imgui.ImFont]) -> None:
    # Segoe UI Semibold 14.
    _in(fonts[_BOLD], 14.0 * 14.0 / 12.0)
    assert text_layout.text_width("31℃") == pytest.approx(27.6333, abs=0.0001)
    imgui.pop_font()


@pytest.mark.parametrize(
    ("text", "width", "lines"),
    [
        # Where a TextBlock in Segoe UI Semilight 12 breaks.
        (
            "Cycle interval for sunrise/sunset times",
            154.8,
            ["Cycle interval for sunrise/", "sunset times"],
        ),
        ("Object 820 / {{0}}", 60.94, ["Object", "820 / {{0}}"]),
        ("[K1] Start/Stop Sequenz", 64.48, ["[K1] Start/", "Stop", "Sequenz"]),
        (
            "Threshold4 value(0..65535)",
            41.11,
            ["Thresh", "old4", "value", "(0..6553", "5)"],
        ),
        (
            "<Logic {{0:17}}> Command for Presence",
            75.11,
            ["<Logic {{0:17}}", "> Command", "for Presence"],
        ),
        ("12.5 °C", 29.91, ["12.5 °", "C"]),
        # The last letter of a line is kerned with the first letter of the next.
        ("Release,Stop", 25.7, ["Rele", "ase,S", "top"]),
        # A word cut in two leaves its spaces to the next line, which may break after them.
        ("Koppler / Verstärker", 42.1, ["Koppler", " /", "Verstärk", "er"]),
        (
            "     Temperatur Grenzwert 3",
            41.48,
            ["", "Temper", "atur", "Grenzw", "ert 3"],
        ),
    ],
)
def test_lines_break_where_wpf_breaks(
    fonts: dict[str, imgui.ImFont], text: str, width: float, lines: list[str]
) -> None:
    _in(fonts[_REGULAR])
    assert wrap(text, width) == lines
    imgui.pop_font()


def test_a_line_feed_starts_a_new_line(fonts: dict[str, imgui.ImFont]) -> None:
    _in(fonts[_REGULAR])
    assert wrap("First\n    second", 1000) == ["First", "    second"]
    assert wrap("a\u2028b", 1000) == ["a", "b"]
    imgui.pop_font()


def test_wrapping_follows_the_display_scaling(
    fonts: dict[str, imgui.ImFont], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(text_layout, "px", lambda value: value * 1.5)
    _in(fonts[_REGULAR], 21.0)
    assert text_layout.text_width("To") == pytest.approx(11.8533 * 1.5, abs=0.001)
    assert wrap("Cycle interval for sunrise/sunset times", 154.8 * 1.5) == [
        "Cycle interval for sunrise/",
        "sunset times",
    ]
    imgui.pop_font()


def test_kerned_text_is_drawn_in_runs(fonts: dict[str, imgui.ImFont]) -> None:
    _in(fonts[_REGULAR])
    imgui.set_cursor_pos(imgui.ImVec2(10, 10))
    before = imgui.get_window_draw_list().vtx_buffer.size()
    text_layout.draw_text("To be")
    # The item is as wide as the kerned text.
    assert imgui.get_item_rect_size().x == pytest.approx(
        text_layout.text_width("To be")
    )
    assert imgui.get_window_draw_list().vtx_buffer.size() > before
    imgui.pop_font()


def test_other_fonts_are_measured_by_imgui() -> None:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(400, 300)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    try:
        assert text_layout.text_width("Wide words") == pytest.approx(
            imgui.calc_text_size("Wide words").x, abs=1.0
        )
        width = imgui.calc_text_size("one two").x + 1
        assert wrap("one two three", width) == ["one two", "three"]
    finally:
        imgui.end_frame()
        imgui.destroy_context(ctx)

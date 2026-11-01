"""A memory dump: addresses, bytes and characters in the font of fixed width, in its columns; the
byte under the mouse (over its two digits or its character) in the hover colour of a tree row, in
both places."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import fonts
from editor_gui.widgets.hex_view import HexView, _character  # pyright: ignore[reportPrivateUsage]

_DATA = bytes(range(0x40, 0x60)) + b"\x00\x7f\x80"


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(900, 700)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _Recorder:
    """Draw lists that note the texts and filled rectangles drawn into them."""

    def __init__(self) -> None:
        self.texts: list[tuple[float, float, str]] = []
        self.rects: list[tuple[float, float, float, float, int]] = []

    def wrap(self, real: imgui.ImDrawList) -> Any:
        recorder = self

        class _List:
            def add_text(self, pos: imgui.ImVec2, col: int, text: str) -> None:
                recorder.texts.append((pos.x, pos.y, text))
                real.add_text(pos, col, text)

            def add_rect_filled(
                self, low: imgui.ImVec2, high: imgui.ImVec2, col: int
            ) -> None:
                recorder.rects.append((low.x, low.y, high.x, high.y, col))
                real.add_rect_filled(low, high, col)

            def __getattr__(self, name: str) -> Any:
                return getattr(real, name)

        return _List()


def _frame(
    view: HexView, monkeypatch: pytest.MonkeyPatch, mouse: tuple[float, float]
) -> _Recorder:
    imgui.get_io().add_mouse_pos_event(*mouse)
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(900, 700))
    imgui.begin("Memory")
    recorder = _Recorder()
    real = imgui.get_window_draw_list
    with monkeypatch.context() as patch:
        patch.setattr(imgui, "get_window_draw_list", lambda: recorder.wrap(real()))
        view.draw(_DATA, base_addr=0x4000)
    imgui.end()
    imgui.end_frame()
    imgui.render()
    return recorder


def _centre(texts: list[tuple[float, float, str]], text: str) -> tuple[float, float]:
    x, y, _ = next(t for t in texts if t[2] == text)
    size = imgui.calc_text_size(text)
    return x + size.x / 2, y + size.y / 2


@pytest.mark.usefixtures("context")
def test_the_byte_under_the_mouse_by_its_digits_or_its_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = HexView()
    texts = _frame(view, monkeypatch, (-1, -1)).texts
    # Over the digits of 0x45 ...
    _frame(view, monkeypatch, _centre(texts, "45"))
    recorder = _frame(view, monkeypatch, _centre(texts, "45"))
    assert view._hover_addr == 5  # pyright: ignore[reportPrivateUsage]
    # ... both its digits and its character have the hover colour of a tree row.
    hover = imgui.get_color_u32(imgui.Col_.header_hovered)
    highlighted = [r for r in recorder.rects if r[4] == hover]
    assert len(highlighted) == 2
    digits, character = sorted(highlighted)
    char_w = imgui.calc_text_size("F").x
    x, y = next(t[:2] for t in texts if t[2] == "45")
    # The digits' highlight fills the room to the neighbouring bytes, the character's its column;
    # both the row's band, 2 px above and below its text.
    room = (22 - 2 * char_w) / 2
    line = imgui.get_text_line_height()
    assert digits[:4] == pytest.approx(
        (x - room, y - 2, x + 2 * char_w + room, y + line + 2)
    )
    x, y = next(t[:2] for t in texts if t[2] == "E")
    assert character[:4] == pytest.approx((x, y - 2, x + char_w, y + line + 2))
    # Over the character of 0x52 (second row).
    _frame(view, monkeypatch, _centre(texts, "R"))
    _frame(view, monkeypatch, _centre(texts, "R"))
    assert view._hover_addr == 0x12  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("context")
def test_bytes_and_characters_in_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    texts = _frame(HexView(), monkeypatch, (-1, -1)).texts
    char_w = imgui.calc_text_size("F").x
    first_row = [t for t in texts if t[1] == texts[0][1]]
    digits = [x for x, _y, text in first_row if len(text) == 2]
    characters = [x for x, _y, text in first_row if len(text) == 1]
    assert len(digits) == len(characters) == 16
    # Bytes 22 px apart, 6 px more between the halves of a row.
    steps = [b - a for a, b in itertools.pairwise(digits)]
    assert steps == pytest.approx([22.0] * 7 + [28.0] + [22.0] * 7)
    # The characters 9 px further than a 17th byte would be, one column each.
    assert characters[0] - digits[-1] == pytest.approx(22.0 + 9.0)
    steps = [b - a for a, b in itertools.pairwise(characters)]
    assert steps == pytest.approx([char_w] * 15)
    # Rows 4 px further apart than lines of text (18 px with the app's fonts).
    rows = sorted({y for _x, y, text in texts if len(text) == 2})
    line = imgui.get_text_line_height()
    assert [b - a for a, b in itertools.pairwise(rows)] == pytest.approx([line + 4] * 2)


def test_characters_beside_the_bytes() -> None:
    assert [_character(b) for b in (0x41, 0x20, 0x7E)] == ["A", " ", "~"]
    # Control characters, DEL and anything above ASCII as dots.
    assert [_character(b) for b in (0x00, 0x1F, 0x7F, 0x80, 0xFF)] == ["."] * 5


@pytest.mark.usefixtures("context")
def test_monospace_draws_in_the_font_of_fixed_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mono = imgui.get_io().fonts.add_font_default()
    monkeypatch.setattr(fonts, "_mono", mono)
    imgui.new_frame()
    imgui.begin("Fonts")
    with fonts.monospace():
        assert imgui.get_font() is mono
        assert imgui.get_font_size() == imgui.get_style().font_size_base
    assert fonts.monospace_font() is mono
    imgui.end()
    imgui.end_frame()

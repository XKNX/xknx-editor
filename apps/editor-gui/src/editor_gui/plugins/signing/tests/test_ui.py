"""The key fields of the signing dialog wrap the long hex keys and grow with them."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.signing.ui import _key_field, key_field_lines

_KEY = "cd28eb99598cf942d776b147892659a3" * 16  # 512 hex digits: a 2048-bit modulus


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _field_height(text: str, width: float) -> tuple[float, str]:
    """The height of a key field showing ``text`` in a window ``width`` wide, and its text."""
    height = 0.0
    for _ in range(2):
        imgui.new_frame()
        imgui.set_next_window_size(imgui.ImVec2(width, 500))
        imgui.begin("Signing Key")
        text = _key_field("##key", text)
        height = imgui.get_item_rect_size().y
        imgui.end()
        imgui.end_frame()
    return height, text


def _lines(height: float) -> int:
    padding = imgui.get_style().frame_padding.y
    return round((height - 2 * padding) / imgui.get_text_line_height())


@pytest.mark.usefixtures("context")
def test_a_key_without_spaces_wraps() -> None:
    imgui.new_frame()
    one_line = imgui.calc_text_size(_KEY).x
    assert key_field_lines(_KEY, one_line / 4) > 2
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_the_field_grows_with_the_key_up_to_eight_lines() -> None:
    wide, text = _field_height(_KEY, 700)
    narrow, _ = _field_height(_KEY, 300)
    assert text == _KEY
    assert 2 < _lines(wide) < _lines(narrow) == 8


@pytest.mark.usefixtures("context")
def test_an_empty_or_short_key_takes_two_lines() -> None:
    for text in ("", "10001"):
        height, _ = _field_height(text, 700)
        assert _lines(height) == 2

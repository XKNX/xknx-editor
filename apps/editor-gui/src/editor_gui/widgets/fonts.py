"""The default font plus bold (headlines) and italic (placeholders) variants of it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from imgui_bundle import hello_imgui, imgui

_DEFAULT_FONT = "fonts/DroidSans.ttf"
# Letters 12 px high (the em of the font is 12 px; an ImGui font size counts ascent plus descent),
# the size of the 9 pt text of Windows dialogs, so labels and values written for those fit the same
# fields.
_FONT_SIZE = 14.0
# ImGuiFreeTypeLoaderFlags_Bold: emboldened glyphs of the same face.
_FREETYPE_BOLD = 1 << 5
# ImGuiFreeTypeLoaderFlags_Oblique: slanted glyphs of the same face.
_FREETYPE_OBLIQUE = 1 << 6

_bold: imgui.ImFont | None = None
_italic: imgui.ImFont | None = None


def load_fonts() -> None:
    """Load the default fonts and the bold and italic variants; the ``load_additional_fonts``
    callback."""
    global _bold, _italic
    hello_imgui.load_font_ttf_with_font_awesome_icons(_DEFAULT_FONT, _FONT_SIZE)
    _bold = _load_variant(_FREETYPE_BOLD)
    _italic = _load_variant(_FREETYPE_OBLIQUE)


def _load_variant(flags: int) -> imgui.ImFont | None:
    params = hello_imgui.FontLoadingParams()
    params.font_config.font_loader_flags = flags
    try:
        return hello_imgui.load_font(_DEFAULT_FONT, _FONT_SIZE, params)
    except RuntimeError:
        return None


@contextmanager
def bold(scale: float = 1.0) -> Iterator[None]:
    """Draw in the bold font at the normal text size, or ``scale`` times it."""
    imgui.push_font(_bold, imgui.get_style().font_size_base * scale)
    try:
        yield
    finally:
        imgui.pop_font()


@contextmanager
def heading() -> Iterator[None]:
    """Draw in the bold font at a larger headline size (for prominent intro text)."""
    imgui.push_font(_bold, imgui.get_style().font_size_base * 1.35)
    try:
        yield
    finally:
        imgui.pop_font()


@contextmanager
def italic() -> Iterator[None]:
    """Draw in the italic font at the normal text size."""
    imgui.push_font(_italic, imgui.get_style().font_size_base)
    try:
        yield
    finally:
        imgui.pop_font()

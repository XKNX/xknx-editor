"""The default font plus bold (headlines), italic (placeholders) and bold italic (formatted
comments) variants of it."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from imgui_bundle import hello_imgui, imgui

_DEFAULT_FONT = "fonts/DroidSans.ttf"
# The bold face of the same font (Droid Sans Bold, the same build as the regular face).
_BOLD_FONT = "fonts/DroidSans-Bold.ttf"
# Letters 12 px high (the em of the font is 12 px; an ImGui font size counts ascent plus descent),
# the size of the 9 pt text of Windows dialogs, so labels and values written for those fit the same
# fields.
_FONT_SIZE = 14.0
# ImGuiFreeTypeLoaderFlags_Oblique: slanted glyphs of the same face.
_FREETYPE_OBLIQUE = 1 << 6

_bold: imgui.ImFont | None = None
_italic: imgui.ImFont | None = None
_bold_italic: imgui.ImFont | None = None


def load_fonts() -> None:
    """Load the default fonts and the bold and italic variants; the ``load_additional_fonts``
    callback."""
    global _bold, _italic, _bold_italic
    hello_imgui.load_font_ttf_with_font_awesome_icons(_DEFAULT_FONT, _FONT_SIZE)
    _bold = _load_variant(_BOLD_FONT)
    # Droid Sans has no italic face: the regular one, slanted.
    _italic = _load_variant(_DEFAULT_FONT, _FREETYPE_OBLIQUE)
    _bold_italic = _load_variant(_BOLD_FONT, _FREETYPE_OBLIQUE)


def _load_variant(path: str, flags: int = 0) -> imgui.ImFont | None:
    params = hello_imgui.FontLoadingParams()
    params.font_config.font_loader_flags = flags
    try:
        return hello_imgui.load_font(path, _FONT_SIZE, params)
    except RuntimeError:
        return None


@contextmanager
def bold(scale: float = 1.0) -> Generator[None]:
    """Draw in the bold font at the normal text size, or ``scale`` times it."""
    imgui.push_font(_bold, imgui.get_style().font_size_base * scale)
    try:
        yield
    finally:
        imgui.pop_font()


@contextmanager
def heading() -> Generator[None]:
    """Draw in the bold font at a larger headline size (for prominent intro text)."""
    imgui.push_font(_bold, imgui.get_style().font_size_base * 1.35)
    try:
        yield
    finally:
        imgui.pop_font()


def fitted_heading(text: str, scale: float = 1.15) -> None:
    """Bold intro lines, ``scale`` times the text size or less so each line fits the width.

    Lines too wide even at the normal size wrap.
    """
    with bold():
        widest = max(imgui.calc_text_size(line).x for line in text.split("\n"))
    if widest > 0.0:
        scale = max(1.0, min(scale, imgui.get_content_region_avail().x / widest))
    with bold(scale):
        imgui.text_wrapped(text)


@contextmanager
def italic() -> Generator[None]:
    """Draw in the italic font at the normal text size."""
    imgui.push_font(_italic, imgui.get_style().font_size_base)
    try:
        yield
    finally:
        imgui.pop_font()


def styled_font(bold: bool, italic: bool) -> imgui.ImFont | None:
    """The font for text in bold and/or italic; None for the regular font (or a variant that could
    not be loaded)."""
    if bold and italic:
        return _bold_italic or _bold
    if bold:
        return _bold
    return _italic if italic else None

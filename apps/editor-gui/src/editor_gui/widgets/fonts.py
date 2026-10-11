"""The default font plus bold (headlines) and italic (placeholders) variants of it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from imgui_bundle import hello_imgui, imgui

_DEFAULT_FONT = "fonts/DroidSans.ttf"
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
    hello_imgui.imgui_default_settings.load_default_font_with_font_awesome_icons()
    _bold = _load_variant(_FREETYPE_BOLD)
    _italic = _load_variant(_FREETYPE_OBLIQUE)


def _load_variant(flags: int) -> imgui.ImFont | None:
    params = hello_imgui.FontLoadingParams()
    params.font_config.font_loader_flags = flags
    try:
        return hello_imgui.load_font(_DEFAULT_FONT, 14.0, params)
    except RuntimeError:
        return None


@contextmanager
def bold() -> Iterator[None]:
    """Draw in the bold font at the normal text size."""
    imgui.push_font(_bold, imgui.get_style().font_size_base)
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

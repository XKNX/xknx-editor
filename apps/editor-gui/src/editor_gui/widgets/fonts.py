"""The default font plus a bold variant of it for headlines."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from imgui_bundle import hello_imgui, imgui

_DEFAULT_FONT = "fonts/DroidSans.ttf"
# ImGuiFreeTypeLoaderFlags_Bold: emboldened glyphs of the same face.
_FREETYPE_BOLD = 1 << 5

_bold: imgui.ImFont | None = None


def load_fonts() -> None:
    """Load the default fonts and the bold variant; the ``load_additional_fonts`` callback."""
    global _bold
    hello_imgui.imgui_default_settings.load_default_font_with_font_awesome_icons()
    params = hello_imgui.FontLoadingParams()
    params.font_config.font_loader_flags = _FREETYPE_BOLD
    try:
        _bold = hello_imgui.load_font(_DEFAULT_FONT, 14.0, params)
    except RuntimeError:
        _bold = None


@contextmanager
def bold() -> Iterator[None]:
    """Draw in the bold font at the normal text size."""
    imgui.push_font(_bold, imgui.get_style().font_size_base)
    try:
        yield
    finally:
        imgui.pop_font()

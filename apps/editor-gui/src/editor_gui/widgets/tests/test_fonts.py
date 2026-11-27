"""The five proportional styles share CJK font data across atlas rebuilds."""

from __future__ import annotations

import ctypes
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import hello_imgui, imgui

from editor_gui.widgets import fonts

_ASSETS = Path(fonts.__file__).resolve().parents[1] / "assets"
_SAMPLE = "设备漢字かなカナ"
_DEFAULT_ASSETS_FOLDER = "assets"

_capsule_address = ctypes.pythonapi.PyCapsule_GetPointer
_capsule_address.argtypes = (ctypes.py_object, ctypes.c_char_p)
_capsule_address.restype = ctypes.c_void_p


@pytest.fixture
def atlas(monkeypatch: pytest.MonkeyPatch) -> Iterator[imgui.ImFontAtlas]:
    # The GUI keeps the active fonts in module globals. Restore those references
    # when this test's context is destroyed.
    for name in ("_default", "_semilight", "_bold", "_italic", "_bold_italic", "_mono"):
        monkeypatch.setattr(fonts, name, None)
    monkeypatch.setattr(fonts, "_cjk_order", ())
    hello_imgui.set_assets_folder(str(_ASSETS))
    context = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename(None)
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    try:
        yield io.fonts
    finally:
        imgui.destroy_context(context)
        # hello_imgui has no getter for the assets folder: back to its default.
        hello_imgui.set_assets_folder(_DEFAULT_ASSETS_FOLDER)


def _cjk_sources(atlas: imgui.ImFontAtlas) -> list[list[tuple[int, int, int]]]:
    # The two large merged sources of each proportional font are the CJK faces.
    cjk: list[list[tuple[int, int, int]]] = []
    for font in atlas.fonts:
        sources: list[tuple[int, int, int]] = []
        for source in font.sources:
            if source.font_data_size < 4 * 1024**2:
                continue
            assert source.merge_mode
            assert not source.font_data_owned_by_atlas
            address = int(_capsule_address(source.font_data, b"nb_handle"))
            sources.append((address, source.font_data_size, source.font_loader_flags))
        cjk.append(sources)
    return cjk


def _assert_cjk_glyphs(atlas: imgui.ImFontAtlas, size: float, density: float) -> None:
    imgui.new_frame()
    try:
        for index in range(5):
            font = atlas.fonts[index].get_font_baked(size, density)
            for character in _SAMPLE:
                glyph = font.find_glyph_no_fallback(ord(character))
                assert glyph is not None and glyph.is_visible()
                assert glyph.get_codepoint() == ord(character)
    finally:
        imgui.end_frame()


def test_shared_cjk_sources_survive_language_and_dpi_rebuilds(
    atlas: imgui.ImFontAtlas, monkeypatch: pytest.MonkeyPatch
) -> None:
    locale = "en"
    monkeypatch.setattr(fonts, "get_locale", lambda: locale)

    fonts.load_fonts()
    sources = _cjk_sources(atlas)
    assert [len(style) for style in sources] == [2, 2, 2, 2, 2, 0]
    assert sorted(
        Counter(address for style in sources for address, _, _ in style).values()
    ) == [2, 2, 3, 3]
    regular = [
        (_ASSETS / f"fonts/NotoSans{region}-Regular.otf").stat().st_size
        for region in ("SC", "JP")
    ]
    bold = [
        (_ASSETS / f"fonts/NotoSans{region}-Bold.otf").stat().st_size
        for region in ("SC", "JP")
    ]
    assert [[size for _, size, _ in style] for style in sources] == [
        regular,
        regular,
        bold,
        regular,
        bold,
        [],
    ]
    hinted = fonts._FREETYPE_HINTING  # pyright: ignore[reportPrivateUsage]
    oblique = hinted | fonts._FREETYPE_OBLIQUE  # pyright: ignore[reportPrivateUsage]
    assert [[flags for _, _, flags in style] for style in sources] == [
        [hinted, hinted],
        [hinted, hinted],
        [hinted, hinted],
        [oblique, oblique],
        [oblique, oblique],
        [],
    ]
    _assert_cjk_glyphs(atlas, 14.0, 1.0)

    locale = "ja"
    fonts.follow_language()
    japanese_sources = [style[::-1] for style in sources]
    assert _cjk_sources(atlas) == japanese_sources
    _assert_cjk_glyphs(atlas, 14.0, 1.0)

    atlas.clear_fonts()
    imgui.get_style().font_scale_dpi = 1.5
    fonts.load_fonts()
    assert _cjk_sources(atlas) == japanese_sources
    _assert_cjk_glyphs(atlas, 21.0, 2.0)


def test_cjk_falls_back_to_hello_imgui_when_the_capsule_is_refused(
    atlas: imgui.ImFontAtlas, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An imgui-bundle that rejects the hand-made capsule still gets the CJK glyphs, through
    hello_imgui's own loading (one copy per style), instead of failing to start."""
    monkeypatch.setattr(fonts, "get_locale", lambda: "en")

    def refuse(*_args: object) -> object:
        raise TypeError("capsule rejected")

    monkeypatch.setattr(fonts, "_font_data_capsule", refuse)

    fonts.load_fonts()
    cjk_sizes = {
        (_ASSETS / f"fonts/NotoSans{region}-{weight}.otf").stat().st_size
        for region in ("SC", "JP")
        for weight in ("Regular", "Bold")
    }
    owned = [
        [
            source.font_data_owned_by_atlas
            for source in font.sources
            if source.merge_mode and source.font_data_size in cjk_sizes
        ]
        for font in list(atlas.fonts)[:5]
    ]
    assert owned == [[True, True]] * 5
    _assert_cjk_glyphs(atlas, 14.0, 1.0)

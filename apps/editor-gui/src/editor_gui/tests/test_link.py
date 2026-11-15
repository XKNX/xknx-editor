"""A link longer than its line is shown shortened with "…"; it opens, shows in its tooltip and is
copied as the whole URL."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui import main

URL = "https://github.com/example/repository/blob/0123456789abcdef/some/long/path/file.py#L439"


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _draw(
    width: float, clicked: bool, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, list[str]]:
    shown: list[str] = []
    opened: list[str] = []

    def link(label: str) -> bool:
        shown.append(label.split("###", 1)[0])
        return clicked

    monkeypatch.setattr(imgui, "text_link", link)
    monkeypatch.setattr(main, "open_url", opened.append)
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(width, 200))
    imgui.begin("Dialog")
    main._link(URL)  # pyright: ignore[reportPrivateUsage]
    imgui.end()
    imgui.end_frame()
    return shown[0], opened


@pytest.mark.usefixtures("context")
def test_a_long_link_is_shortened_and_opens_the_whole_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown, opened = _draw(300.0, True, monkeypatch)
    assert shown.endswith("…") and URL.startswith(shown[:-1])
    assert imgui.calc_text_size(shown).x <= 300.0
    assert opened == [URL]


@pytest.mark.usefixtures("context")
def test_a_link_that_fits_is_shown_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    shown, opened = _draw(780.0, False, monkeypatch)
    assert shown == URL
    assert opened == []


def test_the_tooltip_and_the_copy_keep_the_whole_url(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tooltips: list[str] = []
    monkeypatch.setattr(imgui, "is_item_hovered", lambda *_a: True)
    monkeypatch.setattr(imgui, "set_tooltip", lambda text, *_a: tooltips.append(text))
    copied: list[str] = []
    monkeypatch.setattr(imgui, "begin_popup_context_item", lambda *_a: True)
    monkeypatch.setattr(imgui, "menu_item", lambda *_a: (True, False))
    monkeypatch.setattr(imgui, "set_clipboard_text", copied.append)
    monkeypatch.setattr(imgui, "end_popup", lambda: None)
    _draw(300.0, False, monkeypatch)
    assert URL in tooltips[0]
    assert copied == [URL]

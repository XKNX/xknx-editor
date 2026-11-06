"""The Settings window: its pages and settings, a search over their names in the interface language
and in English, and every kind of control drawn wide and narrow."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.main import menus_shown
from editor_gui.settings_window import (
    Setting,
    SettingsPage,
    SettingsWindow,
    matches,
)
from editor_gui.strings import S, get_locale, in_english, set_locale


@pytest.fixture
def german() -> Iterator[None]:
    before = get_locale()
    set_locale("de")
    yield
    set_locale(before)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _pages(values: dict[str, Any], drawn: list[str]) -> list[SettingsPage]:
    def setting(key: str, kind: Any, **extra: Any) -> Setting:
        return Setting(
            key,
            extra.pop("label", lambda: key.title()),
            kind,
            get=lambda: values[key],
            set=lambda value: values.__setitem__(key, value),
            **extra,
        )

    return [
        SettingsPage(
            "general",
            lambda: S.SETTINGS_PAGE_GENERAL,
            [
                setting(
                    "language",
                    "choice",
                    label=lambda: S.SETTING_LANGUAGE,
                    choices=lambda: [("de", "Deutsch"), ("en", "English")],
                ),
                setting("flag", "bool", hint=lambda: "A hint"),
                setting("number", "int", bounds=(1, 9)),
            ],
        ),
        SettingsPage(
            "folders",
            lambda: S.SETTINGS_PAGE_FOLDERS,
            [
                setting("text", "text", keywords=lambda: "Alpha Beta"),
                setting("secret", "password", secret=True),
                setting("path", "info", wide=True),
                Setting(
                    "buttons",
                    lambda: "Buttons",
                    "custom",
                    render=lambda: drawn.append("custom"),
                ),
                setting("desktop", "bool", shown=lambda: False),
            ],
        ),
    ]


def _window(values: dict[str, Any] | None = None) -> tuple[SettingsWindow, list[str]]:
    drawn: list[str] = []
    data = (
        values
        if values is not None
        else {
            "language": "de",
            "flag": True,
            "number": 3,
            "text": "t",
            "secret": "s",
            "path": "/x",
            "desktop": False,
        }
    )
    pages = _pages(data, drawn)
    return SettingsWindow(lambda: pages, nested=lambda: drawn.append("nested")), drawn


def test_words_are_matched_in_any_text() -> None:
    assert matches("", ["anything"])
    assert matches("fold LOG", ["Log Folder"])
    assert matches("log  folder", ["Folders", "Log"])
    assert not matches("cache", ["Log Folder"])


def test_a_setting_not_shown_here_is_left_out() -> None:
    window, _ = _window()
    keys = [s.key for page in window.pages() for s in page.settings]
    assert "desktop" not in keys
    assert window.setting("desktop") is None
    found = window.setting("text")
    assert found is not None and found[0].key == "folders"


@pytest.mark.usefixtures("german")
def test_the_search_finds_names_in_the_language_and_in_english() -> None:
    window, _ = _window()
    # "Sprache" is the German name, "language" the English one.
    for query in ("sprache", "language"):
        found = window.search(query)
        assert [(p.key, [s.key for s in hits]) for p, hits in found] == [
            ("general", ["language"])
        ]
    # A page's title finds all of its settings; keywords and hints are searched too.
    assert [s.key for s in window.search("ordner")[0][1]] == [
        "text",
        "secret",
        "path",
        "buttons",
    ]
    assert [s.key for s in window.search("beta")[0][1]] == ["text"]
    assert [s.key for s in window.search("hint")[0][1]] == ["flag"]
    assert window.search("nothing like it") == []
    assert len(window.search("")) == 2


@pytest.mark.usefixtures("german")
def test_english_is_read_in_its_own_thread_only() -> None:
    seen: list[str] = []
    with in_english():
        assert S.SETTINGS_PAGE_GENERAL == "General"
        thread = threading.Thread(target=lambda: seen.append(S.SETTINGS_PAGE_GENERAL))
        thread.start()
        thread.join()
    assert seen == ["Allgemein"]
    assert S.SETTINGS_PAGE_GENERAL == "Allgemein"


def _frames(window: SettingsWindow, count: int = 3, width: float = 1000) -> None:
    imgui.get_io().display_size = imgui.ImVec2(width, 800)
    for _ in range(count):
        imgui.new_frame()
        window.render()
        imgui.end_frame()


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("width", [1000.0, 390.0])
def test_every_page_and_control_is_drawn(width: float) -> None:
    window, drawn = _window()
    window.open("folders")
    _frames(window, width=width)
    assert drawn.count("custom") >= 1 and "nested" in drawn
    # The page shown last stays when the window opens again; a search shows every page.
    drawn.clear()
    window.open()
    window._query = "e"  # pyright: ignore[reportPrivateUsage]
    _frames(window, width=width)
    assert "custom" in drawn


@pytest.mark.usefixtures("context")
def test_an_unknown_page_shows_the_first() -> None:
    window, drawn = _window()
    window.open("gone")
    _frames(window)
    assert window._page == "general"  # pyright: ignore[reportPrivateUsage]
    assert "custom" not in drawn


def test_menus_go_into_one_menu_from_the_right_when_they_do_not_fit() -> None:
    widths = [40.0, 40.0, 50.0, 70.0, 50.0, 40.0]
    assert menus_shown(widths, 400.0, 30.0) == 6
    assert menus_shown(widths, 290.0, 30.0) == 6
    # Not all fit: as many as fit beside the one menu holding the others.
    assert menus_shown(widths, 289.0, 30.0) == 5
    assert menus_shown(widths, 279.0, 30.0) == 4
    assert menus_shown(widths, 160.0, 30.0) == 3
    assert menus_shown(widths, 50.0, 30.0) == 0


@pytest.mark.usefixtures("context")
def test_the_window_knows_it_is_shown_from_anywhere() -> None:
    window, _ = _window()
    assert not window.is_open()
    window.open()
    _frames(window, count=2)
    assert window.is_open()
    # Not drawn for two frames (closed): no longer shown.
    for _ in range(2):
        imgui.new_frame()
        imgui.end_frame()
    assert not window.is_open()

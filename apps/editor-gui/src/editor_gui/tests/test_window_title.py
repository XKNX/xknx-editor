"""The window title names the open project and, when its file is named otherwise, the file."""

from __future__ import annotations

from pathlib import Path

from editor_gui.main import window_title
from editor_gui.strings import S


def test_a_file_named_like_the_project_shows_the_name_only() -> None:
    title = window_title("House", Path("projects/House.xknx"))
    assert title == S.WINDOW_TITLE.format(project="House", app=S.APP_TITLE)


def test_a_file_named_otherwise_is_shown_after_the_name() -> None:
    title = window_title(
        "Familie Friedel (2)", Path("projects/Familie Friedel (aktuell).xknx")
    )
    assert title == S.WINDOW_TITLE_FILE.format(
        project="Familie Friedel (2)",
        file="Familie Friedel (aktuell).xknx",
        app=S.APP_TITLE,
    )


def test_a_project_without_a_name_shows_its_file_name() -> None:
    title = window_title("", Path("projects/House.xknx"))
    assert title == S.WINDOW_TITLE.format(project="House", app=S.APP_TITLE)


def test_without_a_name_and_a_file_only_the_app_is_named() -> None:
    assert window_title("", None) == S.APP_TITLE
    assert window_title("House", None) == S.WINDOW_TITLE.format(
        project="House", app=S.APP_TITLE
    )

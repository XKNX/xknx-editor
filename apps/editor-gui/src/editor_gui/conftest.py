"""The editor's tests never write to (or prune) the user's log folder: their log goes nowhere.
Their imgui contexts read and write no imgui.ini. Every menu a test shows is checked: a separator
never follows another one."""

import os
from collections.abc import Iterator
from typing import Any, cast

import pytest
from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.log_file import set_session_log

set_session_log(open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - kept for the run


def _last_item() -> tuple[float, float, float, float]:
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    return (low.x, low.y, high.x, high.y)


@pytest.fixture(autouse=True)
def _no_ini_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A context of a test starts without saved window settings and saves none. Otherwise every
    test (and every parallel worker) shares the imgui.ini of the working directory: a window
    another test left collapsed or small comes up so, and skips the items a test looks for."""
    real_create_context = imgui.create_context

    def create_context(*args: Any, **kwargs: Any) -> imgui.internal.Context:
        # A new context only becomes the current one when there was none.
        previous = cast("imgui.internal.Context | None", imgui.get_current_context())
        ctx = real_create_context(*args, **kwargs)
        imgui.set_current_context(ctx)
        imgui.get_io().set_ini_filename(None)
        if previous is not None:
            imgui.set_current_context(previous)
        return ctx

    monkeypatch.setattr(imgui, "create_context", create_context)


@pytest.fixture(autouse=True)
def _no_separator_twice_in_menus(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """In a menu (a popup), two separators with nothing between them are a test failure."""
    real_separator = imgui.separator
    # The last separator of each popup in this frame: (window id, frame) -> its rect.
    drawn: dict[tuple[int, int], tuple[float, float, float, float]] = {}

    def separator() -> None:
        window = internal.get_current_window()
        in_menu = bool(window.flags & imgui.WindowFlags_.popup)
        key = (window.id_, imgui.get_frame_count())
        if in_menu and drawn.get(key) == _last_item():
            raise AssertionError(f"two separators in a row in the menu {window.name!r}")
        real_separator()
        if in_menu:
            drawn[key] = _last_item()

    monkeypatch.setattr(imgui, "separator", separator)
    yield

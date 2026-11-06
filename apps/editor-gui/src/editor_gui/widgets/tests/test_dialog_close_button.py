"""A dialog's title bar has a close button while its dismiss button can be used; a click on it
dismisses the dialog as Escape does, so the dialog's own cancel handling runs."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import dialog
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog

_TITLE = "Delete###close_test"


@pytest.fixture(autouse=True)
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)
    dialog._closable.clear()  # pyright: ignore[reportPrivateUsage]
    dialog._closed.clear()  # pyright: ignore[reportPrivateUsage]


def _frame(enabled: list[bool] | None = None, *, open_it: bool = False) -> int | None:
    imgui.new_frame()
    if open_it:
        imgui.open_popup(_TITLE)
    clicked = None
    if begin_dialog(_TITLE, width=300.0):
        imgui.text("Delete it?")
        clicked = dialog_buttons("Delete", "Cancel", enabled=enabled)
        end_dialog()
    imgui.end_frame()
    return clicked


def test_the_close_button_dismisses_like_escape() -> None:
    _frame(open_it=True)
    assert _frame() is None
    assert _TITLE in dialog._closable  # pyright: ignore[reportPrivateUsage]
    # A click on it (as the button reports it) makes the dismiss button the one clicked.
    dialog._closed.add(_TITLE)  # pyright: ignore[reportPrivateUsage]
    assert _frame() == 1
    assert _TITLE not in dialog._closed  # pyright: ignore[reportPrivateUsage]


def test_no_close_button_while_the_dialog_cannot_be_dismissed() -> None:
    _frame([True, False], open_it=True)
    _frame([True, False])
    assert _TITLE not in dialog._closable  # pyright: ignore[reportPrivateUsage]
    dialog._closed.add(_TITLE)  # pyright: ignore[reportPrivateUsage]
    assert _frame([True, False]) is None


def test_a_closed_dialog_forgets_its_button() -> None:
    _frame(open_it=True)
    _frame()
    imgui.new_frame()
    imgui.end_frame()
    _frame()
    assert _TITLE not in dialog._closable  # pyright: ignore[reportPrivateUsage]

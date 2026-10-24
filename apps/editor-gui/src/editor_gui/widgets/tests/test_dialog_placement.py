"""A dialog stays centred while its size settles or its content changes, until the user moves it,
and is centred and fitted again when the screen changes its size."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.dialog import (
    begin_dialog,
    begin_tool_window,
    dialog_buttons,
    end_dialog,
)

_TEXT = (
    "Delete '1.0.33  Heating Actuator 8-fold, 4SU MDRC, 24/230VAC'? Its group objects and "
    "group-address links are removed and its individual address is freed. This can be undone."
)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frames(
    count: int,
    content: Callable[[int], None],
    before: Callable[[int], None] | None = None,
) -> tuple[imgui.ImVec2, imgui.ImVec2]:
    """Render the dialog ``count`` frames; return its last position and size."""
    pos = size = imgui.ImVec2(0, 0)
    for frame in range(count):
        imgui.new_frame()
        if before is not None:
            before(frame)
        if frame == 0:
            imgui.open_popup("Delete")
        if begin_dialog("Delete"):
            content(frame)
            dialog_buttons("Delete", "Cancel")
            pos, size = imgui.get_window_pos(), imgui.get_window_size()
            end_dialog()
        imgui.end_frame()
    return pos, size


def _centred(pos: imgui.ImVec2, size: imgui.ImVec2) -> bool:
    return abs(pos.x + size.x / 2 - 400) <= 1 and abs(pos.y + size.y / 2 - 300) <= 1


@pytest.mark.usefixtures("context")
def test_wrapped_text_settles_centred() -> None:
    # Wrapped text in a dialog fitting its content is tall in its first frames, then shrinks.
    pos, size = _frames(6, lambda _: imgui.text_wrapped(_TEXT))
    assert _centred(pos, size)


@pytest.mark.usefixtures("context")
def test_recentred_when_content_grows() -> None:
    def content(frame: int) -> None:
        imgui.text("Deleting")
        if frame >= 5:
            imgui.dummy(imgui.ImVec2(300, 200))

    pos, size = _frames(9, content)
    assert size.y > 200
    assert _centred(pos, size)


@pytest.mark.usefixtures("context")
def test_moved_dialog_stays_where_moved() -> None:
    def move(frame: int) -> None:
        if frame == 4:
            imgui.set_window_pos("Delete", imgui.ImVec2(20, 30))

    def content(frame: int) -> None:
        imgui.text("Deleting")
        if frame >= 6:
            imgui.dummy(imgui.ImVec2(300, 200))

    pos, size = _frames(10, content, move)
    assert size.y > 200
    assert (pos.x, pos.y) == (20, 30)


def _resize_screen(at: int, width: float, height: float) -> Callable[[int], None]:
    def resize(frame: int) -> None:
        if frame == at:
            imgui.get_io().display_size = imgui.ImVec2(width, height)

    return resize


def _centred_on(
    pos: imgui.ImVec2, size: imgui.ImVec2, width: float, height: float
) -> bool:
    return (
        abs(pos.x + size.x / 2 - width / 2) <= 1
        and abs(pos.y + size.y / 2 - height / 2) <= 1
    )


@pytest.mark.usefixtures("context")
def test_follows_a_smaller_screen() -> None:
    # A browser window made as narrow as a phone: the dialog fits it, centred, and its text wraps
    # at the new width instead of scrolling sideways.
    content_width = [0.0]

    def content(_: int) -> None:
        imgui.text_wrapped(_TEXT)
        content_width[0] = imgui.internal.get_current_window().content_size.x

    pos = size = imgui.ImVec2(0, 0)
    resize = _resize_screen(4, 390, 844)
    for frame in range(10):
        imgui.new_frame()
        resize(frame)
        if frame == 0:
            imgui.open_popup("Delete")
        if begin_dialog("Delete", width=460.0):
            content(frame)
            dialog_buttons("Delete", "Cancel")
            pos, size = imgui.get_window_pos(), imgui.get_window_size()
            end_dialog()
        imgui.end_frame()
    assert size.x <= 390
    assert content_width[0] < size.x
    assert _centred_on(pos, size, 390, 844)


@pytest.mark.usefixtures("context")
def test_moved_dialog_is_centred_on_a_resized_screen() -> None:
    def before(frame: int) -> None:
        if frame == 4:
            imgui.set_window_pos("Delete", imgui.ImVec2(20, 30))
        _resize_screen(7, 1200, 900)(frame)

    pos, size = _frames(10, lambda _: imgui.text("Deleting"), before)
    assert _centred_on(pos, size, 1200, 900)


@pytest.mark.usefixtures("context")
def test_tool_window_is_centred_on_a_resized_screen() -> None:
    pos = size = imgui.ImVec2(0, 0)
    resize = _resize_screen(4, 390, 844)
    for frame in range(8):
        imgui.new_frame()
        resize(frame)
        begin_tool_window("Tool", None, width=500.0, height=300.0)
        pos, size = imgui.get_window_pos(), imgui.get_window_size()
        imgui.end()
        imgui.end_frame()
    assert size.x <= 390
    assert _centred_on(pos, size, 390, 844)

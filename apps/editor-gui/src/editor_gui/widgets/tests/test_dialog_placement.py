"""A dialog stays centred while its size settles or its content changes, until the user moves it."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog

_TEXT = (
    "Delete '1.0.33  Heating Actuator 8-fold, 4SU MDRC, 24/230VAC'? Its com-objects and "
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

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


def _drag(at: int, to: imgui.ImVec2) -> Callable[[int], None]:
    """Move the dialog to ``to`` in frame ``at`` with the mouse button held, as a drag does."""

    def before(frame: int) -> None:
        io = imgui.get_io()
        if frame == at - 1:
            io.add_mouse_button_event(0, True)  # held from the next frame on
        if frame == at:
            imgui.set_window_pos("Delete", to)
            io.add_mouse_button_event(0, False)

    return before


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
    def content(frame: int) -> None:
        imgui.text("Deleting")
        if frame >= 6:
            imgui.dummy(imgui.ImVec2(300, 200))

    pos, size = _frames(10, content, _drag(4, imgui.ImVec2(20, 30)))
    assert size.y > 200
    assert (pos.x, pos.y) == (20, 30)


@pytest.mark.usefixtures("context")
def test_shifted_without_a_drag_is_centred_again() -> None:
    # ImGui shifts a window a little to keep it on the screen (a dialog that is as tall as the
    # screen in its first frame): no move by the user, so it is centred again.
    def shift(frame: int) -> None:
        if frame == 4:
            imgui.set_window_pos("Delete", imgui.ImVec2(20, 30))

    pos, size = _frames(8, lambda _: imgui.text("Deleting"), shift)
    assert _centred(pos, size)


@pytest.mark.usefixtures("context")
def test_a_minimum_width_from_the_first_frame() -> None:
    sizes: list[imgui.ImVec2] = []
    for frame in range(6):
        imgui.new_frame()
        if frame == 0:
            imgui.open_popup("Working")
        if begin_dialog("Working", min_width=560.0):
            imgui.text_wrapped(_TEXT)
            sizes.append(imgui.get_window_size())
            pos = imgui.get_window_pos()
            end_dialog()
        imgui.end_frame()
    # Wrapped at the minimum width at once, not character by character into a tall first frame.
    assert all(size.y < 300 for size in sizes[1:])
    assert sizes[-1].x >= 560
    assert _centred(pos, sizes[-1])


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
        _drag(4, imgui.ImVec2(20, 30))(frame)
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


def _menu_bar() -> None:
    """The main menu bar, which takes the top of the screen from the room windows have."""
    if imgui.begin_main_menu_bar():
        imgui.text("File")
        imgui.end_main_menu_bar()


def _settings_frames(
    count: int, before: Callable[[int], None] | None = None
) -> list[tuple[float, float, float, float]]:
    """Render a resizable dialog below a menu bar ``count`` frames; its rect in each frame."""
    rects: list[tuple[float, float, float, float]] = []
    for frame in range(count):
        imgui.new_frame()
        if before is not None:
            before(frame)
        _menu_bar()
        if frame == 0:
            imgui.open_popup("Settings")
        if begin_dialog("Settings", width=820.0, height=680.0):
            imgui.text("Language")
            pos, size = imgui.get_window_pos(), imgui.get_window_size()
            rects.append((pos.x, pos.y, size.x, size.y))
            end_dialog()
        imgui.end_frame()
    return rects


@pytest.mark.usefixtures("context")
def test_stays_put_below_a_menu_bar_on_a_short_screen() -> None:
    # Centred on the whole screen, a dialog as tall as the screen reached above the room below
    # the menu bar; imgui pushed it down and it was centred again, every other frame: it jumped.
    imgui.get_io().display_size = imgui.ImVec2(520, 430)
    rects = _settings_frames(12)
    assert len(set(rects[4:])) == 1
    work = imgui.get_main_viewport()
    _x, y, width, height = rects[-1]
    assert y >= work.work_pos.y
    assert y + height <= work.work_pos.y + work.work_size.y
    assert abs(y + height / 2 - (work.work_pos.y + work.work_size.y / 2)) <= 1
    assert width <= 520


@pytest.mark.usefixtures("context")
def test_resizable_dialog_gets_its_size_again_on_a_larger_screen() -> None:
    # A browser window made small and then large again: the dialog is as large as it is meant
    # to be again, not as small as the small screen made it.
    imgui.get_io().display_size = imgui.ImVec2(390, 600)
    rects = _settings_frames(12, _resize_screen(5, 1280, 900))
    small, large = rects[3], rects[-1]
    assert small[2] <= 390
    assert (large[2], large[3]) == (820, 680)


def _shown_places(
    content: Callable[[], None], width: float | None
) -> list[tuple[int, int]]:
    """Where the dialog is in each frame it shows, opened twice (the second time imgui knows its
    window already)."""
    from imgui_bundle.imgui import internal

    places: list[tuple[int, int]] = []
    for _opening in range(2):
        for frame in range(8):
            imgui.new_frame()
            if frame == 0:
                imgui.open_popup("Delete")
            if begin_dialog("Delete", width=width):
                content()
                closed = dialog_buttons("Delete", "Cancel") is not None or frame == 7
                if not internal.get_current_window().hidden:
                    pos = imgui.get_window_pos()
                    places.append((round(pos.x), round(pos.y)))
                if closed:
                    imgui.close_current_popup()
                end_dialog()
            imgui.end_frame()
    return places


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("width", [None, 420.0])
def test_a_dialog_shows_only_where_it_stays(width: float | None) -> None:
    # Wrapped text makes a dialog that fits its content tall at first: it is hidden until its
    # size settled, so it never shows anywhere but centred, also when opened again.
    places = _shown_places(lambda: imgui.text_wrapped(_TEXT), width)
    assert places
    assert set(places) == {places[0]}

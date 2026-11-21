"""Selecting help text: dragging over it selects, a double click a word, Ctrl+A everything, and a
copy keeps the text as written (wrapped lines joined by spaces, blocks by line feeds, list items
with their marker)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import text_selection
from editor_gui.widgets.markdown import render_markdown
from editor_gui.widgets.text_selection import TextSelection

_TEXT = (
    "# Title\n\n"
    "A paragraph long enough to wrap over more than one line of the narrow pane.\n\n"
    "- An item\n"
    "1. A step"
)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.delta_time = 1 / 60
    yield
    imgui.destroy_context(ctx)


@pytest.fixture
def clipboard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    # Not the clipboard of the machine the tests run on.
    copied: list[str] = []
    monkeypatch.setattr(imgui, "set_clipboard_text", copied.append)
    return copied


def _frame(
    selection: TextSelection,
    text: str = _TEXT,
    before: Callable[[], None] = lambda: None,
) -> None:
    before()
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(260, 400))
    imgui.begin("Help", None, imgui.WindowFlags_.no_saved_settings)
    render_markdown(text, selection=selection)
    imgui.end()
    imgui.end_frame()


def _mouse(x: float, y: float, down: bool | None = None) -> Callable[[], None]:
    def events() -> None:
        io = imgui.get_io()
        io.add_mouse_pos_event(x, y)
        if down is not None:
            io.add_mouse_button_event(0, down)

    return events


def _lines(selection: TextSelection) -> list[text_selection._Line]:  # pyright: ignore[reportPrivateUsage]
    return selection._lines  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("context")
def test_dragging_over_the_text_selects_it() -> None:
    selection = TextSelection("help")
    _frame(selection)
    lines = _lines(selection)
    paragraph = [
        line
        for line in lines
        if line.joiner == " " or line.text.startswith("A paragraph")
    ]
    assert len(paragraph) > 1, "the paragraph wraps in the narrow pane"
    first, last = paragraph[0], paragraph[-1]
    # Pressed before the paragraph's first letter, released after its last one.
    _frame(selection, before=_mouse(first.x + 1, first.y + first.height / 2))
    _frame(selection, before=_mouse(first.x + 1, first.y + first.height / 2, down=True))
    end_x = last.x + 250
    _frame(selection, before=_mouse(end_x, last.y + last.height / 2))
    _frame(selection, before=_mouse(end_x, last.y + last.height / 2, down=False))
    assert selection.selected_text() == (
        "A paragraph long enough to wrap over more than one line of the narrow pane."
    )


@pytest.mark.usefixtures("context")
def test_a_double_click_selects_a_word() -> None:
    selection = TextSelection("help")
    _frame(selection)
    title = _lines(selection)[0]
    point = (title.x + 3, title.y + title.height / 2)
    _frame(selection, before=_mouse(*point))
    for down in (True, False, True, False):
        _frame(selection, before=_mouse(*point, down=down))
    assert selection.selected_text() == "Title"


@pytest.mark.usefixtures("context")
def test_a_copy_of_everything_keeps_the_blocks_and_the_list_markers(
    clipboard: list[str],
) -> None:
    selection = TextSelection("help")
    _frame(selection)
    selection.select_all()
    _frame(selection)
    assert selection.selected_text() == (
        "Title\n"
        "A paragraph long enough to wrap over more than one line of the narrow pane.\n"
        "• An item\n"
        "1. A step"
    )
    # Shown in this frame: the web page's copy has it.
    assert text_selection.active_selection() == selection.selected_text()
    selection._copy()  # pyright: ignore[reportPrivateUsage]
    assert clipboard == [selection.selected_text()]


@pytest.mark.usefixtures("context")
def test_ctrl_c_copies_the_selection_of_the_focused_pane(clipboard: list[str]) -> None:
    selection = TextSelection("help")
    _frame(selection)
    title = _lines(selection)[0]
    point = (title.x + 3, title.y + title.height / 2)
    # A click focuses the pane; Ctrl+A selects all of it, Ctrl+C copies it.
    _frame(selection, before=_mouse(*point))
    _frame(selection, before=_mouse(*point, down=True))
    _frame(selection, before=_mouse(*point, down=False))

    def chord(key: imgui.Key, down: bool) -> Callable[[], None]:
        def events() -> None:
            io = imgui.get_io()
            io.add_key_event(imgui.Key.mod_ctrl, down)
            io.add_key_event(key, down)

        return events

    _frame(selection, before=chord(imgui.Key.a, True))
    _frame(selection, before=chord(imgui.Key.a, False))
    _frame(selection, before=chord(imgui.Key.c, True))
    _frame(selection, before=chord(imgui.Key.c, False))
    assert clipboard == [selection.selected_text()]
    assert clipboard[0].startswith("Title\nA paragraph")


@pytest.mark.usefixtures("context")
def test_another_text_drops_the_selection() -> None:
    selection = TextSelection("help")
    _frame(selection)
    selection.select_all()
    _frame(selection)
    assert selection.selected_text()
    _frame(selection, text="Another help")
    assert selection.selected_text() == ""
    # Nothing selected anywhere: nothing for the web page's copy (a frame later).
    _frame(selection, text="Another help")
    assert text_selection.active_selection() == ""


def test_the_word_around_a_place() -> None:
    word = text_selection._word_bounds  # pyright: ignore[reportPrivateUsage]
    assert word("one two_3, four", 5) == (4, 9)
    assert word("one two", 3) == (3, 4)
    assert word("a, b", 1) == (1, 2)
    assert word("", 0) == (0, 0)

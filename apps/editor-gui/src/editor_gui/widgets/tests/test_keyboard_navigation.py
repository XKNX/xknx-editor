"""The keyboard moves through the app as on Windows: Tab through the fields and buttons, Enter
and Space use the one in focus, the arrow keys move through tree rows (selecting them, as a click
does; with Ctrl held only the focus moves), and a text field keeps the keys it edits with."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.rich_text_edit import RichTextEdit
from editor_gui.widgets.tree import row_chosen, tree_node


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard
    yield
    imgui.destroy_context(ctx)


def _press(key: imgui.Key, frame: Callable[[], None], ctrl: bool = False) -> None:
    """Presses and releases ``key`` (with Ctrl held), one frame each, and one frame after."""
    io = imgui.get_io()
    if ctrl:
        io.add_key_event(imgui.Key.mod_ctrl, True)
    io.add_key_event(key, True)
    frame()
    io.add_key_event(key, False)
    frame()
    if ctrl:
        io.add_key_event(imgui.Key.mod_ctrl, False)
    frame()


class _Tree:
    """A panel with five tree rows; which ones were chosen."""

    def __init__(self) -> None:
        self.chosen: list[int] = []
        self.centers: dict[int, tuple[float, float]] = {}

    def frame(self) -> None:
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(300, 300))
        imgui.begin("Panel")
        for i in range(5):
            if tree_node(f"Row {i}", imgui.TreeNodeFlags_.leaf):
                imgui.tree_pop()
            if row_chosen():
                self.chosen.append(i)
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            self.centers[i] = ((low.x + high.x) / 2, (low.y + high.y) / 2)
        imgui.end()
        imgui.end_frame()
        imgui.render()


@pytest.mark.usefixtures("context")
def test_arrow_keys_choose_tree_rows() -> None:
    tree = _Tree()
    tree.frame()
    tree.frame()
    io = imgui.get_io()
    io.add_mouse_pos_event(*tree.centers[1])
    io.add_mouse_button_event(0, True)
    tree.frame()
    io.add_mouse_button_event(0, False)
    tree.frame()
    assert tree.chosen == [1]
    _press(imgui.Key.down_arrow, tree.frame)
    assert tree.chosen == [1, 2]
    _press(imgui.Key.up_arrow, tree.frame)
    _press(imgui.Key.up_arrow, tree.frame)
    assert tree.chosen == [1, 2, 1, 0]
    # With Ctrl held only the focus moves.
    _press(imgui.Key.down_arrow, tree.frame, ctrl=True)
    assert tree.chosen == [1, 2, 1, 0]


def _dialog(keys: list[imgui.Key]) -> list[int | None]:
    """What a "Delete" / "Cancel" dialog gives, while ``keys`` are pressed one after another."""
    clicked: list[int | None] = []

    def frame() -> None:
        imgui.new_frame()
        if imgui.get_frame_count() == 1:
            imgui.open_popup("Delete Space")
        if begin_dialog("Delete Space", width=300):
            imgui.text("Delete this space?")
            clicked.append(dialog_buttons("Delete", "Cancel"))
            end_dialog()
        imgui.end_frame()
        imgui.render()

    frame()
    frame()
    for key in keys:
        _press(key, frame)
    return clicked


@pytest.mark.usefixtures("context")
def test_tab_and_enter_use_the_focused_button() -> None:
    # Tab moves onto "Delete", then onto "Cancel": Enter then uses "Cancel", not the action.
    assert 1 in _dialog([imgui.Key.tab, imgui.Key.tab, imgui.Key.enter])
    assert 0 not in _dialog([imgui.Key.tab, imgui.Key.tab, imgui.Key.enter])
    # Space uses the focused button, too.
    assert 1 in _dialog([imgui.Key.tab, imgui.Key.tab, imgui.Key.space])
    # On "Delete", or with nothing in focus, Enter accepts.
    assert 0 in _dialog([imgui.Key.tab, imgui.Key.enter])
    assert 0 in _dialog([imgui.Key.enter])


@pytest.mark.usefixtures("context")
def test_the_comment_field_keeps_its_keys() -> None:
    edit = RichTextEdit("comment")
    focused: list[bool] = []

    def frame() -> None:
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(500, 400))
        imgui.begin("Panel")
        imgui.button("Before")
        edit.render("device", "", 200.0, "Comment")
        imgui.button("After")
        focused.append(edit._focused)  # pyright: ignore[reportPrivateUsage]
        imgui.end()
        imgui.end_frame()
        imgui.render()

    frame()
    frame()
    edit._focused = True  # pyright: ignore[reportPrivateUsage]
    frame()
    context = imgui.get_current_context()
    nav = context.nav_id
    for key in (imgui.Key.down_arrow, imgui.Key.tab, imgui.Key.right_arrow):
        _press(key, frame)
    # Nothing moved the focus away from the text: the field still has the caret.
    assert focused[-1]
    assert context.nav_id == nav
    assert not context.nav_cursor_visible
    # Without the caret, Tab moves on (the first press shows where the focus is).
    edit._focused = False  # pyright: ignore[reportPrivateUsage]
    frame()
    _press(imgui.Key.tab, frame)
    _press(imgui.Key.tab, frame)
    assert context.nav_id != nav

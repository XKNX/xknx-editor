"""The keys of a dialog with an action and a dismiss button: Enter presses the action (e.g.
"Delete"), Escape the dismiss button ("Cancel")."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _pressed(
    key: imgui.Key | None,
    labels: Sequence[str] = ("Delete", "Cancel"),
    enabled: Sequence[bool] | None = None,
) -> list[int | None]:
    """What the dialog's buttons give over a few frames, ``key`` pressed in the third."""
    clicked: list[int | None] = []
    io = imgui.get_io()
    for frame in range(5):
        if key is not None and frame == 2:
            io.add_key_event(key, True)
        if key is not None and frame == 3:
            io.add_key_event(key, False)
        imgui.new_frame()
        if frame == 0:
            imgui.open_popup("Delete Space")
        if begin_dialog("Delete Space", width=300):
            imgui.text("Delete this space?")
            clicked.append(dialog_buttons(*labels, enabled=enabled))
            end_dialog()
        imgui.end_frame()
        imgui.render()
    return clicked


@pytest.mark.usefixtures("context")
def test_enter_accepts_and_escape_cancels() -> None:
    assert 0 in _pressed(imgui.Key.enter)
    assert 0 in _pressed(imgui.Key.keypad_enter)
    assert 1 in _pressed(imgui.Key.escape)
    assert set(_pressed(None)) == {None}


@pytest.mark.usefixtures("context")
def test_enter_does_nothing_where_it_should_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The action is grayed out.
    assert set(_pressed(imgui.Key.enter, enabled=(False, True))) == {None}
    # Only a dismiss button (e.g. "Close"): Enter is no action there.
    assert set(_pressed(imgui.Key.enter, labels=("Close",))) == {None}
    # A text field still being edited (a multi-line one) takes the Enter.
    monkeypatch.setattr(imgui, "is_any_item_active", lambda: True)
    assert set(_pressed(imgui.Key.enter)) == {None}

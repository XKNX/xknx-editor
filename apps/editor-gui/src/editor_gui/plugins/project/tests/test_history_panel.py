"""The History panel: a click (or a tap) selects a step, Restore brings the project to it; a
double click restores at once. Nothing depends on hovering."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.history import HistoryEntry, HistoryPanel


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _History:
    """Three steps, the newest first; the cursor on the newest."""

    def __init__(self) -> None:
        self.cursor = 3
        self.jumps: list[int] = []

    def entries(self) -> list[HistoryEntry]:
        return [
            HistoryEntry(id, f"Step {id}", reverted=id > self.cursor)
            for id in (3, 2, 1)
        ]

    def jump(self, entry_id: int) -> None:
        self.jumps.append(entry_id)
        self.cursor = entry_id


def _panel(history: _History) -> HistoryPanel:
    return HistoryPanel(history.entries, lambda: history.cursor, history.jump)


class _Places:
    """Where each step's row and the Restore button were drawn in the last frame."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rows: dict[str, imgui.ImVec2] = {}
        self.button = imgui.ImVec2(0, 0)
        text = imgui.text
        button = imgui.button

        def record_text(value: str) -> None:
            text(value)
            self.rows[value] = imgui.get_item_rect_min()

        def record_button(label: str, *args: object) -> bool:
            pressed = button(label, *args)  # type: ignore[arg-type]
            if label == S.HISTORY_REVERT:
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                self.button = imgui.ImVec2((low.x + high.x) / 2, (low.y + high.y) / 2)
            return pressed

        monkeypatch.setattr(imgui, "text", record_text)
        monkeypatch.setattr(imgui, "button", record_button)


def _frame(panel: HistoryPanel) -> None:
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(400, 300))
    imgui.begin("History")
    panel.render()
    imgui.end()
    imgui.end_frame()


def _click(panel: HistoryPanel, at: imgui.ImVec2, *, double: bool = False) -> None:
    io = imgui.get_io()
    io.add_mouse_pos_event(at.x + 2, at.y + 2)
    _frame(panel)
    for _ in range(2 if double else 1):
        io.add_mouse_button_event(0, True)
        _frame(panel)
        io.add_mouse_button_event(0, False)
        _frame(panel)


def test_a_click_selects_a_step_and_restore_brings_the_project_to_it(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _History()
    panel = _panel(history)
    places = _Places(monkeypatch)
    _frame(panel)
    # Nothing selected: Restore cannot do anything yet.
    assert not panel.restorable(history.cursor)
    _click(panel, places.rows["Step 1"])
    assert panel.selected == 1 and history.jumps == []
    assert panel.restorable(history.cursor)
    _click(panel, places.button)
    assert history.jumps == [1]
    assert panel.selected is None


def test_restore_is_off_for_the_current_step(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _History()
    panel = _panel(history)
    places = _Places(monkeypatch)
    _frame(panel)
    _click(panel, places.rows["Step 3"])
    assert panel.selected == 3
    assert not panel.restorable(history.cursor)
    _click(panel, places.button)
    assert history.jumps == []


def test_a_double_click_restores_at_once(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _History()
    panel = _panel(history)
    places = _Places(monkeypatch)
    _frame(panel)
    _click(panel, places.rows["Step 2"], double=True)
    assert history.jumps == [2]


def test_a_selection_of_a_step_that_is_gone_is_dropped(context: None) -> None:
    history = _History()
    panel = _panel(history)
    panel.selected = 7
    _frame(panel)
    assert panel.selected is None


def test_without_steps_the_panel_says_so(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(imgui, "text_disabled", shown.append)
    panel = HistoryPanel(list, lambda: 0, lambda _id: None)
    _frame(panel)
    assert shown == [S.HISTORY_NO_HISTORY]

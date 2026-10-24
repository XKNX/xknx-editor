"""Panels asked to come to the front while a modal dialog is open wait until it is closed, so the
dialog stays open (e.g. when the browser view switches to another layout)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from editor_gui.main import KnxGuiApp


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> list[SimpleNamespace]:
    panels = [
        SimpleNamespace(label="Buildings###buildings", focus_window_at_next_frame=True),
        SimpleNamespace(label="Editor###editor", focus_window_at_next_frame=False),
    ]
    params = SimpleNamespace(docking_params=SimpleNamespace(dockable_windows=panels))
    monkeypatch.setattr("editor_gui.main.hello_imgui.get_runner_params", lambda: params)
    return panels


def _modal(monkeypatch: pytest.MonkeyPatch, window: object) -> None:
    monkeypatch.setattr(
        "editor_gui.main.imgui.internal.get_top_most_popup_modal", lambda: window
    )


def test_panels_come_to_the_front_after_the_dialog(
    monkeypatch: pytest.MonkeyPatch, windows: list[SimpleNamespace]
) -> None:
    stub = SimpleNamespace(_focus_after_dialog=[])

    _modal(monkeypatch, object())
    KnxGuiApp._keep_dialog_open(stub)  # type: ignore[arg-type]
    assert not windows[0].focus_window_at_next_frame
    assert stub._focus_after_dialog == ["buildings"]

    _modal(monkeypatch, None)
    KnxGuiApp._keep_dialog_open(stub)  # type: ignore[arg-type]
    assert windows[0].focus_window_at_next_frame
    assert not windows[1].focus_window_at_next_frame
    assert stub._focus_after_dialog == []


def test_without_a_dialog_panels_come_to_the_front_at_once(
    monkeypatch: pytest.MonkeyPatch, windows: list[SimpleNamespace]
) -> None:
    stub = SimpleNamespace(_focus_after_dialog=[])
    _modal(monkeypatch, None)
    KnxGuiApp._keep_dialog_open(stub)  # type: ignore[arg-type]
    assert windows[0].focus_window_at_next_frame
    assert stub._focus_after_dialog == []

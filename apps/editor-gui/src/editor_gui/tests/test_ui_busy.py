"""The app notes when the user last moved the mouse, clicked or typed: work in the background waits
for a pause (the evaluation of devices opened from a snapshot of another version)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui import main
from editor_gui.main import KnxGuiApp


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.end_frame()
    yield
    imgui.destroy_context(ctx)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [100.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    return now


def _frame(events: Callable[[imgui.IO], None] | None = None) -> None:
    """One frame with ``events``; the app notes its input before the next, as pre_new_frame."""
    io = imgui.get_io()
    if events is not None:
        events(io)
    imgui.new_frame()
    imgui.end_frame()


def _app() -> Any:
    return cast(Any, SimpleNamespace(_last_input=0.0))


def _busy(app: Any) -> bool:
    return KnxGuiApp._ui_busy(app)


@pytest.mark.usefixtures("context")
def test_no_input_is_no_work(clock: list[float]) -> None:
    app = _app()
    _frame()
    KnxGuiApp.note_input(app)
    assert not _busy(app)


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize(
    "events",
    [
        lambda io: io.add_mouse_pos_event(40, 30),
        lambda io: io.add_mouse_wheel_event(0, 1),
        lambda io: io.add_mouse_button_event(0, True),
        lambda io: io.add_key_event(imgui.Key.down_arrow, True),
    ],
    ids=["move", "wheel", "button", "key"],
)
def test_input_counts_for_a_second(
    clock: list[float], events: Callable[[imgui.IO], None]
) -> None:
    app = _app()
    _frame(lambda io: io.add_mouse_pos_event(10, 10))
    _frame()
    _frame(events)
    KnxGuiApp.note_input(app)
    assert app._last_input == 100.0
    clock[0] += main._BUSY_AFTER_INPUT - 0.1
    assert _busy(app)
    clock[0] += 0.2
    assert not _busy(app)

"""Finger gestures of the browser UI and its layouts by screen size."""

from __future__ import annotations

from typing import Any

from editor_gui.main import compact_docking_splits, compact_section, form_factor
from editor_gui.web.touch import SLOP, TouchGestures


class FakeUi:
    """Records what the gestures do; ``target`` is what a swipe scrolls."""

    def __init__(self, target: str | None = "list") -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.target = target
        self.frames = 0
        self.scrolled = [0.0, 0.0]
        self.room = True

    def frame(self) -> int:
        return self.frames

    def move(self, x: float, y: float) -> None:
        self.calls.append(("move", x, y))

    def button(self, button: int, down: bool) -> None:
        self.calls.append(("button", button, down))

    def leave(self) -> None:
        self.calls.append(("leave",))

    def scroll_target(self, vertical: bool) -> str | None:
        self.calls.append(("target", vertical))
        return self.target

    def cancel_press(self) -> None:
        self.calls.append(("cancel",))

    def scroll(self, target: str, dx: float, dy: float) -> bool:
        self.scrolled[0] += dx
        self.scrolled[1] += dy
        return self.room

    def buttons(self) -> list[tuple[Any, ...]]:
        return [c for c in self.calls if c[0] == "button"]


def finger(
    gestures: TouchGestures,
    ui: FakeUi,
    phase: str,
    x: float = 0,
    y: float = 0,
    ms: float = 0,
) -> None:
    event = {"t": "touch", "p": phase, "x": x, "y": y}
    if gestures.needs_frame(event):
        ui.frames += 1  # the frame after the touch
    gestures.handle(event, ms / 1000.0)


def test_tap_clicks_at_the_release() -> None:
    ui = FakeUi()
    g = TouchGestures(ui)
    finger(g, ui, "down", 10, 20)
    assert ui.calls == [("move", 10, 20)]
    finger(g, ui, "move", 12, 21)
    finger(g, ui, "up", 12, 21)
    assert ui.buttons() == [("button", 0, True), ("button", 0, False)]
    assert ui.calls[-1] == ("leave",)


def test_swipe_scrolls_and_never_presses() -> None:
    ui = FakeUi()
    g = TouchGestures(ui)
    finger(g, ui, "down", 100, 300)
    finger(g, ui, "move", 100, 250, ms=16)
    finger(g, ui, "move", 100, 200, ms=32)
    g.step(0.0)
    assert ui.buttons() == []
    assert ("target", True) in ui.calls
    # The content follows the finger from where it touched.
    assert ui.scrolled == [0.0, -100.0]


def test_swipe_waits_for_the_frame_after_the_touch() -> None:
    ui = FakeUi()
    g = TouchGestures(ui)
    finger(g, ui, "down", 0, 0)
    move = {"t": "touch", "p": "move", "x": 0, "y": SLOP + 5}
    assert g.needs_frame(move)
    ui.frames += 1
    assert not g.needs_frame(move)
    # Small moves are no swipe yet and need no frame.
    assert not g.needs_frame({"t": "touch", "p": "move", "x": 0, "y": SLOP - 1})


def test_fast_swipe_moves_on_and_slows_down() -> None:
    ui = FakeUi()
    g = TouchGestures(ui)
    finger(g, ui, "down", 0, 500)
    for i in range(1, 6):
        finger(g, ui, "move", 0, 500 - 40 * i, ms=16 * i)
    finger(g, ui, "up", 0, 300, ms=90)
    g.step(0.0)
    assert g.active
    before = ui.scrolled[1]
    g.step(1.0)  # first step of the momentum
    g.step(1.016)
    first = ui.scrolled[1] - before
    assert first < 0
    for i in range(2, 200):
        g.step(1.0 + 0.016 * i)
    assert not g.active
    later = ui.scrolled[1]
    g.step(10.0)
    assert ui.scrolled[1] == later


def test_slow_release_has_no_momentum() -> None:
    ui = FakeUi()
    g = TouchGestures(ui)
    finger(g, ui, "down", 0, 500)
    finger(g, ui, "move", 0, 400, ms=16)
    # The finger rests before it lifts.
    finger(g, ui, "up", 0, 400, ms=500)
    g.step(0.0)
    assert not g.active


def test_no_scroll_target_drags_like_the_mouse() -> None:
    ui = FakeUi(target=None)
    g = TouchGestures(ui)
    finger(g, ui, "down", 50, 50)
    finger(g, ui, "move", 80, 50)
    finger(g, ui, "move", 90, 50)
    finger(g, ui, "up", 90, 50)
    assert ui.calls == [
        ("move", 50, 50),
        ("target", False),
        ("button", 0, True),
        ("move", 80, 50),
        ("move", 90, 50),
        ("move", 90, 50),
        ("button", 0, False),
        ("leave",),
    ]


def test_hold_and_release_is_a_right_click() -> None:
    ui = FakeUi()
    g = TouchGestures(ui)
    finger(g, ui, "down", 5, 5)
    finger(g, ui, "hold")
    finger(g, ui, "up", 5, 5)
    assert ui.buttons() == [("button", 1, True), ("button", 1, False)]


def test_hold_and_move_drags_even_where_a_swipe_scrolls() -> None:
    ui = FakeUi()
    g = TouchGestures(ui)
    finger(g, ui, "down", 5, 5)
    finger(g, ui, "hold")
    finger(g, ui, "move", 5, 60)
    finger(g, ui, "up", 5, 60)
    assert ui.buttons() == [("button", 0, True), ("button", 0, False)]
    assert ("target", True) not in ui.calls
    assert ui.scrolled == [0.0, 0.0]


def test_second_finger_cancels_a_drag() -> None:
    ui = FakeUi(target=None)
    g = TouchGestures(ui)
    finger(g, ui, "down", 0, 0)
    finger(g, ui, "move", 30, 0)
    finger(g, ui, "cancel")
    assert ui.calls[-3:] == [("cancel",), ("button", 0, False), ("leave",)]
    assert not g.active


def test_cancel_before_anything_presses_nothing() -> None:
    ui = FakeUi()
    g = TouchGestures(ui)
    finger(g, ui, "down", 0, 0)
    finger(g, ui, "cancel")
    assert ui.buttons() == []


def test_form_factor() -> None:
    assert form_factor(1920, 1080) == "wide"
    assert form_factor(1280, 800) == "wide"  # a landscape tablet
    assert form_factor(800, 1280) == "rows3"  # the same tablet upright
    assert form_factor(412, 915) == "rows"  # a phone upright
    assert form_factor(915, 412) == "columns"  # a phone on its side


def test_form_factor_does_not_flip_at_the_limit() -> None:
    assert form_factor(990, 700, "wide") == "wide"
    assert form_factor(1010, 700, "columns") == "columns"
    assert form_factor(1050, 700, "columns") == "wide"
    assert form_factor(950, 700, "wide") == "columns"


def test_compact_sections() -> None:
    assert compact_section("LeftSpace", "rows") == "LeftSpace"
    assert compact_section("DeviceSpace", "rows") == "MainDockSpace"
    assert compact_section("BottomSpace", "rows") == "MainDockSpace"
    assert compact_section("BottomSpace", "rows3") == "BottomSpace"
    assert compact_section("RightSpace", "columns") == "MainDockSpace"
    for form in ("rows", "rows3", "columns"):
        made = {split.new_dock for split in compact_docking_splits(form)}
        wanted = {
            compact_section(s, form)
            for s in ("LeftSpace", "DeviceSpace", "RightSpace", "BottomSpace")
        } - {"MainDockSpace"}
        assert made == wanted

"""Touch input of the browser UI: a finger works like the mouse where that fits, and like on a
touch screen where the mouse has nothing alike.

- A tap clicks. What its press opens under the finger (a submenu at the screen's edge, a
  drop-down list) is not clicked by its release.
- A swipe scrolls the list or panel under the finger, with momentum, in the direction it
  started in (up and down, or sideways); sideways on a tab bar it moves the tabs. Where nothing
  scrolls that way, or the finger is on a scroll bar, a window's title or something that shows a
  resize pointer (a splitter, a column border), it drags like the mouse (also a slider moved
  sideways).
- Holding the finger still for a moment (the page tells when) and lifting it is a right-click,
  for the context menus; holding and then moving drags like the mouse (drag and drop, selecting
  text).

The button goes down only once it is clear which of these it is: at the release of a tap, or when
a move turns out to be a drag. A swipe never presses anything, also not what acts as soon as it
is pressed (tabs, tree nodes). Until then the finger only moves the pointer.

The page sends a finger's phases (``down``, ``move``, ``hold``, ``up``, ``cancel``) in app units;
a second finger (zoom) cancels the first one. :class:`TouchGestures` turns them into mouse input
and scrolling through a :class:`TouchUi`, which the web mode implements with ImGui. The look for
touch (larger hit areas) is :class:`TouchLook`."""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any, Protocol

from imgui_bundle import imgui

from editor_gui.widgets.tabs import shown_tab_bar_ids

# How far a finger may move (app units) and still tap or hold.
SLOP = 8.0
# Slowest release speed (app units per second) that keeps scrolling on, and where it stops.
_FLING_START = 120.0
_FLING_STOP = 15.0
# Time constant of the momentum's slowing down, in seconds.
_FLING_DECAY = 0.325
# A finger at rest this long before lifting leaves no momentum.
_FLING_REST = 0.08
# Pointers shown over what is dragged to resize or move (splitters, borders).
_DRAG_CURSORS = frozenset(
    {
        imgui.MouseCursor_.resize_all,
        imgui.MouseCursor_.resize_ns,
        imgui.MouseCursor_.resize_ew,
        imgui.MouseCursor_.resize_nesw,
        imgui.MouseCursor_.resize_nwse,
    }
)


class TouchUi(Protocol):
    """What the gestures do in the app."""

    def frame(self) -> int:
        """The number of the last frame."""
        ...

    def move(self, x: float, y: float) -> None: ...

    def button(self, button: int, down: bool) -> None: ...

    def leave(self) -> None:
        """No pointer over the app (nothing stays hovered after a finger lifts)."""
        ...

    def scroll_target(self, vertical: bool) -> str | None:
        """The window that a swipe at the pointer scrolls in that direction, as seen in the last
        frame; None where the finger drags something (or nothing scrolls)."""
        ...

    def cancel_press(self) -> None:
        """Forget a press, so that releasing the button does not click."""
        ...

    def scroll(self, target: str, dx: float, dy: float) -> bool:
        """Scroll ``target`` by the finger's movement; False when it did not move (an end, or
        the window is gone)."""
        ...

    def pointer_window(self) -> int:
        """The window under the pointer as seen in the last frame (its root window's id), 0 for
        none."""
        ...


# Frames a tap's release waits after its press: the press is handled in the first, what it
# opens is drawn there, and the frame after it finds that under the pointer.
_TAP_RELEASE_FRAMES = 2


class TouchGestures:
    """One finger's gestures; see the module."""

    def __init__(self, ui: TouchUi) -> None:
        self._ui = ui
        self._state = "idle"
        self._start = (0.0, 0.0)
        self._last = (0.0, 0.0)
        self._down_frame = -1
        self._target: str | None = None
        # The direction a swipe scrolls in: up and down, else sideways.
        self._vertical = True
        # Scrolling still to do (finger movement since the last frame).
        self._pending = (0.0, 0.0)
        self._velocity = (0.0, 0.0)
        self._last_move = 0.0
        self._flinging = False
        self._fling_time = 0.0
        # A tap pressed and not released yet: the frame it pressed in and the window that was
        # under the finger then.
        self._tap: tuple[int, int] | None = None

    @property
    def active(self) -> bool:
        """A finger is down, a tap waits for its release, or the content still moves on."""
        return self._state != "idle" or self._flinging or self._tap is not None

    def needs_frame(self, event: dict[str, Any]) -> bool:
        """Whether ``event`` has to wait for the next frame: whether a swipe scrolls depends on
        what is under the finger, which the frame after it touched shows."""
        return (
            self._state == "touched"
            and event.get("p") == "move"
            and self._beyond_slop(event)
            and self._ui.frame() <= self._down_frame
        )

    def handle(self, event: dict[str, Any], now: float) -> None:
        """Apply one finger event; ``now`` in seconds (the page's time stamp)."""
        phase = event.get("p")
        x = float(event.get("x", self._last[0]))
        y = float(event.get("y", self._last[1]))
        ui = self._ui
        if phase == "down":
            # The next finger comes before the last tap's release: release it now.
            self._release_tap(away=False)
            self._flinging = False
            self._target = None
            self._pending = (0.0, 0.0)
            self._state = "touched"
            self._start = self._last = (x, y)
            ui.move(x, y)
            self._down_frame = ui.frame()
            return
        if phase == "move":
            if self._state == "touched" and self._beyond_slop(event):
                dx, dy = x - self._start[0], y - self._start[1]
                self._vertical = abs(dy) >= abs(dx)
                target = ui.scroll_target(self._vertical)
                if target is None:
                    self._drag(x, y)
                else:
                    ui.leave()
                    self._state = "scroll"
                    self._target = target
                    self._velocity = (0.0, 0.0)
                    self._last_move = now
                    # The content follows the finger from where it touched it.
                    self._pending = self._along(dx, dy)
            elif self._state == "hold" and self._beyond_slop(event):
                self._drag(x, y)
            elif self._state == "drag":
                ui.move(x, y)
            elif self._state == "scroll":
                dx, dy = self._along(x - self._last[0], y - self._last[1])
                self._pending = (self._pending[0] + dx, self._pending[1] + dy)
                dt = now - self._last_move
                if dt > 0:
                    # Weighted towards the latest movement: that is the speed at the release.
                    vx, vy = self._velocity
                    self._velocity = (
                        0.4 * vx + 0.6 * dx / dt,
                        0.4 * vy + 0.6 * dy / dt,
                    )
                self._last_move = now
            self._last = (x, y)
            return
        if phase == "hold":
            if self._state == "touched":
                self._state = "hold"
            return
        if phase == "up":
            if self._state == "touched":
                # Released once it is clear what the press opened (see step).
                ui.button(0, True)
                self._tap = (ui.frame(), ui.pointer_window())
            elif self._state == "hold":
                # A long press: the right button.
                ui.button(1, True)
                ui.button(1, False)
                ui.leave()
            elif self._state == "drag":
                ui.move(x, y)
                ui.button(0, False)
                ui.leave()
            elif self._state == "scroll":
                speed = math.hypot(*self._velocity)
                if speed >= _FLING_START and now - self._last_move <= _FLING_REST:
                    self._flinging = True
                    self._fling_time = -1.0
            self._state = "idle"
            return
        if phase == "cancel":
            if self._state == "drag":
                ui.cancel_press()
                ui.button(0, False)
            if self._state != "idle":
                ui.leave()
            self._state = "idle"

    def step(self, now: float) -> None:
        """Once per frame (``now`` in seconds, any clock): release a tap, scroll by the finger's
        movement, or let the content move on after a swipe."""
        if self._tap is not None:
            pressed, window = self._tap
            if self._ui.frame() >= pressed + _TAP_RELEASE_FRAMES:
                # Another window under the finger now: the press opened it there, and the
                # release must not click what it shows.
                self._release_tap(away=self._ui.pointer_window() != window)
        target = self._target
        if target is None:
            return
        dx, dy = self._pending
        self._pending = (0.0, 0.0)
        if self._state == "scroll":
            if dx or dy:
                self._ui.scroll(target, dx, dy)
            return
        if not self._flinging:
            return
        if self._fling_time < 0:
            self._fling_time = now
            return
        dt = min(now - self._fling_time, 0.1)
        self._fling_time = now
        vx, vy = self._velocity
        moved = self._ui.scroll(target, vx * dt, vy * dt)
        decay = math.exp(-dt / _FLING_DECAY)
        self._velocity = (vx * decay, vy * decay)
        if not moved or math.hypot(*self._velocity) < _FLING_STOP:
            self._flinging = False

    def _release_tap(self, *, away: bool) -> None:
        """Release a tap's press, with the pointer off the app when ``away``."""
        if self._tap is None:
            return
        self._tap = None
        if away:
            self._ui.leave()
        self._ui.button(0, False)
        self._ui.leave()

    def _along(self, dx: float, dy: float) -> tuple[float, float]:
        """The part of a movement in the direction the swipe scrolls in."""
        return (0.0, dy) if self._vertical else (dx, 0.0)

    def _drag(self, x: float, y: float) -> None:
        """Press where the finger touched and follow it, like the mouse."""
        self._state = "drag"
        self._ui.button(0, True)
        self._ui.move(x, y)

    def _beyond_slop(self, event: dict[str, Any]) -> bool:
        dx = float(event.get("x", self._start[0])) - self._start[0]
        dy = float(event.get("y", self._start[1])) - self._start[1]
        return math.hypot(dx, dy) > SLOP


def _room(window: imgui.internal.Window, vertical: bool) -> float:
    """How far a window can be scrolled in that direction, as imgui lets the mouse wheel scroll
    it: sideways only where the window scrolls sideways (a few pixels of content past its edge
    do not make a window scroll), not at all where the wheel goes to the window around."""
    flags = window.flags
    if flags & imgui.WindowFlags_.no_scroll_with_mouse:
        return 0.0
    if vertical:
        return window.scroll_max.y
    sideways = (
        imgui.WindowFlags_.horizontal_scrollbar
        | imgui.WindowFlags_.always_horizontal_scrollbar
    )
    return window.scroll_max.x if flags & sideways else 0.0


_TAB_BAR = "tab bar:"


def _tab_bar_room(tab_bar: imgui.internal.TabBar) -> float:
    """How far a tab bar's tabs can be moved: as far as they reach past the bar."""
    width = tab_bar.bar_rect.max.x - tab_bar.bar_rect.min.x
    return max(0.0, tab_bar.width_all_tabs - width)


def _tab_bars() -> Iterator[tuple[str, imgui.internal.TabBar]]:
    """The tab bars a finger can be on, with their scroll targets: those of the sections
    (docking) the windows are in, and the app's own ones."""
    nodes: set[int] = set()
    for window in imgui.get_current_context().windows:
        node = window.dock_node
        if (
            node is None  # pyright: ignore[reportUnnecessaryComparison]
            or node.id_ in nodes
            or node.tab_bar is None  # pyright: ignore[reportUnnecessaryComparison]
            or node.is_hidden_tab_bar()
        ):
            continue
        nodes.add(node.id_)
        yield f"{_TAB_BAR}dock:{node.id_}", node.tab_bar
    for tab_bar_id in shown_tab_bar_ids():
        tab_bar = imgui.internal.tab_bar_find_by_id(tab_bar_id)
        if tab_bar is not None:  # pyright: ignore[reportUnnecessaryComparison]
            yield f"{_TAB_BAR}{tab_bar_id}", tab_bar


def _find_tab_bar(target: str) -> imgui.internal.TabBar | None:
    """The tab bar a scroll target of :func:`_tab_bars` names, while it is there."""
    name = target.removeprefix(_TAB_BAR)
    if name.startswith("dock:"):
        node = imgui.internal.dock_context_find_node_by_id(
            imgui.get_current_context(), int(name.removeprefix("dock:"))
        )
        return node.tab_bar if node is not None else None  # pyright: ignore[reportUnnecessaryComparison]
    return imgui.internal.tab_bar_find_by_id(int(name))


class ImGuiTouchUi:
    """:class:`TouchUi` with ImGui's input queue and windows."""

    def __init__(self, io: imgui.IO) -> None:
        self._io = io

    def frame(self) -> int:
        return imgui.get_frame_count()

    def move(self, x: float, y: float) -> None:
        self._io.add_mouse_source_event(imgui.MouseSource.touch_screen)
        self._io.add_mouse_pos_event(x, y)

    def button(self, button: int, down: bool) -> None:
        self._io.add_mouse_source_event(imgui.MouseSource.touch_screen)
        self._io.add_mouse_button_event(button, down)

    def leave(self) -> None:
        self._io.add_mouse_pos_event(-3.4e38, -3.4e38)

    def scroll_target(self, vertical: bool) -> str | None:
        internal = imgui.internal
        g = imgui.get_current_context()
        # The bindings type these as non-optional, but they can be None.
        if g.drag_drop_active or g.moving_window is not None:  # pyright: ignore[reportUnnecessaryComparison]
            return None
        # A splitter or border under the finger (it shows a resize pointer) is dragged.
        if imgui.get_mouse_cursor() in _DRAG_CURSORS:
            return None
        window = g.hovered_window
        hovered = g.hovered_id
        # A text field taking typing: the finger selects text.
        if hovered and hovered == internal.get_active_id() and self._io.want_text_input:
            return None
        mouse = self._io.mouse_pos
        # Sideways on a tab bar whose tabs do not all fit: the tabs move.
        if not vertical:
            for target, tab_bar in _tab_bars():
                bar = tab_bar.bar_rect
                if (
                    bar.min.x <= mouse.x < bar.max.x
                    and bar.min.y <= mouse.y < bar.max.y
                    and tab_bar.curr_frame_visible >= imgui.get_frame_count() - 1
                ):
                    return target if _tab_bar_room(tab_bar) > 0.0 else None
        while window is not None:  # pyright: ignore[reportUnnecessaryComparison]
            if hovered and hovered in (
                internal.get_window_scrollbar_id(window, internal.Axis.x),
                internal.get_window_scrollbar_id(window, internal.Axis.y),
            ):
                return None
            # The title moves the window.
            if (
                window.title_bar_height > 0.0
                and window.pos.y <= mouse.y < window.pos.y + window.title_bar_height
            ):
                return None
            if _room(window, vertical) > 0.0:
                return window.name
            if not window.flags & imgui.WindowFlags_.child_window:
                return None
            window = window.parent_window
        return None

    def cancel_press(self) -> None:
        imgui.internal.clear_active_id()

    def pointer_window(self) -> int:
        window = imgui.get_current_context().hovered_window
        # The bindings type it as non-optional, but it can be None.
        if window is None:  # pyright: ignore[reportUnnecessaryComparison]
            return 0
        return window.root_window.id_

    def scroll(self, target: str, dx: float, dy: float) -> bool:
        internal = imgui.internal
        if target.startswith(_TAB_BAR):
            tab_bar = _find_tab_bar(target)
            if tab_bar is None or not dx:  # pyright: ignore[reportUnnecessaryComparison]
                return False
            value = min(max(tab_bar.scrolling_target - dx, 0.0), _tab_bar_room(tab_bar))
            if value == tab_bar.scrolling_target:
                return False
            # The tabs follow the finger at once, not in imgui's animation.
            tab_bar.scrolling_target = tab_bar.scrolling_anim = value
            tab_bar.scrolling_target_dist_to_visibility = 0.0
            return True
        window = internal.find_window_by_name(target)
        if window is None:  # pyright: ignore[reportUnnecessaryComparison]
            return False
        moved = False
        for vertical, delta in ((False, dx), (True, dy)):
            room = _room(window, vertical)
            if not delta or room <= 0.0:
                continue
            now = window.scroll.y if vertical else window.scroll.x
            value = min(max(now - delta, 0.0), room)
            if value != now:
                moved = True
                if vertical:
                    internal.set_scroll_y(window, value)
                else:
                    internal.set_scroll_x(window, value)
        return moved


class TouchLook:
    """Larger scroll bars and slider grips while the app is used with a finger; the theme's own
    values otherwise. Applied every frame, so a theme change is taken over. (No touch padding
    around items: it widens every panel's hover area too, which then covers thin splitters
    between panels.)"""

    def __init__(self) -> None:
        self._base: dict[str, Any] = {}
        self._applied: dict[str, Any] = {}

    def apply(self, touch: bool) -> None:
        style = imgui.get_style()
        current: dict[str, Any] = {
            "scrollbar_size": style.scrollbar_size,
            "grab_min_size": style.grab_min_size,
        }
        if not touch:
            for name, value in self._applied.items():
                if current[name] == value:
                    self._set(name, self._base[name])
            self._applied.clear()
            return
        for name, value in current.items():
            if self._applied.get(name) != value:
                self._base[name] = value
        base = self._base
        wanted: dict[str, Any] = {
            "scrollbar_size": round(base["scrollbar_size"] * 1.6),
            "grab_min_size": round(base["grab_min_size"] * 1.5),
        }
        for name, value in wanted.items():
            if current[name] != value:
                self._set(name, value)
        self._applied = wanted

    @staticmethod
    def _set(name: str, value: Any) -> None:
        setattr(imgui.get_style(), name, value)

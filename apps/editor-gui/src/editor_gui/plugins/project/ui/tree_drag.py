"""Drag and drop in the trees (Buildings, Topology, Group Addresses), also from one tree onto
another: an element dropped where its "Move to" would put it moves there; with Ctrl or Shift held
it is copied there (``Drop.copy``, the Paste Special dialog asks how). The place under the pointer
is checked while dragging: one the element may not go to does not light up and shows the
not-allowed pointer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from imgui_bundle import imgui

# What can be dragged.
DragKind = Literal[
    "space",
    "trade",
    "function",
    "device",
    "group_address",
    "group_range",
    "line",
    "segment",
]

# imgui's payload type of a dragged tree element.
_PAYLOAD = "XKNX_TREE_ELEMENT"


@dataclass(frozen=True)
class Dragged:
    """A dragged element: its kind and ids (several selected devices are dragged together)."""

    kind: DragKind
    ids: tuple[int, ...]


@dataclass(frozen=True)
class Drop:
    """A dragged element let go over a place it may go; ``copy`` with Ctrl or Shift held."""

    dragged: Dragged
    copy: bool


# The element being dragged: the imgui payload carries only one number.
_dragged: Dragged | None = None


def drag_source(dragged: Dragged, label: str) -> None:
    """Make the item just drawn draggable as ``dragged``, ``label`` shown at the pointer."""
    global _dragged
    if imgui.begin_drag_drop_source():
        _dragged = dragged
        imgui.set_drag_drop_payload_py_id(_PAYLOAD, dragged.ids[0])
        imgui.text_unformatted(label)
        imgui.end_drag_drop_source()


def dragging() -> Dragged | None:
    """The tree element being dragged, None when there is none."""
    payload = imgui.get_drag_drop_payload_py_id()
    if payload is None or payload.type != _PAYLOAD:  # pyright: ignore[reportUnnecessaryComparison]
        return None
    return _dragged


def copy_held() -> bool:
    """Whether a key that makes a drop copy is held: Ctrl or Shift (not both)."""
    io = imgui.get_io()
    return io.key_ctrl != io.key_shift


def drop_target(
    accepts: Callable[[Dragged], bool],
    accepts_copy: Callable[[Dragged], bool] = lambda _dragged: False,
) -> Drop | None:
    """The item just drawn as a place to drop on: what is let go over it when ``accepts`` takes
    it (``accepts_copy`` while a key that makes a copy is held), else nothing (and the
    not-allowed pointer while over it)."""
    dragged = dragging()
    if dragged is None or not imgui.begin_drag_drop_target():
        return None
    drop: Drop | None = None
    if (accepts_copy if copy_held() else accepts)(dragged):
        payload = imgui.accept_drag_drop_payload_py_id(_PAYLOAD)
        if payload is not None:  # pyright: ignore[reportUnnecessaryComparison]
            drop = Drop(dragged, copy_held())
    else:
        imgui.set_mouse_cursor(imgui.MouseCursor_.not_allowed)
    imgui.end_drag_drop_target()
    return drop

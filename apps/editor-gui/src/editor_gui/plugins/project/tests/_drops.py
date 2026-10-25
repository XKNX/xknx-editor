"""Let go of a dragged tree element over one row of a tree in a headless imgui context, as a user
would drop it there (see :mod:`editor_gui.plugins.project.ui.tree_drag`)."""

from __future__ import annotations

from types import ModuleType

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui import tree_drag
from editor_gui.plugins.project.ui.tree_drag import Dragged


def drop_on(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    row: str,
    dragged: Dragged,
    *,
    copy: bool = False,
) -> list[bool]:
    """Drop ``dragged`` on the tree node of ``module`` whose label contains ``row`` (its ``##``
    id, e.g. ``"##segment_5"``), with Ctrl held when ``copy``. The returned list records, for
    every place offered the element in the frames that follow, whether it would take it."""
    last = [""]
    real_tree_node = module.tree_node
    offered: list[bool] = []

    def tree_node(label: str, *args: object) -> bool:
        last[0] = label
        return real_tree_node(label, *args)

    real_drop_target = tree_drag.drop_target

    def drop_target(accepts: object) -> tree_drag.Drop | None:
        if row not in last[0]:
            return None
        return real_drop_target(lambda d: _record(offered, accepts(d)))  # type: ignore[operator]

    monkeypatch.setattr(module, "tree_node", tree_node)
    monkeypatch.setattr(module, "drop_target", drop_target)
    monkeypatch.setattr(tree_drag, "dragging", lambda: dragged)
    monkeypatch.setattr(tree_drag, "copy_held", lambda: copy)
    monkeypatch.setattr(imgui, "begin_drag_drop_target", lambda: True)
    monkeypatch.setattr(imgui, "accept_drag_drop_payload_py_id", lambda *_a: object())
    monkeypatch.setattr(imgui, "end_drag_drop_target", lambda: None)
    return offered


def _record(offered: list[bool], taken: bool) -> bool:
    offered.append(taken)
    return taken

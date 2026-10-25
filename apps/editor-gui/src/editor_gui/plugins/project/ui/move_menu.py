"""The "Move to" submenu of the trees (Buildings, Topology, Group Addresses)."""

from __future__ import annotations

from collections.abc import Hashable, Sequence

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S


def render_move_menu[T: Hashable](
    targets: Sequence[tuple[str, T]], current: T
) -> tuple[T] | None:
    """The "Move to" submenu: each target (its menu text and value), the one the element is in
    already (``current``) grayed out. Not shown when no other target is left. The target chosen
    (in a tuple, as None may be a target), or None."""
    if all(target == current for _text, target in targets):
        return None
    if not imgui.begin_menu(S.SPACES_MOVE_TO):
        return None
    chosen: tuple[T] | None = None
    for text, target in targets:
        if imgui.menu_item(text, "", False, target != current)[0]:
            chosen = (target,)
    imgui.end_menu()
    return chosen

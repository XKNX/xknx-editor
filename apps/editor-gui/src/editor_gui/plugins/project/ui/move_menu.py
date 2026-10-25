"""The "Move to" submenu of the trees (Buildings, Topology, Group Addresses)."""

from __future__ import annotations

from collections.abc import Collection, Hashable, Sequence

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S


def render_move_menu[T: Hashable](
    targets: Sequence[tuple[str, T]],
    current: T,
    disabled: Collection[T] = (),
) -> tuple[T] | None:
    """The "Move to" submenu: each target (its menu text and value), the one the element is in
    already (``current``) and those in ``disabled`` (shown for the tree around the others)
    grayed out. Not shown when no other target is left. The target chosen (in a tuple, as None
    may be a target), or None."""
    if all(target == current or target in disabled for _text, target in targets):
        return None
    if not imgui.begin_menu(S.SPACES_MOVE_TO):
        return None
    chosen: tuple[T] | None = None
    for text, target in targets:
        enabled = target != current and target not in disabled
        if imgui.menu_item(text, "", False, enabled)[0]:
            chosen = (target,)
    imgui.end_menu()
    return chosen

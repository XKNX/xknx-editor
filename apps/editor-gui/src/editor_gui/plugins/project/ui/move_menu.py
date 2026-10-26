"""The "Move to" submenu of the trees (Buildings, Topology, Group Addresses), also the catalog's
"Add to"."""

from __future__ import annotations

from collections.abc import Callable, Collection, Hashable, Sequence

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px

# The most the submenu grows (at 100 % scaling); a longer list scrolls.
_MAX_HEIGHT = 420.0


def render_move_menu[T: Hashable](
    targets: Sequence[tuple[str, T]],
    current: T | None,
    disabled: Collection[T] = (),
    label: str = "",
    before: Callable[[], object] = lambda: None,
) -> tuple[T] | None:
    """The "Move to" submenu (or one titled ``label``): each target (its menu text and value),
    the one the element is in already (``current``) and those in ``disabled`` (shown for the
    tree around the others) grayed out. Not shown when no other target is left. The target
    chosen (in a tuple, as None may be a target), or None. A long list is cut to a height (at
    most half the screen) and scrolls. ``before`` runs right before it shows (e.g.
    :meth:`MenuGroups.show`)."""
    if all(target == current or target in disabled for _text, target in targets):
        return None
    before()
    height = min(px(_MAX_HEIGHT), imgui.get_io().display_size.y / 2)
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(0.0, 0.0), imgui.ImVec2(imgui.FLT_MAX, height)
    )
    if not imgui.begin_menu(label or S.SPACES_MOVE_TO):
        return None
    chosen: tuple[T] | None = None
    for text, target in targets:
        enabled = target != current and target not in disabled
        if imgui.menu_item(text, "", False, enabled)[0]:
            chosen = (target,)
    imgui.end_menu()
    return chosen

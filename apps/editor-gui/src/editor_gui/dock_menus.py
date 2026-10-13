"""The menu at the start of each dock section's tab bar: every tab of the section, with a checkbox
to show or hide it, so a closed tab can be reopened where it belongs."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import hello_imgui, imgui

from editor_gui.widgets.checkbox import checkbox

_POPUP = "##section_menu"


def render_section_menus(
    trailing: Callable[[imgui.internal.DockNode], None] | None = None,
) -> None:
    """Replace the window menu of every dock section that has a tab bar, and let ``trailing`` add
    buttons at its end. Call once per frame, after the dockable windows are drawn."""
    params = hello_imgui.get_runner_params().docking_params
    sections: dict[int, list[hello_imgui.DockableWindow]] = {}
    for window in params.dockable_windows:
        if not window.include_in_view_menu:
            continue
        node_id = _section_id(window, params)
        if node_id:
            sections.setdefault(node_id, []).append(window)
    for node_id, windows in sections.items():
        node = imgui.internal.dock_builder_get_node(node_id)
        if node is None or node.tab_bar is None or not node.is_leaf_node():
            continue
        host = node.host_window
        # A tab bar can only be amended while its host window is drawn this frame.
        if (
            host is None
            or not host.active
            or host.hidden
            or host.skip_items
            or node.is_hidden_tab_bar()
            or node.last_frame_active != imgui.get_frame_count()
        ):
            continue
        flag = imgui.internal.DockNodeFlagsPrivate_.no_window_menu_button
        if not node.local_flags & flag:
            node.set_local_flags(node.local_flags | flag)
        if imgui.internal.dock_node_begin_amend_tab_bar(node):
            _render_menu(node, windows)
            if trailing is not None:
                trailing(node)
            imgui.internal.dock_node_end_amend_tab_bar()


def _section_id(
    window: hello_imgui.DockableWindow, params: hello_imgui.DockingParams
) -> int | None:
    """The dock node a window is in (or was in when it was closed), else its configured one."""
    imgui_window = imgui.internal.find_window_by_name(window.label)
    if imgui_window is not None and imgui_window.dock_id:
        return imgui_window.dock_id
    return params.dock_space_id_from_name(window.dock_space_name)


def _render_menu(
    node: imgui.internal.DockNode, windows: list[hello_imgui.DockableWindow]
) -> None:
    size = imgui.get_frame_height()
    flags = imgui.TabItemFlags_.leading | imgui.TabItemFlags_.no_tooltip
    if imgui.tab_item_button(_POPUP, flags):
        imgui.open_popup(_POPUP)
    low = imgui.get_item_rect_min()
    high = imgui.get_item_rect_max()
    arrow = imgui.get_font_size() * 0.6
    imgui.internal.render_arrow_dock_menu(
        imgui.get_window_draw_list(),
        imgui.ImVec2(
            low.x + (high.x - low.x - arrow) / 2, low.y + (high.y - low.y - arrow) / 2
        ),
        arrow,
        imgui.get_color_u32(imgui.Col_.text),
    )
    if imgui.is_popup_open(_POPUP):
        imgui.set_next_window_pos(imgui.ImVec2(node.pos.x, node.pos.y + size))
    if not imgui.begin_popup(_POPUP):
        return
    tab_bar = node.tab_bar
    if tab_bar is not None and sum(1 for tab in tab_bar.tabs if tab.window) == 1:
        # One tab: the default menu's own entry (hide the tab bar).
        imgui.internal.dock_node_window_menu_handler_default(
            imgui.get_current_context(), node, tab_bar
        )
        imgui.separator()
    shown = node.visible_window.name if node.visible_window is not None else None
    for window in windows:
        name = window.label.split("###")[-1]
        imgui.begin_disabled(not window.can_be_closed)
        changed, visible = checkbox(f"##show_{name}", window.is_visible)
        imgui.end_disabled()
        if changed:
            window.is_visible = visible
        imgui.same_line()
        title = window.label.split("###")[0]
        if imgui.selectable(f"{title}##select_{name}", window.label == shown)[0]:
            window.is_visible = True
            window.focus_window_at_next_frame = True
    imgui.end_popup()

"""The menu at the start of each dock section's tab bar: every tab of the section, with a checkbox
to show or hide it, so a closed tab can be reopened where it belongs."""

from __future__ import annotations

import math

from imgui_bundle import hello_imgui, imgui

from editor_gui.strings import S
from editor_gui.widgets.checkbox import checkbox

_POPUP = "##section_menu"


def render_section_menus() -> None:
    """Replace the window menu of every dock section that has a tab bar. Call once per frame,
    after the dockable windows are drawn."""
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
        # The bindings type the dock nodes and windows as non-optional, but they can be None.
        if node is None or node.tab_bar is None or not node.is_leaf_node():  # pyright: ignore[reportUnnecessaryComparison]
            continue
        host = node.host_window
        # A tab bar can only be amended while its host window is drawn this frame.
        if (
            host is None  # pyright: ignore[reportUnnecessaryComparison]
            or not host.active
            or host.hidden
            or host.skip_items
            or node.is_hidden_tab_bar()
            or node.last_frame_active != imgui.get_frame_count()
        ):
            continue
        flag = (
            imgui.internal.DockNodeFlagsPrivate_.no_window_menu_button
            | imgui.internal.DockNodeFlagsPrivate_.no_close_button
        )
        if node.local_flags & flag != flag:
            node.set_local_flags(node.local_flags | flag)
        if imgui.internal.dock_node_begin_amend_tab_bar(node):
            _render_menu(node, windows)
            _render_close(node, windows)
            imgui.internal.dock_node_end_amend_tab_bar()


def _section_id(
    window: hello_imgui.DockableWindow, params: hello_imgui.DockingParams
) -> int | None:
    """The dock node a window is in (or was in when it was closed), else its configured one."""
    imgui_window = imgui.internal.find_window_by_name(window.label)
    if imgui_window is not None and imgui_window.dock_id:  # pyright: ignore[reportUnnecessaryComparison]
        return imgui_window.dock_id
    return params.dock_space_id_from_name(window.dock_space_name)


def _render_close(
    node: imgui.internal.DockNode, windows: list[hello_imgui.DockableWindow]
) -> None:
    """The button at the end of the tab bar that closes the tab in front, as far from the right
    edge as from the top and the bottom (imgui's own keeps the frame padding from the right)."""
    # The tabs end before it, as before imgui's own (a grayed-out tab button holds the place).
    imgui.begin_disabled()
    imgui.tab_item_button("##close_space", imgui.TabItemFlags_.trailing)
    imgui.end_disabled()
    shown = node.visible_window.name if node.visible_window is not None else None  # pyright: ignore[reportUnnecessaryComparison]
    window = next((w for w in windows if w.label == shown), None)
    if window is None or not window.can_be_closed:
        return
    # Whole pixels, so that the gaps come out the same (imgui's button is as large as the font).
    # The bar's last row is the line below the tabs.
    bar = math.floor(imgui.get_frame_height())
    visible = math.floor(bar - imgui.get_style().tab_bar_border_size)
    gap = math.ceil((visible - imgui.get_font_size()) / 2)
    side = visible - 2 * gap
    right = round(node.pos.x + node.size.x)
    top = round(node.pos.y)
    box = imgui.internal.ImRect(
        right - gap - side, top + gap, right - gap, top + gap + side
    )
    ident = imgui.get_id("##section_close")
    imgui.push_clip_rect(
        imgui.ImVec2(right - bar, top), imgui.ImVec2(right, top + bar), False
    )
    if imgui.internal.item_add(box, ident):
        pressed, hovered, held = imgui.internal.button_behavior(
            box, ident, False, False
        )
        draw = imgui.get_window_draw_list()
        if hovered:
            col = imgui.Col_.button_active if held else imgui.Col_.button_hovered
            draw.add_rect_filled(box.min, box.max, imgui.get_color_u32(col))
        # The keyboard focus frame, inside the box.
        imgui.internal.render_nav_cursor(
            box, ident, imgui.internal.NavRenderCursorFlags_.compact
        )
        # The cross of imgui's close button.
        center = imgui.ImVec2(
            (box.min.x + box.max.x) / 2 - 0.5, (box.min.y + box.max.y) / 2 - 0.5
        )
        extent = imgui.get_font_size() * 0.5 * 0.7071 - 1.0
        color = imgui.get_color_u32(imgui.Col_.text)
        for dx in (extent, -extent):
            draw.add_line(
                imgui.ImVec2(center.x + dx, center.y + extent),
                imgui.ImVec2(center.x - dx, center.y - extent),
                color,
            )
        if pressed:
            window.is_visible = False
    imgui.pop_clip_rect()


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
    if tab_bar is not None and sum(1 for tab in tab_bar.tabs if tab.window) == 1:  # pyright: ignore[reportUnnecessaryComparison]
        # One tab: its tab bar can be hidden (the small triangle left at the top shows it again).
        hidden = imgui.internal.DockNodeFlagsPrivate_.hidden_tab_bar
        if imgui.menu_item(S.DOCK_HIDE_TAB_BAR, "", node.is_hidden_tab_bar())[0]:
            node.set_local_flags(node.local_flags ^ hidden)
        imgui.separator()
    shown = node.visible_window.name if node.visible_window is not None else None  # pyright: ignore[reportUnnecessaryComparison]
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

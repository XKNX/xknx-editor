"""Group Addresses view: the named range tree (main/middle/...) down to group addresses.

Shows, for the selected group address, the device com-objects assigned to it, and lets the user
create / rename / delete group addresses and set their datapoint type (context menus + modals,
mirroring the Devices panel)."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.device import com_object_display_name
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.widgets.dpi import px, px_vec2
from editor_gui.widgets.text_util import text_clipped_tooltip

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import _Assignment
    from xknxeditor.proj.core.addressing import GroupAddressStyle
    from xknxeditor.proj.core.service import GroupRangeInfo


# Accent colour for the sending marker, matching the Group Objects table.
_SENDING_COLOR = imgui.ImVec4(0.45, 0.8, 0.5, 1.0)


class GroupAddressesPanel:
    def __init__(
        self,
        get_range_tree: "Callable[[], list[GroupRangeInfo]]",
        get_assignments_for_ga: "Callable[[int], list[_Assignment]]",
        get_devices: "Callable[[], list[Device]]",
        on_create_ga: Callable[[str, str], None],
        on_rename_ga: Callable[[int, str], None],
        on_set_ga_dpt: Callable[[int, str], None],
        on_remove_ga: Callable[[int], None],
        take_requested_ga: Callable[[], int | None] | None = None,
        on_create_range: Callable[[int | None, str], None] | None = None,
        on_rename_range: Callable[[int, str], None] | None = None,
        on_remove_range: Callable[[int], None] | None = None,
        group_style: "Callable[[], GroupAddressStyle] | None" = None,
        on_select_device_id: Callable[[int], None] | None = None,
    ) -> None:
        self._get_range_tree = get_range_tree
        self._get_assignments_for_ga = get_assignments_for_ga
        self._get_devices = get_devices
        self._on_create_ga = on_create_ga
        self._on_rename_ga = on_rename_ga
        self._on_set_ga_dpt = on_set_ga_dpt
        self._on_remove_ga = on_remove_ga
        self._on_create_range = on_create_range
        self._on_rename_range = on_rename_range
        self._on_remove_range = on_remove_range
        self._group_style = group_style
        self._on_select_device_id = on_select_device_id
        # External "select this GA" requests (e.g. clicking a Health finding).
        self._take_requested_ga = take_requested_ga
        self._filter_text: str = ""
        self._selected_ga_id: int | None = None
        # When set (from an external navigation request), the tree force-opens the ancestor folders
        # of this GA and scrolls to it for one frame, then clears it.
        self._reveal_ga_id: int | None = None
        self._selected_ga_text: str = ""
        self._selected_ga_description: str = ""
        self._selected_ga_comment: str = ""
        # Modal state.
        self._popup_ga_id: int = 0
        self._popup_address: str = ""
        self._popup_name: str = ""
        self._popup_dpt: str = ""
        self._popup_dpt_error: str = ""
        self._open_new_ga = False
        self._open_rename_ga = False
        self._open_set_dpt = False
        # Group-range (folder) modal state.
        self._popup_range_id: int = 0
        self._popup_range_parent: int | None = None
        self._popup_range_name: str = ""
        self._open_new_range = False
        self._open_rename_range = False
        self._open_delete_range = False

    def render(self) -> None:
        tree = self._get_range_tree()

        # Adopt an externally requested selection (Health navigation) once, before drawing.
        if self._take_requested_ga is not None:
            requested = self._take_requested_ga()
            if requested is not None:
                self._selected_ga_id = requested
                self._reveal_ga_id = requested

        if imgui.begin_popup_context_window("##ga_context"):
            self._render_create_menu_items()
            imgui.end_popup()

        # Visible toolbar (the right-click menu above is easy to miss). Shown before the modal
        # dispatch so a click is picked up the same frame, and above the empty-tree early return so
        # the first folder/address can be created in a fresh project.
        if imgui.small_button(S.GA_NEW):
            self._popup_address = ""
            self._popup_name = ""
            self._open_new_ga = True
        if self._folders_enabled():
            imgui.same_line()
            if imgui.small_button(S.GA_NEW_MAIN):
                self._popup_range_parent = None
                self._popup_range_name = ""
                self._open_new_range = True

        if self._open_new_ga:
            imgui.open_popup(S.GA_NEW)
            self._open_new_ga = False
        if self._open_rename_ga:
            imgui.open_popup(S.GA_RENAME)
            self._open_rename_ga = False
        if self._open_set_dpt:
            imgui.open_popup(S.GA_SET_DPT)
            self._open_set_dpt = False
        if self._open_new_range:
            imgui.open_popup(S.GA_FOLDER_NEW)
            self._open_new_range = False
        if self._open_rename_range:
            imgui.open_popup(S.GA_FOLDER_RENAME)
            self._open_rename_range = False
        if self._open_delete_range:
            imgui.open_popup(S.GA_FOLDER_DELETE)
            self._open_delete_range = False
        self._render_new_ga_popup()
        self._render_rename_popup()
        self._render_dpt_popup()
        self._render_new_range_popup()
        self._render_rename_range_popup()
        self._render_delete_range_popup()

        if not tree:
            imgui.text_disabled(S.GA_NO_PROJECT)
            return

        self._filter_text = filter_box(
            "##ga_filter", S.GA_FILTER_HINT, self._filter_text
        )
        flt = self._filter_text.strip().lower()

        avail = imgui.get_content_region_avail()
        tree_height = max(avail.y * 0.6, 0.0)
        if imgui.begin_child("##ga_tree", imgui.ImVec2(0.0, tree_height)):
            # The context menu must live in the same window as the tree — begin_child is its own
            # window, so a right-click here does NOT reach the panel-level menu above (that one only
            # fires in the area below the tree). Register it inside the child too, like the Devices
            # panel does (which has no child, so its menu just works everywhere).
            if imgui.begin_popup_context_window("##ga_tree_ctx"):
                self._render_create_menu_items()
                imgui.end_popup()
            for node in tree:
                self._render_range(node, flt, is_root=True)
        imgui.end_child()
        # Reveal is a one-frame action: ancestors were force-opened and the GA scrolled into view.
        self._reveal_ga_id = None

        imgui.separator()
        self._render_assignments()

    def _render_create_menu_items(self) -> None:
        """The 'new group address' / 'new main group' items shared by the panel's context menus."""
        if imgui.menu_item(S.GA_NEW, "", False)[0]:
            self._popup_address = ""
            self._popup_name = ""
            self._open_new_ga = True
        if self._folders_enabled() and imgui.menu_item(S.GA_NEW_MAIN, "", False)[0]:
            self._popup_range_parent = None
            self._popup_range_name = ""
            self._open_new_range = True

    def _render_range(
        self, node: "GroupRangeInfo", flt: str = "", is_root: bool = False
    ) -> None:
        if flt and not self._range_has_match(node, flt):
            return  # while filtering, hide ranges with no matching group address
        label = (
            f"{node.name}##gr{node.id}" if node.name else f"[{node.id}]##gr{node.id}"
        )
        if flt:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        elif self._reveal_ga_id is not None and self._range_contains_ga(
            node, self._reveal_ga_id
        ):
            # Force-open the folders on the path to a GA we were asked to reveal.
            imgui.set_next_item_open(True, imgui.Cond_.always)
        # Collapsed by default; a filter still force-opens matching ranges (above).
        open_node = imgui.tree_node_ex(label)
        self._render_range_context_menu(node, is_root)
        if not open_node:
            return
        for child in node.children:
            self._render_range(child, flt)
        for ga in node.group_addresses:
            if flt and not self._ga_matches(ga, flt):
                continue
            selected = ga.id == self._selected_ga_id
            ga_label = f"{ga.text}  {ga.name}##ga{ga.id}"
            if imgui.selectable(ga_label, selected)[0]:
                self._selected_ga_id = ga.id
                self._selected_ga_text = f"{ga.text}  {ga.name}"
                self._selected_ga_description = ga.description
                self._selected_ga_comment = ga.comment
            if ga.id == self._reveal_ga_id:
                imgui.set_scroll_here_y()
            self._render_ga_context_menu(ga)
        imgui.tree_pop()

    @staticmethod
    def _ga_matches(ga: object, flt: str) -> bool:
        text = getattr(ga, "text", "") or ""
        name = getattr(ga, "name", "") or ""
        return flt in text.lower() or flt in name.lower()

    def _range_has_match(self, node: "GroupRangeInfo", flt: str) -> bool:
        if any(self._ga_matches(ga, flt) for ga in node.group_addresses):
            return True
        return any(self._range_has_match(child, flt) for child in node.children)

    def _range_contains_ga(self, node: "GroupRangeInfo", ga_id: int) -> bool:
        if any(ga.id == ga_id for ga in node.group_addresses):
            return True
        return any(self._range_contains_ga(child, ga_id) for child in node.children)

    def _render_ga_context_menu(self, ga: object) -> None:
        # ga is a core GroupAddressInfo (id, text, name, datapoint_type, …).
        if not imgui.begin_popup_context_item(f"##ga_ctx_{ga.id}"):  # type: ignore[attr-defined]
            return
        if imgui.menu_item(S.CONTEXT_RENAME, "", False)[0]:
            self._popup_ga_id = ga.id  # type: ignore[attr-defined]
            self._popup_name = ga.name  # type: ignore[attr-defined]
            self._open_rename_ga = True
        if imgui.menu_item(S.GA_SET_DPT, "", False)[0]:
            self._popup_ga_id = ga.id  # type: ignore[attr-defined]
            self._popup_dpt = ga.datapoint_type or ""  # type: ignore[attr-defined]
            self._popup_dpt_error = ""
            self._open_set_dpt = True
        if imgui.menu_item(S.CONTEXT_COPY_ADDRESS, "", False)[0]:
            imgui.set_clipboard_text(getattr(ga, "text", "") or "")
        imgui.separator()
        if imgui.menu_item(S.CONTEXT_DELETE, "", False)[0]:
            self._on_remove_ga(ga.id)  # type: ignore[attr-defined]
            if self._selected_ga_id == ga.id:  # type: ignore[attr-defined]
                self._selected_ga_id = None
        imgui.end_popup()

    def _folders_enabled(self) -> bool:
        """Folders (group ranges) only exist in the level-based styles, not Free."""
        from xknxeditor.proj.core.addressing import GroupAddressStyle

        return (
            self._on_create_range is not None
            and self._group_style is not None
            and self._group_style() is not GroupAddressStyle.FREE
        )

    def _middle_enabled(self) -> bool:
        """Middle groups (a folder under a main group) exist only in ThreeLevel."""
        from xknxeditor.proj.core.addressing import GroupAddressStyle

        return (
            self._on_create_range is not None
            and self._group_style is not None
            and self._group_style() is GroupAddressStyle.THREE_LEVEL
        )

    def _render_range_context_menu(self, node: "GroupRangeInfo", is_root: bool) -> None:
        if self._on_rename_range is None:  # folder editing disabled
            return
        if not imgui.begin_popup_context_item(f"##gr_ctx_{node.id}"):
            return
        # A middle group can only be added under a top-level (main) group, ThreeLevel only.
        if (
            is_root
            and self._middle_enabled()
            and imgui.menu_item(S.GA_NEW_MIDDLE, "", False)[0]
        ):
            self._popup_range_parent = node.id
            self._popup_range_name = ""
            self._open_new_range = True
        if imgui.menu_item(S.CONTEXT_RENAME, "", False)[0]:
            self._popup_range_id = node.id
            self._popup_range_name = node.name
            self._open_rename_range = True
        if self._on_remove_range is not None:
            imgui.separator()
            if imgui.menu_item(S.CONTEXT_DELETE, "", False)[0]:
                self._popup_range_id = node.id
                self._open_delete_range = True
        imgui.end_popup()

    def _render_new_range_popup(self) -> None:
        if self._on_create_range is None:
            return
        if not imgui.begin_popup_modal(
            S.GA_FOLDER_NEW, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text_disabled(
            S.GA_FOLDER_MAIN_HINT
            if self._popup_range_parent is None
            else S.GA_FOLDER_MIDDLE_HINT
        )
        imgui.set_next_item_width(px(220.0))
        _, self._popup_range_name = imgui.input_text(
            "##gr_new_name", self._popup_range_name
        )
        btn_w = px_vec2(120, 0)
        if imgui.button(S.BTN_OK, btn_w):
            self._on_create_range(
                self._popup_range_parent, self._popup_range_name.strip()
            )
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_rename_range_popup(self) -> None:
        if self._on_rename_range is None:
            return
        if not imgui.begin_popup_modal(
            S.GA_FOLDER_RENAME, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.set_next_item_width(px(220.0))
        _, self._popup_range_name = imgui.input_text(
            "##gr_rename", self._popup_range_name
        )
        btn_w = px_vec2(120, 0)
        if imgui.button(S.BTN_OK, btn_w):
            self._on_rename_range(self._popup_range_id, self._popup_range_name.strip())
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_delete_range_popup(self) -> None:
        if self._on_remove_range is None:
            return
        if not imgui.begin_popup_modal(
            S.GA_FOLDER_DELETE, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text_wrapped(S.GA_FOLDER_DELETE_CONFIRM)
        btn_w = px_vec2(120, 0)
        if imgui.button(S.CONTEXT_DELETE, btn_w):
            self._on_remove_range(self._popup_range_id)
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_new_ga_popup(self) -> None:
        if not imgui.begin_popup_modal(
            S.GA_NEW, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text_disabled(S.GA_ADDRESS)
        imgui.set_next_item_width(px(220.0))
        _, self._popup_address = imgui.input_text_with_hint(
            "##ga_addr", "1/2/3", self._popup_address
        )
        imgui.text_disabled(S.POPUP_NAME)
        imgui.set_next_item_width(px(220.0))
        _, self._popup_name = imgui.input_text("##ga_new_name", self._popup_name)
        btn_w = px_vec2(120, 0)
        if imgui.button(S.BTN_OK, btn_w) and self._popup_address.strip():
            self._on_create_ga(self._popup_address.strip(), self._popup_name)
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_rename_popup(self) -> None:
        if not imgui.begin_popup_modal(
            S.GA_RENAME, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.set_next_item_width(px(220.0))
        _, self._popup_name = imgui.input_text("##ga_rename", self._popup_name)
        btn_w = px_vec2(120, 0)
        if imgui.button(S.BTN_OK, btn_w):
            self._on_rename_ga(self._popup_ga_id, self._popup_name)
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_dpt_popup(self) -> None:
        if not imgui.begin_popup_modal(
            S.GA_SET_DPT, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text_disabled(S.GA_DPT_HINT)
        imgui.set_next_item_width(px(220.0))
        _, self._popup_dpt = imgui.input_text_with_hint(
            "##ga_dpt", "DPST-1-1", self._popup_dpt
        )
        if self._popup_dpt_error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.3, 0.3, 1.0), self._popup_dpt_error)
        btn_w = px_vec2(120, 0)
        if imgui.button(S.BTN_OK, btn_w):
            try:
                self._on_set_ga_dpt(self._popup_ga_id, self._popup_dpt.strip())
            except ValueError as e:
                self._popup_dpt_error = str(e)
            else:
                self._popup_dpt_error = ""
                imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            self._popup_dpt_error = ""
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_assignments(self) -> None:
        if self._selected_ga_id is None:
            imgui.text_disabled(S.GA_ASSIGNED_OBJECTS)
            return
        imgui.text_disabled(self._selected_ga_text)
        if self._selected_ga_description:
            imgui.text_wrapped(f"{S.GA_DESCRIPTION}: {self._selected_ga_description}")
        if self._selected_ga_comment:
            imgui.text_wrapped(f"{S.GA_COMMENT}: {self._selected_ga_comment}")

        names = self._com_object_names()
        unknown = (len(names), 0)
        assignments = sorted(
            self._get_assignments_for_ga(self._selected_ga_id),
            key=lambda a: (
                names[a.com_object_id][3] if a.com_object_id in names else unknown
            ),
        )
        flags = imgui.TableFlags_.borders_inner | imgui.TableFlags_.sizing_stretch_prop
        if not imgui.begin_table("##ga_assignments", 3, flags):
            return
        imgui.table_setup_column(
            S.GA_COL_DEVICE, imgui.TableColumnFlags_.width_stretch, 0.5
        )
        imgui.table_setup_column(
            S.GA_COL_OBJECT, imgui.TableColumnFlags_.width_stretch, 0.4
        )
        imgui.table_setup_column(
            S.GA_COL_SENDING, imgui.TableColumnFlags_.width_fixed, px(20.0)
        )
        # Manual header row so the terse "S" column can carry an explanatory tooltip.
        imgui.table_next_row(imgui.TableRowFlags_.headers)
        for col in range(3):
            imgui.table_set_column_index(col)
            imgui.table_header(imgui.table_get_column_name(col))
            if col == 2 and imgui.is_item_hovered():
                imgui.set_tooltip(S.GA_COL_SENDING_TOOLTIP)
        for a in assignments:
            entry = names.get(a.com_object_id)
            if entry is None:
                device_label, co_name, node_id = "?", "?", None
            else:
                device_label, co_name, node_id, _order = entry
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            # Clicking a row opens the owning device in the editor (Configure panel).
            if node_id is not None and self._on_select_device_id is not None:
                if imgui.selectable(
                    f"{device_label}##ga_asg{a.com_object_id}",
                    False,
                    imgui.SelectableFlags_.span_all_columns,
                )[0]:
                    self._on_select_device_id(node_id)
                if imgui.is_item_hovered():
                    imgui.set_tooltip(S.GA_OBJECT_OPEN_TOOLTIP)
            else:
                text_clipped_tooltip(device_label)
            imgui.table_set_column_index(1)
            text_clipped_tooltip(co_name, disabled=True)
            imgui.table_set_column_index(2)
            if a.is_sending:
                # Font-safe accent marker (DroidSans has no U+2192 arrow glyph).
                imgui.text_colored(_SENDING_COLOR, "»")
                if imgui.is_item_hovered():
                    imgui.set_tooltip(S.GA_COL_SENDING_TOOLTIP)
        imgui.end_table()

    def _com_object_names(self) -> dict[int, tuple[str, str, int, tuple[int, int]]]:
        """Per com-object db id: device label, object name, device node id and the row's sort
        key (device order, object number)."""
        names: dict[int, tuple[str, str, int, tuple[int, int]]] = {}
        for index, device in enumerate(self._get_devices()):
            # Imported devices are often unnamed; fall back to the product name (as the
            # Devices tree does), and prefix the individual address for context.
            primary = device.display_name or "?"
            ia = device.individual_address
            label = f"{ia}  {primary}" if ia else primary
            for co in device.com_objects:
                if co.db_id is not None:
                    names[co.db_id] = (
                        label,
                        com_object_display_name(co),
                        device.node_id,
                        (index, co.number),
                    )
        return names

"""Group Addresses view: the named range tree (main/middle/...) down to group addresses.

Shows, for the selected group address, the device com-objects assigned to it, and lets the user
create / rename / delete group addresses and set their datapoint type (context menus + modals,
mirroring the Devices panel)."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui
from imgui_bundle import portable_file_dialogs as pfd

from editor_gui.device import com_object_display_name
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.group_value import GroupValueActions
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.dpt_select import dpt_combo
from editor_gui.widgets.dpt_value import GroupValueBus
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
        on_set_ga_address: Callable[[int, str], bool] | None = None,
        on_set_ga_security: Callable[[int, str], bool] | None = None,
        take_requested_ga: Callable[[], int | None] | None = None,
        on_create_range: Callable[[int | None, str], None] | None = None,
        on_rename_range: Callable[[int, str], None] | None = None,
        on_remove_range: Callable[[int], None] | None = None,
        group_style: "Callable[[], GroupAddressStyle] | None" = None,
        on_select_device_id: Callable[[int], None] | None = None,
        bus: GroupValueBus | None = None,
        on_import: Callable[[Path], tuple[int, int, int]] | None = None,
        on_export: Callable[[], None] | None = None,
    ) -> None:
        self._get_range_tree = get_range_tree
        self._get_assignments_for_ga = get_assignments_for_ga
        self._get_devices = get_devices
        self._on_create_ga = on_create_ga
        self._on_rename_ga = on_rename_ga
        self._on_set_ga_dpt = on_set_ga_dpt
        self._on_set_ga_security = on_set_ga_security
        self._on_remove_ga = on_remove_ga
        self._on_set_ga_address = on_set_ga_address
        self._on_create_range = on_create_range
        self._on_rename_range = on_rename_range
        self._on_remove_range = on_remove_range
        self._group_style = group_style
        self._on_select_device_id = on_select_device_id
        self._bus = bus
        self._on_import = on_import
        self._on_export = on_export
        self._import_dialog: pfd.open_file | None = None
        self._import_message = ""
        self._open_import_result = False
        # External "select this GA" requests (e.g. clicking a Health finding).
        self._take_requested_ga = take_requested_ga
        self._filter_text: str = ""
        self._selected_ga_id: int | None = None
        # Com-object name/label map for the assignments table, cached by the identity of the
        # revision-cached device list (a fresh list object is handed out on the next edit), so it is
        # not rebuilt over every device + com-object each frame while a GA is selected.
        self._com_names_key: int | None = None
        self._com_names_cache: dict[int, tuple[str, str, int, tuple[int, int]]] = {}
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
        self._open_renumber_ga = False
        self._renumber_error = ""
        self._open_set_dpt = False
        self._group_value = (
            GroupValueActions(bus, "ga_write_value") if bus is not None else None
        )
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
        if self._on_export is not None:
            imgui.same_line()
            if imgui.small_button(S.GA_EXPORT_BUTTON):
                self._on_export()
        if self._on_import is not None:
            imgui.same_line()
            if imgui.small_button(S.GA_IMPORT_BUTTON):
                self.start_import()

        if self._open_new_ga:
            imgui.open_popup(S.GA_NEW)
            self._open_new_ga = False
        if self._open_rename_ga:
            imgui.open_popup(S.GA_RENAME)
            self._open_rename_ga = False
        if self._open_renumber_ga:
            imgui.open_popup(S.GA_RENUMBER)
            self._open_renumber_ga = False
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
        self._render_renumber_popup()
        self._render_dpt_popup()
        if self._group_value is not None:
            self._group_value.render()
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
        if self._on_export is not None or self._on_import is not None:
            imgui.separator()
        if (
            self._on_export is not None
            and imgui.menu_item(S.GA_EXPORT_MENU, "", False)[0]
        ):
            self._on_export()
        if (
            self._on_import is not None
            and imgui.menu_item(S.GA_IMPORT_MENU, "", False)[0]
        ):
            self.start_import()

    def start_import(self) -> None:
        """Ask for a group address file and import it into the open project."""
        if self._import_dialog is None:
            self._import_dialog = pfd.open_file(
                S.GA_IMPORT_TITLE,
                "",
                [S.GA_IMPORT_FILTER, "*.xml *.csv", S.GA_IMPORT_ALL_FILES, "*"],
            )

    def render_import(self) -> None:
        """The import's file dialog and result; drawn every frame, also while the panel is hidden."""
        self._poll_import()
        if self._open_import_result:
            imgui.open_popup(S.GA_IMPORT_TITLE)
            self._open_import_result = False
        self._render_import_result()

    def _poll_import(self) -> None:
        dialog = self._import_dialog
        if dialog is None or not dialog.ready() or self._on_import is None:
            return
        self._import_dialog = None
        paths = dialog.result()
        if not paths:
            return
        from xknxeditor.proj.core.ga_export import GroupAddressStyleMismatch

        try:
            added, updated, skipped = self._on_import(Path(paths[0]))
        except GroupAddressStyleMismatch as exc:
            self._import_message = S.GA_IMPORT_STYLE_MISMATCH.format(
                file=_style_text(exc.file_style),
                project=_style_text(exc.project_style),
            )
        except ValueError as exc:
            self._import_message = S.GA_IMPORT_FAILED.format(error=exc)
        else:
            self._import_message = S.GA_IMPORT_DONE.format(added=added, updated=updated)
            if skipped:
                self._import_message += "\n" + S.GA_IMPORT_SKIPPED.format(count=skipped)
        self._open_import_result = True

    def _render_import_result(self) -> None:
        if not begin_dialog(S.GA_IMPORT_TITLE, width=420.0):
            return
        imgui.text_wrapped(self._import_message)
        if dialog_buttons(S.BTN_OK) == 0:
            imgui.close_current_popup()
        end_dialog()

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
        if (
            self._on_set_ga_address is not None
            and imgui.menu_item(S.GA_RENUMBER, "", False)[0]
        ):
            self._popup_ga_id = ga.id  # type: ignore[attr-defined]
            self._popup_address = getattr(ga, "text", "") or ""
            self._renumber_error = ""
            self._open_renumber_ga = True
        if imgui.menu_item(S.GA_SET_DPT, "", False)[0]:
            self._popup_ga_id = ga.id  # type: ignore[attr-defined]
            self._popup_dpt = ga.datapoint_type or ""  # type: ignore[attr-defined]
            self._popup_dpt_error = ""
            self._open_set_dpt = True
        if self._on_set_ga_security is not None and imgui.begin_menu(S.GA_SECURITY):
            current = getattr(ga, "security_mode", None) or "Auto"  # type: ignore[attr-defined]
            for mode, label in (
                ("Auto", S.GA_SECURITY_AUTO),
                ("On", S.GA_SECURITY_ON),
                ("Off", S.GA_SECURITY_OFF),
            ):
                if imgui.menu_item(label, "", current == mode)[0]:
                    self._on_set_ga_security(ga.id, mode)  # type: ignore[attr-defined]
            imgui.end_menu()
        if imgui.menu_item(S.CONTEXT_COPY_ADDRESS, "", False)[0]:
            imgui.set_clipboard_text(getattr(ga, "text", "") or "")
        if self._group_value is not None:
            imgui.separator()
            self._group_value.render_menu_items(
                getattr(ga, "text", "") or "",
                getattr(ga, "name", "") or "",
                getattr(ga, "datapoint_type", None),
            )
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

    def _next_free_text(self, node: "GroupRangeInfo") -> str:
        """The lowest unused address in ``node``'s window, formatted; empty when it is full."""
        from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga

        used = {ga.address for ga in node.group_addresses}
        style = (
            self._group_style()
            if self._group_style is not None
            else GroupAddressStyle.THREE_LEVEL
        )
        for address in range(max(1, node.range_start), node.range_end + 1):
            if address not in used:
                return format_ga(address, style)
        return ""

    def _render_range_context_menu(self, node: "GroupRangeInfo", is_root: bool) -> None:
        if self._on_rename_range is None:  # folder editing disabled
            return
        if not imgui.begin_popup_context_item(f"##gr_ctx_{node.id}"):
            return
        # Group addresses live in middle groups (ThreeLevel) or main groups (TwoLevel).
        if (
            not (is_root and self._middle_enabled())
            and imgui.menu_item(S.GA_NEW, "", False)[0]
        ):
            self._popup_address = self._next_free_text(node)
            self._popup_name = ""
            self._open_new_ga = True
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
        if not begin_dialog(S.GA_FOLDER_NEW):
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
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        if clicked == 0:
            self._on_create_range(
                self._popup_range_parent, self._popup_range_name.strip()
            )
            imgui.close_current_popup()
        if clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_rename_range_popup(self) -> None:
        if self._on_rename_range is None:
            return
        if not begin_dialog(S.GA_FOLDER_RENAME):
            return
        imgui.set_next_item_width(px(220.0))
        _, self._popup_range_name = imgui.input_text(
            "##gr_rename", self._popup_range_name
        )
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        if clicked == 0:
            self._on_rename_range(self._popup_range_id, self._popup_range_name.strip())
            imgui.close_current_popup()
        if clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_delete_range_popup(self) -> None:
        if self._on_remove_range is None:
            return
        if not begin_dialog(S.GA_FOLDER_DELETE):
            return
        imgui.text_wrapped(S.GA_FOLDER_DELETE_CONFIRM)
        clicked = dialog_buttons(S.CONTEXT_DELETE, S.BTN_CANCEL)
        if clicked == 0:
            self._on_remove_range(self._popup_range_id)
            imgui.close_current_popup()
        if clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_new_ga_popup(self) -> None:
        if not begin_dialog(S.GA_NEW):
            return
        imgui.text_disabled(S.GA_ADDRESS)
        imgui.set_next_item_width(px(220.0))
        _, self._popup_address = imgui.input_text_with_hint(
            "##ga_addr", "1/2/3", self._popup_address
        )
        imgui.text_disabled(S.POPUP_NAME)
        imgui.set_next_item_width(px(220.0))
        _, self._popup_name = imgui.input_text("##ga_new_name", self._popup_name)
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        if clicked == 0 and self._popup_address.strip():
            self._on_create_ga(self._popup_address.strip(), self._popup_name)
            imgui.close_current_popup()
        if clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_rename_popup(self) -> None:
        if not begin_dialog(S.GA_RENAME):
            return
        imgui.set_next_item_width(px(220.0))
        _, self._popup_name = imgui.input_text("##ga_rename", self._popup_name)
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        if clicked == 0:
            self._on_rename_ga(self._popup_ga_id, self._popup_name)
            imgui.close_current_popup()
        if clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_renumber_popup(self) -> None:
        if not begin_dialog(S.GA_RENUMBER):
            return
        imgui.text(S.GA_RENUMBER_PROMPT)
        imgui.set_next_item_width(px(220.0))
        submitted, self._popup_address = imgui.input_text(
            "##ga_renumber",
            self._popup_address,
            imgui.InputTextFlags_.enter_returns_true,
        )
        if self._renumber_error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.4, 0.4, 1.0), self._renumber_error)
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        if (clicked == 0 or submitted) and self._on_set_ga_address is not None:
            if self._on_set_ga_address(self._popup_ga_id, self._popup_address.strip()):
                self._renumber_error = ""
                # Refresh the cached selection heading so it shows the new address, not the old one,
                # until the next (re)selection re-derives it from the rebuilt tree.
                if self._selected_ga_id == self._popup_ga_id:
                    name = (
                        self._selected_ga_text.split("  ", 1)[1]
                        if "  " in self._selected_ga_text
                        else ""
                    )
                    self._selected_ga_text = f"{self._popup_address.strip()}  {name}"
                imgui.close_current_popup()
            else:
                self._renumber_error = S.GA_RENUMBER_FAILED
        if clicked == 1:
            self._renumber_error = ""
            imgui.close_current_popup()
        end_dialog()

    def _render_dpt_popup(self) -> None:
        names: Mapping[str, str] = self._bus.names() if self._bus is not None else {}
        if not begin_dialog(S.GA_SET_DPT, width=420.0 if names else None):
            return
        if names:
            imgui.text_disabled(S.GA_WRITE_DPT)
            imgui.set_next_item_width(-1)
            _, self._popup_dpt = dpt_combo(
                "ga_dpt", self._popup_dpt, names, S.ML_FILTER_HINT
            )
        else:
            # Without master data there is nothing to choose from; enter the token.
            imgui.text_disabled(S.GA_DPT_HINT)
            imgui.set_next_item_width(px(220.0))
            _, self._popup_dpt = imgui.input_text_with_hint(
                "##ga_dpt", "DPST-1-1", self._popup_dpt
            )
        if self._popup_dpt_error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.3, 0.3, 1.0), self._popup_dpt_error)
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        if clicked == 0:
            try:
                self._on_set_ga_dpt(self._popup_ga_id, self._popup_dpt.strip())
            except ValueError as e:
                self._popup_dpt_error = str(e)
            else:
                self._popup_dpt_error = ""
                imgui.close_current_popup()
        if clicked == 1:
            self._popup_dpt_error = ""
            imgui.close_current_popup()
        end_dialog()

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
        key (device order, object number). Cached by device-list identity (revision-stable)."""
        devices = self._get_devices()
        if self._com_names_key == id(devices):
            return self._com_names_cache
        names: dict[int, tuple[str, str, int, tuple[int, int]]] = {}
        for index, device in enumerate(devices):
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
        self._com_names_cache = names
        self._com_names_key = id(devices)
        return names


def _style_text(style: "GroupAddressStyle") -> str:
    from xknxeditor.proj.core.addressing import GroupAddressStyle

    return {
        GroupAddressStyle.THREE_LEVEL: S.GA_STYLE_THREE_LEVEL,
        GroupAddressStyle.TWO_LEVEL: S.GA_STYLE_TWO_LEVEL,
        GroupAddressStyle.FREE: S.GA_STYLE_FREE,
    }[style]

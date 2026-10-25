"""Group Addresses view: the named range tree (main/middle/...) down to group addresses.

Shows, for the selected group address, the device com-objects assigned to it, and lets the user
create / rename / delete group addresses and groups (context menus + modals, mirroring the
Topology panel); a secured group address, and a group whose Security is on, shows the KNX Data
Secure shield. Address, data type and security are changed in the details."""

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.color import sending_green, themed
from editor_gui.device import com_object_display_name
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.group_value import GroupValueActions
from editor_gui.plugins.project.ui.move_menu import render_move_menu
from editor_gui.plugins.project.ui.tree_drag import Dragged, drag_source, drop_target
from editor_gui.widgets.description import secure_icon, secure_icon_width
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.dpt_value import GroupValueBus
from editor_gui.widgets.tables import begin_data_table, table_header_menu
from editor_gui.widgets.text_util import text_clipped_tooltip
from editor_gui.widgets.tree import (
    begin_tree_area,
    context_menu,
    leaf_rows,
    tree_node,
)
from xknxeditor.proj.core.rich_text import parse_rich_text, plain_text

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import Assignment
    from xknxeditor.proj.core.addressing import GroupAddressStyle
    from xknxeditor.proj.core.service import GroupRangeInfo


# Accent colour for the last value a group address was seen carrying on the bus, as (on dark
# themes, on light themes).
_LIVE_COLOR = (imgui.ImVec4(0.45, 0.7, 0.9, 1.0), imgui.ImVec4(0.1, 0.38, 0.7, 1.0))


def _range_text(node: "GroupRangeInfo") -> str:
    """A group as a "Move to" target: its number and name ("1/2  Lights")."""
    base = node.name or f"[{node.id}]"
    shown = f"{node.address}  {base}" if node.address else base
    return f"{shown}##mvgr{node.id}"


class GroupAddressesPanel:
    def __init__(
        self,
        get_range_tree: "Callable[[], list[GroupRangeInfo]]",
        get_assignments_for_ga: "Callable[[int], list[Assignment]]",
        get_devices: "Callable[[], list[Device]]",
        on_create_ga: Callable[[str, str], None],
        on_rename_ga: Callable[[int, str], None],
        on_remove_ga: Callable[[int], None],
        take_requested_ga: Callable[[], int | None] | None = None,
        on_create_range: Callable[[int | None, str], object] | None = None,
        on_rename_range: Callable[[int, str], None] | None = None,
        on_remove_range: Callable[[int], None] | None = None,
        group_style: "Callable[[], GroupAddressStyle] | None" = None,
        on_select_device_id: Callable[[int], None] | None = None,
        on_select_element: Callable[[str, int], None] = lambda _k, _i: None,
        bus: GroupValueBus | None = None,
        on_import: Callable[[Path], tuple[int, int, int]] | None = None,
        on_export: Callable[[], None] | None = None,
        get_latest_value: Callable[[str, str | None], tuple[str, str, float] | None]
        | None = None,
        on_move_ga: Callable[[int, int], str | None] | None = None,
        on_move_range: Callable[[int, int], str | None] | None = None,
        notify: Callable[[str], None] = lambda _text: None,
        one_undo_step: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> None:
        """``on_move_ga`` puts a group address into another group, ``on_move_range`` a middle
        group below another main group ("Move to"); both give the reason it cannot be, which
        ``notify`` shows. ``one_undo_step`` makes the edits of one drop one undo step."""
        self._one_undo_step = one_undo_step
        self._on_move_ga = on_move_ga
        self._on_move_range = on_move_range
        self._notify = notify
        self._get_range_tree = get_range_tree
        # Selecting a group range or address (its comment shows in the Comment tab).
        self._on_select_element = on_select_element
        # The group range selected in this tree: highlighted instead of the group address until a
        # group address is selected again.
        self._selected_range_id: int | None = None
        self._get_assignments_for_ga = get_assignments_for_ga
        self._get_devices = get_devices
        self._on_create_ga = on_create_ga
        self._on_rename_ga = on_rename_ga
        self._on_remove_ga = on_remove_ga
        self._on_create_range = on_create_range
        self._on_rename_range = on_rename_range
        self._on_remove_range = on_remove_range
        self._group_style = group_style
        self._on_select_device_id = on_select_device_id
        self._bus = bus
        self._on_import = on_import
        self._on_export = on_export
        self._get_latest_value = get_latest_value
        self._import_dialog: file_dialogs.OpenFileDialog | None = None
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
        self._open_new_ga = False
        self._open_rename_ga = False
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
                self._selected_range_id = None
                self._reveal_ga_id = requested
                self._on_select_element("group_address", requested)

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
        if begin_tree_area("##ga_tree", tree_height):
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
            self._import_dialog = file_dialogs.open_file(
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
        # Show the folder's main/middle-group number before its name (like the leaf GA rows),
        # e.g. "0  Safety" and "0/1  Alarm". Free style has no number, so this stays the name only.
        base = node.name or f"[{node.id}]"
        number = getattr(node, "address", "")
        shown = f"{number}  {base}" if number else base
        label = f"{shown}##gr{node.id}"
        if flt:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        elif self._reveal_ga_id is not None and self._range_contains_ga(
            node, self._reveal_ga_id
        ):
            # Force-open the folders on the path to a GA we were asked to reveal.
            imgui.set_next_item_open(True, imgui.Cond_.always)
        # Collapsed by default; a filter still force-opens matching ranges (above). A click selects
        # the range, the arrow and a double click open it.
        flags = (
            imgui.TreeNodeFlags_.open_on_arrow
            | imgui.TreeNodeFlags_.open_on_double_click
        )
        if node.id == self._selected_range_id:
            flags |= imgui.TreeNodeFlags_.selected
        open_node = tree_node(label, flags)
        if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
            self._selected_range_id = node.id
            self._on_select_element("group_range", node.id)
        self._render_range_context_menu(node, is_root)
        self._drag_and_drop_range(node, is_root, shown)
        if _forced_on(node):
            # The KNX Data Secure shield after a group whose Security is on.
            secure_icon(
                x=imgui.get_item_rect_min().x
                + imgui.get_tree_node_to_label_spacing()
                + imgui.calc_text_size(shown).x
                + imgui.get_style().item_spacing.x
            )
        if not open_node:
            return
        for child in node.children:
            self._render_range(child, flt)
        with leaf_rows():
            self._render_group_addresses(node, flt)
        imgui.tree_pop()

    def _drag_and_drop_range(
        self, node: "GroupRangeInfo", is_root: bool, shown: str
    ) -> None:
        """Drag and drop as "Move to" does: a middle group onto another main group, a group
        address onto another group that holds group addresses."""
        middle = self._middle_enabled()
        if self._on_move_range is not None and middle and not is_root:
            drag_source(Dragged("group_range", (node.id,)), shown)
        holds_addresses = self._folders_enabled() and (
            not is_root if middle else is_root
        )

        def accepts(dragged: Dragged) -> bool:
            inside = {ga.id for ga in node.group_addresses} | {
                c.id for c in node.children
            }
            if inside.issuperset(dragged.ids):
                return False
            if dragged.kind == "group_address":
                return holds_addresses and self._on_move_ga is not None
            return dragged.kind == "group_range" and middle and is_root

        drop = drop_target(accepts)
        if drop is None:
            return
        with self._one_undo_step():
            for ident in drop.dragged.ids:
                if drop.dragged.kind == "group_address":
                    assert self._on_move_ga is not None
                    self._report(self._on_move_ga(ident, node.id))
                else:
                    assert self._on_move_range is not None
                    self._report(self._on_move_range(ident, node.id))

    def _render_group_addresses(self, node: "GroupRangeInfo", flt: str) -> None:
        spacing = imgui.get_style().item_spacing.x
        for ga in node.group_addresses:
            if flt and not self._ga_matches(ga, flt):
                continue
            selected = ga.id == self._selected_ga_id and self._selected_range_id is None
            name = f"{ga.text}  {ga.name}"
            # Full-width row so the whole line is the click / right-click target and the name stays
            # fully visible; the last value seen on the bus is drawn after it (dimmed accent) and
            # clipped, so it never covers the name. allow_overlap lets the value be hovered for a
            # tooltip without fighting the row below it.
            row_x = imgui.get_cursor_pos_x()
            clicked = imgui.selectable(
                f"{name}##ga{ga.id}", selected, imgui.SelectableFlags_.allow_overlap
            )[0]
            if clicked:
                self._selected_ga_id = ga.id
                self._selected_range_id = None
                self._on_select_element("group_address", ga.id)
            if ga.id == self._selected_ga_id:
                # Kept current: the comment and description can change while it is selected.
                self._selected_ga_text = name
                self._selected_ga_description = ga.description
                self._selected_ga_comment = plain_text(parse_rich_text(ga.comment))
            if ga.id == self._reveal_ga_id:
                imgui.set_scroll_here_y()
            # Context menu while the selectable is still the last item, so it binds to the row.
            self._render_ga_context_menu(ga)
            if self._on_move_ga is not None and self._folders_enabled():
                drag_source(Dragged("group_address", (ga.id,)), name)
            end = row_x + imgui.calc_text_size(name).x
            if ga.data_secure:
                # The KNX Data Secure shield right after the name, as after a secure device.
                left = imgui.get_item_rect_min().x - row_x
                secure_icon(x=left + end + spacing)
                end += spacing + secure_icon_width()
            live = (
                self._get_latest_value(ga.text, ga.datapoint_type)
                if self._get_latest_value is not None
                else None
            )
            value_text, value_time = (live[0], live[1]) if live else ("", "")
            if value_text:
                imgui.same_line()
                imgui.set_cursor_pos_x(end + spacing * 3.0)
                imgui.text_colored(themed(*_LIVE_COLOR), value_text)
                if imgui.is_item_hovered():
                    imgui.set_tooltip(
                        f"{value_text}\n{value_time}" if value_time else value_text
                    )

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
        if not context_menu(f"##ga_ctx_{ga.id}"):  # type: ignore[attr-defined]
            return
        if imgui.menu_item(S.CONTEXT_RENAME, "", False)[0]:
            self._popup_ga_id = ga.id  # type: ignore[attr-defined]
            self._popup_name = ga.name  # type: ignore[attr-defined]
            self._open_rename_ga = True
        if imgui.menu_item(S.CONTEXT_COPY_ADDRESS, "", False)[0]:
            imgui.set_clipboard_text(getattr(ga, "text", "") or "")
        self._render_move_ga_menu(ga.id)  # type: ignore[attr-defined]
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

    def _render_move_ga_menu(self, ga_id: int) -> None:
        """ "Move to" of a group address: the groups that hold group addresses (the middle
        groups, or the main groups of the two-level style), its own grayed out."""
        if self._on_move_ga is None or not self._folders_enabled():
            return
        tree = self._get_range_tree()
        groups = (
            [m for main in tree for m in main.children]
            if self._middle_enabled()
            else list(tree)
        )
        current = next(
            (g.id for g in groups if any(a.id == ga_id for a in g.group_addresses)),
            None,
        )
        chosen = render_move_menu([(_range_text(g), g.id) for g in groups], current)
        if chosen is not None and chosen[0] is not None:
            self._report(self._on_move_ga(ga_id, chosen[0]))

    def _render_move_range_menu(self, range_id: int) -> None:
        """ "Move to" of a middle group: the main groups, its own grayed out."""
        if self._on_move_range is None or not self._middle_enabled():
            return
        tree = self._get_range_tree()
        current = next(
            (main.id for main in tree if any(m.id == range_id for m in main.children)),
            None,
        )
        if current is None:
            return
        chosen = render_move_menu([(_range_text(m), m.id) for m in tree], current)
        if chosen is not None:
            self._report(self._on_move_range(range_id, chosen[0]))

    def _report(self, reason: str | None) -> None:
        if reason:
            self._notify(reason)

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
        if not context_menu(f"##gr_ctx_{node.id}"):
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
        if not is_root:
            self._render_move_range_menu(node.id)
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
        if not begin_dialog(S.GA_FOLDER_DELETE, width=420.0):
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
        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.sizing_stretch_prop
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.reorderable
        )
        if not begin_data_table("##ga_assignments", 3, flags):
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
        table_header_menu()
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
                # The sending marker, in the Group Objects table's colour.
                imgui.text_colored(sending_green(), "»")
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
            # Topology tree does), and prefix the individual address for context.
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


def _forced_on(node: "GroupRangeInfo") -> bool:
    """Whether a group's Security is on: its own, or that of a group above it that forces one."""
    forced = node.forced_by
    return (forced.mode if forced is not None else node.security_mode) == "On"


def _style_text(style: "GroupAddressStyle") -> str:
    from xknxeditor.proj.core.addressing import GroupAddressStyle

    return {
        GroupAddressStyle.THREE_LEVEL: S.GA_STYLE_THREE_LEVEL,
        GroupAddressStyle.TWO_LEVEL: S.GA_STYLE_TWO_LEVEL,
        GroupAddressStyle.FREE: S.GA_STYLE_FREE,
    }[style]

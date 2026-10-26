"""Group Addresses view: the named range tree (main/middle/...) down to group addresses.

Shows, for the selected group address, the device com-objects assigned to it, and lets the user
create / rename / delete group addresses and groups (context menus + modals, mirroring the
Topology panel); a secured group address, and a group whose Security is on, shows the KNX Data
Secure shield. Address, data type and security are changed in the details."""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.color import sending_green, themed
from editor_gui.device import com_object_display_name
from editor_gui.plugins.project.paste import PasteTarget
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.add_dialog import (
    AddChoice,
    AddDialog,
    AddRequest,
    AddRow,
    UndoStep,
    add_button,
    add_menu_item,
    add_rows,
    no_room,
    no_undo_step,
    start_add,
)
from editor_gui.plugins.project.ui.group_value import GroupValueActions
from editor_gui.plugins.project.ui.move_menu import render_move_menu
from editor_gui.plugins.project.ui.paste_special import (
    PasteSpecialDialog,
    ShortcutRow,
    handle_row_keys,
    render_copy_group_address_item,
    render_copy_item,
    render_delete_item,
)
from editor_gui.plugins.project.ui.tree_drag import Dragged, drag_source, drop_target
from editor_gui.widgets.description import (
    description_icon,
    description_icon_width,
    render_description,
    secure_icon,
    secure_icon_width,
    shows_description_icon,
)
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.dpt_value import GroupValueBus
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tables import begin_data_table, table_header_menu
from editor_gui.widgets.text_util import text_clipped_tooltip
from editor_gui.widgets.tree import (
    begin_tree_area,
    context_menu,
    keyboard_moved_here,
    leaf_rows,
    row_chosen,
    tree_node,
)
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.paste import AddressPool, place_range
from xknxeditor.proj.core.rich_text import parse_rich_text, plain_text

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import Assignment
    from xknxeditor.proj.core.service import GroupRangeInfo


# Keeps the toolbar buttons apart from the menu items of the same text.
TOOLBAR_ID = "##ga_toolbar"

# The most main groups (0 to 31) and middle groups of a main group (0 to 7).
_MAIN_GROUPS = 32
_MIDDLE_GROUPS = 8
# How many addresses a new Free style range at the top spans.
_FREE_RANGE_CAPACITY = 1024

# Accent colour for the last value a group address was seen carrying on the bus, as (on dark
# themes, on light themes).
_LIVE_COLOR = (imgui.ImVec4(0.45, 0.7, 0.9, 1.0), imgui.ImVec4(0.1, 0.38, 0.7, 1.0))


def _address_room(group: "GroupRangeInfo") -> int:
    """How many more group addresses ``group`` holds."""
    return group.range_end - group.range_start + 1 - len(group.group_addresses)


def _range_text(node: "GroupRangeInfo") -> str:
    """A group as a "Move to" target: its number and name ("1/2  Lights")."""
    return f"{group_text(node)}##mvgr{node.id}"


def group_text(node: "GroupRangeInfo") -> str:
    """A group as lists show it: its number and name ("1/2  Lights"), a Free style range its
    addresses and name ("1-1024  Lights")."""
    base = node.name or f"[{node.id}]"
    number = node.address or (
        f"{node.range_start}-{node.range_end}" if node.range_end else ""
    )
    return f"{number}  {base}" if number else base


def address_groups(
    tree: "list[GroupRangeInfo]", style: GroupAddressStyle
) -> "list[GroupRangeInfo]":
    """The groups that hold group addresses, in tree order: the middle groups, the main groups
    of the two-level style, every range of the Free style."""
    if style is GroupAddressStyle.THREE_LEVEL:
        return [middle for main in tree for middle in main.children]
    if style is GroupAddressStyle.TWO_LEVEL:
        return list(tree)
    out: list[GroupRangeInfo] = []

    def walk(nodes: "list[GroupRangeInfo]") -> None:
        for node in nodes:
            out.append(node)
            walk(node.children)

    walk(tree)
    return out


def used_addresses(tree: "list[GroupRangeInfo]") -> set[int]:
    """Every address in use, in every group (Free style ranges may share them)."""
    used: set[int] = set()
    stack = list(tree)
    while stack:
        each = stack.pop()
        used.update(ga.address for ga in each.group_addresses)
        stack.extend(each.children)
    return used


def add_group_addresses(
    request: AddRequest,
    group_of: "Callable[[AddRow], GroupRangeInfo | None]",
    tree: "list[GroupRangeInfo]",
    style: GroupAddressStyle,
    create: Callable[[int, int, str], object],
    target: str,
    undo_step: UndoStep,
    notify: Callable[[str], None],
) -> None:
    """Add the group addresses ``request`` asks for (to ``target``, its name), a row's into the
    group ``group_of`` gives it, numbered as its "Generate Addresses" says: "Start with" counts
    within the group, in the Free style it is the address itself. ``create`` makes one (group
    id, address, name) and raises ``ValueError`` when it cannot. One undo step per row."""
    from xknxeditor.proj.core.paste import group_base

    used = used_addresses(tree)

    def add_one(row: AddRow) -> str | None:
        group = group_of(row)
        if group is None:
            return S.ADD_LIMIT_REACHED
        base = (
            0
            if style is GroupAddressStyle.FREE
            else group_base(group.range_start, style)
        )
        address = AddressPool(
            group.range_start,
            group.range_end,
            used,
            request.allocation,
            start_with=request.start_with,
            base=base,
        ).take()
        if address is None:
            return S.ADD_LIMIT_REACHED
        try:
            create(group.id, address, row.name.strip() or S.GA_DEFAULT_GROUP_ADDRESS)
        except ValueError as error:
            return str(error)
        used.add(address)
        return None

    add_rows(
        request,
        S.HISTORY_EV_ADDED_GROUP_ADDRESSES,
        target,
        add_one,
        undo_step,
        notify,
    )


class GroupAddressesPanel:
    def __init__(
        self,
        get_range_tree: "Callable[[], list[GroupRangeInfo]]",
        get_assignments_for_ga: "Callable[[int], list[Assignment]]",
        get_devices: "Callable[[], list[Device]]",
        on_create_ga_in: Callable[[int, int, str], object],
        on_remove_ga: Callable[[int], None],
        take_requested_ga: Callable[[], int | None] | None = None,
        on_create_range: Callable[[int | None, str, int | None], object] | None = None,
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
        on_move_range: Callable[[int, int | None], str | None] | None = None,
        notify: Callable[[str], None] = lambda _text: None,
        one_undo_step: UndoStep = no_undo_step,
        paste_dialog: PasteSpecialDialog | None = None,
        on_create_free_range: Callable[[int | None, str, int, int], object]
        | None = None,
    ) -> None:
        """``on_create_range`` adds a main group (parent None) or a middle group below a main
        group, with a name and a number (None: the next free one), giving its id or None;
        ``on_create_free_range`` a Free style range (parent, name, first and last address),
        ``on_create_ga_in`` a group address in a range (range id, address, name); both raise
        ``ValueError`` when it does not fit there.
        ``on_move_ga`` puts a group address into another group, ``on_move_range`` a middle
        group below another main group or a Free style range into another range (None: to the
        top) ("Move to"); both give the reason it cannot be, which
        ``notify`` shows. ``one_undo_step`` makes the edits of one drop one undo step;
        ``paste_dialog`` asks how to copy what is dropped with Ctrl held (no copies without
        it)."""
        self._one_undo_step = one_undo_step
        # The "Add …" dialog of main groups, middle groups and group addresses.
        self._adder = AddDialog("group_addresses")
        # The selected row as Ctrl+C/X/V take it, found each frame.
        self._shortcut_row: ShortcutRow | None = None
        self._paste_dialog = paste_dialog
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
        self._on_remove_ga = on_remove_ga
        self._on_create_range = on_create_range
        self._on_create_free_range = on_create_free_range
        self._on_create_ga_in = on_create_ga_in
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
        self._selected_ga_comment: str = ""
        self._group_value = (
            GroupValueActions(bus, "ga_write_value") if bus is not None else None
        )
        # Group-range (folder) modal state.
        self._popup_range_id: int = 0
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
        if add_button(
            f"{S.GA_ADD_GROUP_ADDRESSES}{TOOLBAR_ID}", self._no_address_group(tree)
        ):
            self._start_add_group_addresses_to(tree)
        # The buttons go on in the next line where the panel is too narrow (a phone).
        if self._folders_enabled():
            same_line_if_fits(button_width(S.GA_ADD_MAIN_GROUPS))
            if add_button(
                f"{S.GA_ADD_MAIN_GROUPS}{TOOLBAR_ID}",
                no_room(_MAIN_GROUPS - len(tree)),
            ):
                self._start_add_main_groups(tree)
        elif self._free_ranges_enabled():
            same_line_if_fits(button_width(S.GA_ADD_GROUP_RANGES))
            if imgui.small_button(f"{S.GA_ADD_GROUP_RANGES}{TOOLBAR_ID}"):
                self._start_add_free_ranges(None)
        if self._on_export is not None:
            same_line_if_fits(button_width(S.GA_EXPORT_BUTTON))
            if imgui.small_button(S.GA_EXPORT_BUTTON):
                self._on_export()
        if self._on_import is not None:
            same_line_if_fits(button_width(S.GA_IMPORT_BUTTON))
            if imgui.small_button(S.GA_IMPORT_BUTTON):
                self.start_import()

        if self._open_delete_range:
            imgui.open_popup(S.GA_FOLDER_DELETE)
            self._open_delete_range = False
        if self._group_value is not None:
            self._group_value.render()
        self._adder.render()
        self._render_delete_range_popup()
        if self._paste_dialog is not None:
            self._paste_dialog.render()

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
            self._shortcut_row = None
            for node in tree:
                self._render_range(node, flt, is_root=True)
            if self._paste_dialog is not None:
                self._paste_dialog.handle_shortcuts(self._shortcut_row)
            else:
                handle_row_keys(self._shortcut_row)
        imgui.end_child()
        # Reveal is a one-frame action: ancestors were force-opened and the GA scrolled into view.
        self._reveal_ga_id = None

        imgui.separator()
        self._render_assignments()

    def _render_create_menu_items(self) -> None:
        """The "Add …" items shared by the panel's context menus."""
        tree = self._get_range_tree()
        if add_menu_item(S.GA_ADD_GROUP_ADDRESSES, self._no_address_group(tree)):
            self._start_add_group_addresses_to(tree)
        if self._folders_enabled() and add_menu_item(
            S.GA_ADD_MAIN_GROUPS, no_room(_MAIN_GROUPS - len(tree))
        ):
            self._start_add_main_groups(tree)
        if (
            self._free_ranges_enabled()
            and imgui.menu_item(S.GA_ADD_GROUP_RANGES, "", False)[0]
        ):
            self._start_add_free_ranges(None)
        if self._paste_dialog is not None:
            # Main groups and Free style ranges pasted here go to the top.
            imgui.separator()
            self._paste_dialog.render_paste_items(self._top_copy_target)
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
            self._shortcut_row = ShortcutRow(
                Dragged("group_range", (node.id,)),
                lambda c: self._range_copy_target(node, is_root, c),
                # A main group stays where it is; a Free style range moves anywhere.
                movable=not is_root or self._free(),
                on_delete=(
                    (lambda: self._ask_delete_range(node.id))
                    if self._on_remove_range is not None
                    else None
                ),
            )
        open_node = tree_node(label, flags)
        if row_chosen():
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
        render_description(node.description)
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
        address onto another group that holds group addresses, a Free style range into another
        range. With Ctrl held they are copied there, also into their own group; a main group's
        copy (dragged onto any main group) goes to the top."""
        middle = self._middle_enabled()
        free = self._free()
        movable = self._on_move_range is not None and (free or (middle and not is_root))
        if movable or (is_root and self._paste_dialog is not None):
            drag_source(Dragged("group_range", (node.id,)), shown)
        holds_addresses = free or (
            self._folders_enabled() and (not is_root if middle else is_root)
        )

        def accepts(dragged: Dragged) -> bool:
            inside = {ga.id for ga in node.group_addresses} | {
                c.id for c in node.children
            }
            if inside.issuperset(dragged.ids):
                return False
            if dragged.kind == "group_address":
                return holds_addresses and self._on_move_ga is not None
            if dragged.kind == "group_range" and free:
                return self._on_move_range is not None and not self._inside_any(
                    node.id, dragged.ids
                )
            return (
                dragged.kind == "group_range"
                and middle
                and is_root
                and self._main_groups().isdisjoint(dragged.ids)
            )

        def copy_target(dragged: Dragged) -> PasteTarget | None:
            return self._range_copy_target(node, is_root, dragged)

        drop = drop_target(accepts, lambda dragged: copy_target(dragged) is not None)
        if drop is None:
            return
        if drop.copy:
            target = copy_target(drop.dragged)
            assert self._paste_dialog is not None and target is not None
            self._paste_dialog.open(drop.dragged.kind, list(drop.dragged.ids), target)
            return
        with self._one_undo_step():
            for ident in drop.dragged.ids:
                if drop.dragged.kind == "group_address":
                    assert self._on_move_ga is not None
                    self._report(self._on_move_ga(ident, node.id))
                else:
                    assert self._on_move_range is not None
                    self._report(self._on_move_range(ident, node.id))

    def _top_copy_target(self, copied: Dragged) -> PasteTarget | None:
        """Where ``copied`` goes when pasted beside the groups: main groups and Free style
        ranges to the top."""
        if copied.kind != "group_range" or not (
            self._free() or self._main_groups().issuperset(copied.ids)
        ):
            return None
        return PasteTarget("group_range", None)

    def _range_copy_target(
        self, node: "GroupRangeInfo", is_root: bool, copied: Dragged
    ) -> PasteTarget | None:
        """Where copies of ``copied`` go when pasted on the group ``node`` (a main group with
        ``is_root``): group addresses into it when it holds group addresses (also their own),
        middle groups into a main group, main groups to the top; None for anything else."""
        if self._paste_dialog is None:
            return None
        middle = self._middle_enabled()
        free = self._free()
        if copied.kind == "group_address":
            holds_addresses = free or (
                self._folders_enabled() and (not is_root if middle else is_root)
            )
            return PasteTarget("group_range", node.id) if holds_addresses else None
        if copied.kind == "group_range" and free:
            # Into any range but itself and those in it.
            if self._inside_any(node.id, copied.ids):
                return None
            return PasteTarget("group_range", node.id)
        if copied.kind != "group_range" or not is_root:
            return None
        mains = self._main_groups()
        if mains.issuperset(copied.ids):
            return PasteTarget("group_range", None)
        if middle and mains.isdisjoint(copied.ids):
            return PasteTarget("group_range", node.id)
        return None

    def _render_group_addresses(self, node: "GroupRangeInfo", flt: str) -> None:
        spacing = imgui.get_style().item_spacing.x
        for ga in node.group_addresses:
            if flt and not self._ga_matches(ga, flt):
                continue
            selected = ga.id == self._selected_ga_id and self._selected_range_id is None
            if selected:
                self._shortcut_row = ShortcutRow(
                    Dragged("group_address", (ga.id,)),
                    lambda c, group=node: (
                        PasteTarget("group_range", group.id)
                        if c.kind == "group_address"
                        else None
                    ),
                    on_delete=lambda ga_id=ga.id: self._delete_ga(ga_id),
                )
            name = f"{ga.text}  {ga.name}"
            # Full-width row so the whole line is the click / right-click target and the name stays
            # fully visible; the last value seen on the bus is drawn after it (dimmed accent) and
            # clipped, so it never covers the name. allow_overlap lets the value be hovered for a
            # tooltip without fighting the row below it.
            row_x = imgui.get_cursor_pos_x()
            clicked = imgui.selectable(
                f"{name}##ga{ga.id}", selected, imgui.SelectableFlags_.allow_overlap
            )[0]
            if clicked or keyboard_moved_here():
                self._selected_ga_id = ga.id
                self._selected_range_id = None
                self._on_select_element("group_address", ga.id)
            if ga.id == self._selected_ga_id:
                # Kept current: the comment can change while it is selected.
                self._selected_ga_text = name
                self._selected_ga_comment = plain_text(parse_rich_text(ga.comment))
            if ga.id == self._reveal_ga_id:
                imgui.set_scroll_here_y()
            # Context menu while the selectable is still the last item, so it binds to the row.
            self._render_ga_context_menu(ga, node)
            if self._on_move_ga is not None and (
                self._folders_enabled() or self._free()
            ):
                drag_source(Dragged("group_address", (ga.id,)), name)
            end = row_x + imgui.calc_text_size(name).x
            # Screen x of the window's x 0 (``end`` counts in window coordinates).
            left = imgui.get_item_rect_min().x - row_x
            if ga.data_secure:
                # The KNX Data Secure shield right after the name, as after a secure device.
                secure_icon(x=left + end + spacing)
                end += spacing + secure_icon_width()
            # Its description: a short one inline, a longer one behind the note icon.
            description = (ga.description or "").strip()
            if description and shows_description_icon(description):
                description_icon(description, x=left + end + spacing)
                end += spacing + description_icon_width()
            elif description:
                imgui.same_line()
                imgui.set_cursor_pos_x(end + spacing)
                imgui.text_disabled(description)
                end += spacing + imgui.calc_text_size(description).x
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

    def _render_ga_context_menu(self, ga: object, group: "GroupRangeInfo") -> None:
        """The menu of a group address in ``group``: what is pasted on it goes into its
        group."""
        # ga is a core GroupAddressInfo (id, text, name, datapoint_type, …).
        if not context_menu(f"##ga_ctx_{ga.id}"):  # type: ignore[attr-defined]
            return
        # As a device's: where it is and its address; reading and writing its value; then
        # Copy / Cut / Paste and "Delete".
        self._render_move_ga_menu(ga.id)  # type: ignore[attr-defined]
        render_copy_group_address_item(getattr(ga, "text", "") or "")
        if self._group_value is not None:
            imgui.separator()
            self._group_value.render_menu_items(
                getattr(ga, "text", "") or "",
                getattr(ga, "name", "") or "",
                getattr(ga, "datapoint_type", None),
            )
        if self._paste_dialog is not None:
            imgui.separator()
            render_copy_item(Dragged("group_address", (ga.id,)))  # type: ignore[attr-defined]
            self._paste_dialog.render_paste_items(
                lambda c: (
                    PasteTarget("group_range", group.id)
                    if c.kind == "group_address"
                    else None
                )
            )
        imgui.separator()
        if render_delete_item():
            self._delete_ga(ga.id)  # type: ignore[attr-defined]
        imgui.end_popup()

    def _delete_ga(self, ga_id: int) -> None:
        """Delete the group address ``ga_id`` ("Delete" in its menu, or the Delete key)."""
        self._on_remove_ga(ga_id)
        if self._selected_ga_id == ga_id:
            self._selected_ga_id = None

    def _ask_delete_range(self, range_id: int) -> None:
        """Ask before the group ``range_id`` is deleted with everything in it."""
        self._popup_range_id = range_id
        self._open_delete_range = True

    def _render_move_ga_menu(self, ga_id: int) -> None:
        """ "Move to" of a group address: the groups that hold group addresses (the middle
        groups, or the main groups of the two-level style, every range of the Free style), its
        own grayed out. The middle groups are listed below their main groups, which are grayed
        out; the ranges of the Free style below the ranges they are in."""
        if self._on_move_ga is None or not (self._folders_enabled() or self._free()):
            return
        tree = self._get_range_tree()
        style = self._style()
        groups = address_groups(tree, style)
        current = next(
            (g.id for g in groups if any(a.id == ga_id for a in g.group_addresses)),
            None,
        )
        targets: list[tuple[str, int]] = []
        disabled: set[int] = set()
        if style is GroupAddressStyle.THREE_LEVEL:
            for main in tree:
                targets.append((_range_text(main), main.id))
                disabled.add(main.id)
                targets.extend(
                    (f"  {_range_text(middle)}", middle.id) for middle in main.children
                )
        else:
            depth_of = self._depths(tree)
            targets = [
                (f"{'  ' * depth_of[g.id]}{_range_text(g)}", g.id) for g in groups
            ]
        chosen = render_move_menu(targets, current, disabled)
        if chosen is not None:
            self._report(self._on_move_ga(ga_id, chosen[0]))

    def _render_move_range_menu(self, range_id: int) -> None:
        """ "Move to" of a middle group: the main groups, its own grayed out. Of a Free style
        range: the top, then every range but itself and those in it, where it is grayed
        out."""
        if self._on_move_range is None:
            return
        tree = self._get_range_tree()
        if self._free():
            parent = next(
                (
                    g.id
                    for g in address_groups(tree, GroupAddressStyle.FREE)
                    if any(c.id == range_id for c in g.children)
                ),
                None,
            )
            targets: list[tuple[str, int | None]] = [(S.SPACES_MOVE_ROOT, None)]
            depth_of = self._depths(tree)
            targets.extend(
                (f"{'  ' * depth_of[g.id]}{_range_text(g)}", g.id)
                for g in address_groups(tree, GroupAddressStyle.FREE)
                if not self._inside_any(g.id, (range_id,))
            )
            chosen_free = render_move_menu(targets, parent)
            if chosen_free is not None:
                self._report(self._on_move_range(range_id, chosen_free[0]))
            return
        if not self._middle_enabled():
            return
        current = next(
            (main.id for main in tree if any(m.id == range_id for m in main.children)),
            None,
        )
        if current is None:
            return
        chosen = render_move_menu([(_range_text(m), m.id) for m in tree], current)
        if chosen is not None:
            self._report(self._on_move_range(range_id, chosen[0]))

    def _main_groups(self) -> set[int]:
        """The ids of the main groups (the top of the tree)."""
        return {node.id for node in self._get_range_tree()}

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

    def _render_range_context_menu(self, node: "GroupRangeInfo", is_root: bool) -> None:
        if self._on_create_range is None and self._on_remove_range is None:
            return  # folder editing disabled
        if not context_menu(f"##gr_ctx_{node.id}"):
            return
        # Group addresses live in middle groups (ThreeLevel) or main groups (TwoLevel).
        if not (is_root and self._middle_enabled()) and add_menu_item(
            S.GA_ADD_GROUP_ADDRESSES, no_room(_address_room(node))
        ):
            self._start_add_group_addresses(node)
        # A middle group can only be added under a top-level (main) group, ThreeLevel only.
        if (
            is_root
            and self._middle_enabled()
            and add_menu_item(
                S.GA_ADD_MIDDLE_GROUPS,
                no_room(_MIDDLE_GROUPS - len(node.children)),
            )
        ):
            self._start_add_middle_groups(node)
        # A Free style range holds ranges as well as group addresses.
        if (
            self._free_ranges_enabled()
            and imgui.menu_item(S.GA_ADD_GROUP_RANGES, "", False)[0]
        ):
            self._start_add_free_ranges(node)
        if not is_root or self._free():
            self._render_move_range_menu(node.id)
        if self._paste_dialog is not None:
            imgui.separator()
            # A main group cannot be moved: no "Cut".
            render_copy_item(
                Dragged("group_range", (node.id,)), movable=not is_root or self._free()
            )
            self._paste_dialog.render_paste_items(
                lambda c: self._range_copy_target(node, is_root, c)
            )
        if self._on_remove_range is not None:
            imgui.separator()
            if render_delete_item():
                self._ask_delete_range(node.id)
        imgui.end_popup()

    def _style(self) -> "GroupAddressStyle":
        from xknxeditor.proj.core.addressing import GroupAddressStyle

        if self._group_style is None:
            return GroupAddressStyle.THREE_LEVEL
        return self._group_style()

    def _start_add_main_groups(self, tree: "list[GroupRangeInfo]") -> None:
        """ "Add Main Groups": numbers 0 to 31, at most 32 main groups."""
        target = S.PANEL_GROUP_ADDRESSES

        def add(request: AddRequest) -> None:
            used = {node.range_start >> 11 for node in self._get_range_tree()}
            self._add_groups(
                request, None, used, 31, S.GA_DEFAULT_MAIN_GROUP, target, main=True
            )

        start_add(
            lambda: self._adder.open(
                S.GA_TITLE_MAIN_GROUPS,
                target,
                add,
                default_name=S.GA_DEFAULT_MAIN_GROUP,
                numbered=True,
                start_minimum=0,
                start_maximum=31,
                limit=_MAIN_GROUPS - len(tree),
            ),
            add,
            self._notify,
        )

    def _start_add_middle_groups(self, node: "GroupRangeInfo") -> None:
        """ "Add Middle Groups" below a main group: numbers 0 to 7, at most 8."""
        target = f"{node.address} {node.name}".strip()

        def add(request: AddRequest) -> None:
            main = next((n for n in self._get_range_tree() if n.id == node.id), node)
            used = {(child.range_start >> 8) & 7 for child in main.children}
            self._add_groups(
                request, node.id, used, 7, S.GA_DEFAULT_MIDDLE_GROUP, target, main=False
            )

        start_add(
            lambda: self._adder.open(
                S.GA_TITLE_MIDDLE_GROUPS,
                target,
                add,
                default_name=S.GA_DEFAULT_MIDDLE_GROUP,
                numbered=True,
                start_minimum=0,
                start_maximum=7,
                limit=_MIDDLE_GROUPS - len(node.children),
            ),
            add,
            self._notify,
        )

    def _add_groups(
        self,
        request: AddRequest,
        parent: int | None,
        used: set[int],
        highest: int,
        default: str,
        target: str,
        *,
        main: bool,
    ) -> None:
        if self._on_create_range is None:
            return
        create = self._on_create_range
        pool = AddressPool(
            0,
            highest,
            used,
            request.allocation,
            start_with=request.start_with,
            skip_zero=False,
        )

        def add(row: AddRow) -> str | None:
            number = pool.take()
            if number is None:
                return S.ADD_LIMIT_REACHED
            created = create(parent, row.name.strip() or default, number)
            return None if created is not None else S.ADD_LIMIT_REACHED

        add_rows(
            request,
            S.HISTORY_EV_ADDED_MAIN_GROUPS
            if main
            else S.HISTORY_EV_ADDED_MIDDLE_GROUPS,
            target,
            add,
            self._one_undo_step,
            self._notify,
        )

    def _start_add_group_addresses(self, node: "GroupRangeInfo") -> None:
        """ "Add Group Addresses" to a middle group (a main group in two-level style): "Start
        with" is the address within the group."""
        from xknxeditor.proj.core.paste import group_base

        style = self._style()
        free = style is GroupAddressStyle.FREE
        # In the Free style "Start with" is the address itself.
        base = 0 if free else group_base(node.range_start, style)
        target = f"{node.address} {node.name}".strip() or node.name

        def add(request: AddRequest) -> None:
            group = self._find_range(node.id) or node
            add_group_addresses(
                request,
                lambda _row: group,
                self._get_range_tree(),
                style,
                self._on_create_ga_in,
                target,
                self._one_undo_step,
                self._notify,
            )

        start_add(
            lambda: self._adder.open(
                S.GA_TITLE_GROUP_ADDRESSES,
                target,
                add,
                default_name=S.GA_DEFAULT_GROUP_ADDRESS,
                numbered=True,
                start_minimum=node.range_start if free else 0,
                start_maximum=node.range_end - base,
                limit=_address_room(node),
            ),
            add,
            self._notify,
            start_with=node.range_start if free else 0,
        )

    def _no_address_group(self, tree: "list[GroupRangeInfo]") -> str:
        """Why no group address can be added from the panel: no group holds any ("" when one
        does)."""
        return "" if address_groups(tree, self._style()) else S.SPACES_FN_NO_GROUP

    def _start_add_group_addresses_to(self, tree: "list[GroupRangeInfo]") -> None:
        """ "Add Group Addresses" from the panel: the group each row's go into is chosen in the
        dialog, at first the selected group (or the group of the selected group address)."""
        from xknxeditor.proj.core.paste import group_base

        style = self._style()
        groups = address_groups(tree, style)
        if not groups:
            return
        first = next(
            (
                g
                for g in groups
                if g.id == self._selected_range_id
                or any(ga.id == self._selected_ga_id for ga in g.group_addresses)
            ),
            groups[0],
        )
        choice = AddChoice(
            S.SPACES_FN_GA_GROUP,
            [(str(g.id), group_text(g)) for g in groups],
            str(first.id),
        )
        free = style is GroupAddressStyle.FREE
        target = S.PANEL_GROUP_ADDRESSES

        def add(request: AddRequest) -> None:
            now = self._get_range_tree()
            by_id = {g.id: g for g in address_groups(now, style)}
            add_group_addresses(
                request,
                lambda row: (
                    by_id.get(int(row.choice)) if row.choice.isdigit() else None
                ),
                now,
                style,
                self._on_create_ga_in,
                target,
                self._one_undo_step,
                self._notify,
            )

        start_add(
            lambda: self._adder.open(
                S.GA_TITLE_GROUP_ADDRESSES,
                target,
                add,
                default_name=S.GA_DEFAULT_GROUP_ADDRESS,
                choice=choice,
                numbered=True,
                # "Start with" counts within the group, in the Free style it is the address.
                start_minimum=1 if free else 0,
                start_maximum=0xFFFF
                if free
                else first.range_end - group_base(first.range_start, style),
            ),
            add,
            self._notify,
            choice,
            start_with=first.range_start if free else 0,
        )

    def _start_add_free_ranges(self, parent: "GroupRangeInfo | None") -> None:
        """ "Add Group Ranges" of the Free style, at the top or in ``parent``: each row's ranges
        span its Capacity (1024 at the top, a quarter of the parent in one), placed as
        "Generate Addresses" says ("Start with" is the first address)."""
        create = self._on_create_free_range
        if create is None:
            return
        low, high = (
            (1, 0xFFFF) if parent is None else (parent.range_start, parent.range_end)
        )
        capacity = (
            _FREE_RANGE_CAPACITY
            if parent is None
            else max(1, (parent.range_end - parent.range_start) // 4)
        )
        parent_id = parent.id if parent is not None else None
        target = S.PANEL_GROUP_ADDRESSES if parent is None else parent.name

        def add(request: AddRequest) -> None:
            siblings = (
                self._get_range_tree()
                if parent is None
                else (self._find_range(parent.id) or parent).children
            )
            taken = [(n.range_start, n.range_end) for n in siblings]

            def add_one(row: AddRow) -> str | None:
                start = place_range(
                    low,
                    high,
                    taken,
                    row.capacity,
                    request.allocation,
                    request.start_with,
                )
                if start is None:
                    return S.ADD_LIMIT_REACHED
                end = start + row.capacity - 1
                try:
                    create(
                        parent_id,
                        row.name.strip() or S.GA_DEFAULT_GROUP_RANGE,
                        start,
                        end,
                    )
                except ValueError as error:
                    return str(error)
                taken.append((start, end))
                return None

            add_rows(
                request,
                S.HISTORY_EV_ADDED_GROUP_RANGES,
                target,
                add_one,
                self._one_undo_step,
                self._notify,
            )

        start_add(
            lambda: self._adder.open(
                S.GA_TITLE_GROUP_RANGES,
                target,
                add,
                default_name=S.GA_DEFAULT_GROUP_RANGE,
                numbered=True,
                start_minimum=low,
                start_maximum=high,
                capacity=capacity,
            ),
            add,
            self._notify,
            start_with=low,
            capacity=capacity,
        )

    def _free_ranges_enabled(self) -> bool:
        """Group ranges of any span: the Free style."""
        return self._on_create_free_range is not None and self._free()

    def _free(self) -> bool:
        """Whether the project has the Free style: ranges of any span, anywhere."""
        return (
            self._group_style is not None
            and self._group_style() is GroupAddressStyle.FREE
        )

    def _inside_any(self, range_id: int, ids: "Iterable[int]") -> bool:
        """Whether the range ``range_id`` is one of the ranges ``ids`` or in one of them."""
        for ident in ids:
            node = self._find_range(ident)
            if node is not None and _contains_range(node, range_id):
                return True
        return False

    @staticmethod
    def _depths(tree: "list[GroupRangeInfo]") -> dict[int, int]:
        """How deep each range of ``tree`` is (0 at the top)."""
        depths: dict[int, int] = {}

        def walk(nodes: "list[GroupRangeInfo]", depth: int) -> None:
            for node in nodes:
                depths[node.id] = depth
                walk(node.children, depth + 1)

        walk(tree, 0)
        return depths

    def _find_range(self, range_id: int) -> "GroupRangeInfo | None":
        """The group ``range_id`` as the tree shows it now."""
        stack = list(self._get_range_tree())
        while stack:
            node = stack.pop()
            if node.id == range_id:
                return node
            stack.extend(node.children)
        return None

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

    def _render_assignments(self) -> None:
        if self._selected_ga_id is None:
            imgui.text_disabled(S.GA_ASSIGNED_OBJECTS)
            return
        # The description shows in the tree (behind its icon when long) and in the details.
        imgui.text_disabled(self._selected_ga_text)
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


def _contains_range(node: "GroupRangeInfo", range_id: int) -> bool:
    """Whether ``range_id`` is ``node`` or a range in it."""
    return node.id == range_id or any(
        _contains_range(child, range_id) for child in node.children
    )


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

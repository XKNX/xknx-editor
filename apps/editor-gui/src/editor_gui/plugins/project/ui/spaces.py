"""Buildings view: the imported location tree (building -> floor -> room -> ...) with the
devices placed in each space and the functions assigned to it."""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui

from editor_gui.plugins.project.element_status import usage_label, usage_options
from editor_gui.plugins.project.paste import PasteTarget
from editor_gui.plugins.project.space_text import (
    DEFAULT_FUNCTION_TYPE,
    add_item_text,
    add_title,
    default_function_name,
    default_name,
    function_type_label,
    function_type_presets,
    space_type_name,
)
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.add_dialog import (
    AddChoice,
    AddDialog,
    AddRequest,
    AddRow,
    UndoStep,
    add_menu_item,
    no_undo_step,
    one_row,
    start_add,
)
from editor_gui.plugins.project.ui.config_dialog import DeviceConfigDialog
from editor_gui.plugins.project.ui.group_addresses import (
    add_group_addresses,
    address_groups,
    group_text,
)
from editor_gui.plugins.project.ui.move_menu import render_move_menu
from editor_gui.plugins.project.ui.paste_special import (
    MenuGroups,
    PasteSpecialDialog,
    ShortcutRow,
    handle_row_keys,
    no_paste,
    render_copy_device_address_item,
    render_copy_group_address_item,
    render_copy_item,
    render_delete_item,
)
from editor_gui.plugins.project.ui.topology import render_read_device_item
from editor_gui.plugins.project.ui.tree_drag import Dragged, drag_source, drop_target
from editor_gui.widgets.description import render_description, render_device_marks
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px_vec2
from editor_gui.widgets.tree import (
    begin_tree_area,
    context_menu,
    leaf_rows,
    tree_node,
)
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.paste import group_base
from xknxeditor.proj.core.space_rules import ROOM, child_types, holds_devices

if TYPE_CHECKING:
    from xknxeditor.proj.core.service import (
        FunctionGroupAddressInfo,
        FunctionInfo,
        SpaceDeviceInfo,
        SpaceInfo,
        TradeInfo,
    )


def space_label(space: "SpaceInfo") -> str:
    """A space as the tree lists it: its name, then its number in brackets."""
    label = space.name or space_type_name(space.space_type) or "?"
    if space.number:
        label = f"{label} ({space.number})"
    return label


# The type the "Add" dialog adds when it adds trades.
_TRADE = "Trade"


# Dialog ids: the translated title plus a stable per-target id (open_popup and begin_dialog
# must be passed the same string).
def _parent_id(
    tree: "Sequence[SpaceInfo] | Sequence[TradeInfo]", node_id: int
) -> int | None:
    """The id of the node ``node_id`` is below in ``tree`` (spaces or trades); None at the top
    level (or when it is not in the tree)."""

    def walk(
        nodes: "Sequence[SpaceInfo] | Sequence[TradeInfo]", parent: int | None
    ) -> int | None:
        for node in nodes:
            if node.id == node_id:
                return parent
            found = walk(node.children, node.id)
            if found is not None:
                return found
        return None

    return walk(tree, None)


def _find_node(tree: "Sequence[SpaceInfo] | Sequence[TradeInfo]", node_id: int) -> Any:
    """The space or trade ``node_id`` in ``tree``, None when it is not there."""
    for node in tree:
        if node.id == node_id:
            return node
        found = _find_node(node.children, node_id)
        if found is not None:
            return found
    return None


def _trade_devices_dialog(trade_id: int) -> str:
    return f"{S.TRADES_ADD_DEVICES_TITLE}###tradedev{trade_id}"


def _delete_space_dialog(space_id: int) -> str:
    return f"{S.SPACES_DELETE_TITLE}###delspace{space_id}"


def _assign_device_dialog(space_id: int) -> str:
    return f"{S.SPACES_ASSIGN_TITLE}###assigndev{space_id}"


def _link_function_dialog(function_id: int) -> str:
    return f"{S.SPACES_FN_LINK_TITLE}###linkfn{function_id}"


def _function_label(function: "FunctionInfo") -> str:
    return function.name or function.usage_text or function.function_type


class SpacesPanel:
    def __init__(
        self,
        get_space_tree: "Callable[[], list[SpaceInfo]]",
        on_select_device_id: Callable[[int], None],
        on_create_function: Callable[[int, str, str], object] | None = None,
        on_remove_function: Callable[[int], None] | None = None,
        on_move_function: Callable[[int, int], None] | None = None,
        on_set_function_type: Callable[[int, str], None] | None = None,
        on_add_function_ga: Callable[[int, int, str], object] | None = None,
        on_remove_function_ga: Callable[[int], None] | None = None,
        get_ga_range_tree: Callable[[], list[Any]] | None = None,
        on_create_space: Callable[[int | None, str, str, str], object] | None = None,
        get_selected_space_id: Callable[[], int | None] | None = None,
        on_select_space: Callable[[int], None] | None = None,
        on_move_space: Callable[[int, int | None], str | None] | None = None,
        on_remove_space: Callable[[int], None] | None = None,
        on_set_device_space: Callable[[int, int | None], str | None] | None = None,
        get_unassigned_devices: "Callable[[], list[SpaceDeviceInfo]] | None" = None,
        on_copy_config: Callable[[int], None] | None = None,
        on_paste_config: Callable[[int, bool, bool], None] | None = None,
        on_duplicate_config: Callable[[int, bool, bool], None] | None = None,
        can_paste_config: Callable[[int], bool] | None = None,
        count_paste_targets: Callable[[int], int] | None = None,
        on_device_info: Callable[[int], None] | None = None,
        on_compare: Callable[[int], None] | None = None,
        on_select_element: Callable[[str, int], None] = lambda _k, _i: None,
        get_selected_node_id: Callable[[], int | None] | None = None,
        get_trade_tree: "Callable[[], list[TradeInfo]]" = list,
        on_create_trade: Callable[[int | None, str], object] | None = None,
        on_move_trade: Callable[[int, int | None], None] | None = None,
        on_remove_trade: Callable[[int], None] | None = None,
        on_add_trade_device: Callable[[int, int], None] | None = None,
        on_remove_trade_device: Callable[[int, int], None] | None = None,
        get_space_devices: "Callable[[], list[SpaceDeviceInfo]]" = list,
        notify: Callable[[str], None] = lambda _t: None,
        one_undo_step: UndoStep = no_undo_step,
        paste_dialog: PasteSpecialDialog | None = None,
        on_delete_device: Callable[[int], None] | None = None,
        get_parked_address: Callable[[int], str] = lambda _n: "",
        group_style: Callable[[], GroupAddressStyle] = (
            lambda: GroupAddressStyle.THREE_LEVEL
        ),
        on_create_ga_in: Callable[[int, int, str], int | None] | None = None,
        get_selected_element: Callable[[], tuple[str, int] | None] = lambda: None,
    ) -> None:
        """``notify`` shows the reason an edit was refused; ``one_undo_step`` makes the edits of
        one drop one undo step; ``paste_dialog`` asks how to copy what is dropped with Ctrl
        held (no copies without it); ``on_delete_device`` deletes a device (by id) from the
        project, after the user confirmed it; ``get_parked_address`` gives "1.5.-" for a device
        on a line without a device number. ``on_create_ga_in`` creates a group address (group
        id, address, name) for a function's "Add Group Addresses…", giving its id and raising
        ``ValueError`` when it does not fit there; ``get_selected_element`` is the element
        selected in any tree (kind, id)."""
        self._get_parked_address = get_parked_address
        self._group_style = group_style
        self._on_create_ga_in = on_create_ga_in
        self._get_selected_element = get_selected_element
        # The group address link (of a function) selected in this tree.
        self._selected_link: int | None = None
        self._one_undo_step = one_undo_step
        self._paste_dialog = paste_dialog
        self._on_delete_device = on_delete_device
        # The device the "Delete Device" confirmation asks about: its id and name.
        self._delete_device: tuple[int, str] | None = None
        self._open_delete_device = False
        self._get_trade_tree = get_trade_tree
        self._on_create_trade = on_create_trade
        self._on_move_trade = on_move_trade
        self._on_remove_trade = on_remove_trade
        self._on_add_trade_device = on_add_trade_device
        self._on_remove_trade_device = on_remove_trade_device
        self._get_space_devices = get_space_devices
        self._notify = notify
        # The trade selected in this tree (highlighted), until a space, function or device is.
        self._selected_trade: int | None = None
        # The "Add …" dialog of building parts and trades.
        self._adder = AddDialog("buildings")
        # The selected row as Ctrl+C/X/V take it, found each frame.
        self._shortcut_row: ShortcutRow | None = None
        self._trade_device_filter = ""
        # Selecting a function (its comment shows in the Comment tab).
        self._on_select_element = on_select_element
        # The function selected in this tree: highlighted instead of the space until a space or a
        # device is selected here.
        self._selected_function: int | None = None
        self._get_space_tree = get_space_tree
        self._on_select_device_id = on_select_device_id
        self._on_create_function = on_create_function
        self._on_remove_function = on_remove_function
        self._on_move_function = on_move_function
        self._on_set_function_type = on_set_function_type
        self._on_add_function_ga = on_add_function_ga
        self._on_remove_function_ga = on_remove_function_ga
        self._get_ga_range_tree = get_ga_range_tree
        self._on_create_space = on_create_space
        self._get_selected_space_id = get_selected_space_id
        self._on_select_space = on_select_space
        self._on_move_space = on_move_space
        self._on_remove_space = on_remove_space
        self._on_set_device_space = on_set_device_space
        self._get_unassigned_devices = get_unassigned_devices
        self._on_copy_config = on_copy_config
        self._can_paste_config_cb = can_paste_config
        self._on_device_info = on_device_info
        self._on_compare = on_compare
        # Node id of the globally selected device, so this tree highlights the matching leaf.
        self._get_selected_node_id = get_selected_node_id
        self._config_dialog = DeviceConfigDialog(
            "spaces",
            on_paste_config or (lambda _n, _p, _l: None),
            on_duplicate_config or (lambda _n, _p, _l: None),
            count_paste_targets or (lambda _n: 1),
        )
        self._filter_text: str = ""
        # "Link with Group Address" dialog state.
        self._link_filter = ""
        # "Assign device" dialog state.
        self._assign_filter = ""
        # A popup requested from inside a context menu must be opened one scope up, not inside the
        # context-menu popup: imgui.open_popup() and begin_popup() only match when called in the
        # same window/id scope. Opening from within the context menu (a different popup window)
        # registers the popup under a mismatched id, so begin_dialog() at the tree scope never sees
        # it. The context menu stores its id here; the tree scope opens it (see _open_if_pending).
        self._pending_popup_id: str | None = None

    def render(self) -> None:
        tree = self._get_space_tree()
        self._filter_text = filter_box(
            "##spaces_filter", S.SPACES_FILTER_HINT, self._filter_text
        )
        flt = self._filter_text.strip().lower()
        begin_tree_area("##spaces_tree")
        self._shortcut_row = None
        self._render_tree(tree, flt)
        if self._paste_dialog is not None:
            self._paste_dialog.handle_shortcuts(self._shortcut_row)
        else:
            handle_row_keys(self._shortcut_row)
        imgui.end_child()
        self._config_dialog.render()
        self._adder.render()
        self._render_delete_device_confirm()
        if self._paste_dialog is not None:
            self._paste_dialog.render()

    def _render_tree(self, tree: list["SpaceInfo"], flt: str) -> None:
        """The buildings and trades below the filter box, with their menus and dialogs."""
        # "Buildings" holds the buildings, "Trades" the trades: both open by default.
        flags = imgui.TreeNodeFlags_.default_open
        buildings_open = tree_node(f"{S.SPACES_ROOT_BUILDINGS}##spaces_root", flags)
        if self._on_create_space is not None and context_menu("##spaces_root_ctx"):
            if imgui.menu_item(S.SPACES_ADD_BUILDINGS, "", False)[0]:
                self._start_add(child_types(None)[0], None, S.SPACES_ROOT_BUILDINGS)
            self._render_paste_items(lambda c: self._space_copy_target(None, c))
            imgui.end_popup()
        self._drop_on_space(None)
        if buildings_open:
            if not tree:
                imgui.text_disabled(S.SPACES_EMPTY)
            for space in tree:
                self._render_space(space, flt)
            imgui.tree_pop()
        self._render_trades(flt)
        self._render_unassigned_section()

    def _drag_and_drop_space(self, space: "SpaceInfo") -> None:
        """A space is dragged where its "Move to" puts it; devices are dropped on it."""
        if self._on_move_space is not None:
            drag_source(Dragged("space", (space.id,)), space_label(space))
        self._drop_on_space(space)

    def _drop_on_space(self, space: "SpaceInfo | None") -> None:
        """The space just drawn (``None``: the "Buildings" root) as a place to drop on: the
        spaces that may go below it, functions, and devices when it holds them."""
        tree = self._get_space_tree()

        def accepts(dragged: Dragged) -> bool:
            if dragged.kind == "space":
                return self._on_move_space is not None and all(
                    self._space_may_go(tree, moving, space) for moving in dragged.ids
                )
            if dragged.kind == "function":
                if space is None or self._on_move_function is None:
                    return False
                return not {f.id for f in space.functions}.issuperset(dragged.ids)
            if dragged.kind != "device" or space is None:
                return False
            here = {device.id for device in space.devices}
            return (
                self._on_set_device_space is not None
                and holds_devices(space.space_type)
                and not here.issuperset(dragged.ids)
            )

        drop = drop_target(
            accepts, lambda dragged: self._space_copy_target(space, dragged) is not None
        )
        if drop is None:
            return
        target = space.id if space is not None else None
        if drop.copy:
            assert self._paste_dialog is not None
            self._paste_dialog.open(
                drop.dragged.kind, list(drop.dragged.ids), PasteTarget("space", target)
            )
            return
        with self._one_undo_step():
            for ident in drop.dragged.ids:
                if drop.dragged.kind == "space":
                    assert self._on_move_space is not None
                    self._report(self._on_move_space(ident, target))
                elif drop.dragged.kind == "function":
                    assert self._on_move_function is not None and target is not None
                    self._on_move_function(ident, target)
                else:
                    assert self._on_set_device_space is not None
                    self._report(self._on_set_device_space(ident, target))

    def _space_copy_target(
        self, space: "SpaceInfo | None", copied: Dragged
    ) -> PasteTarget | None:
        """Where copies of ``copied`` go when pasted on the space (``None``: the "Buildings"
        root); None when they may not go there: a building part only where its type may go
        (not into itself), a function into any building part, a device into one that holds
        devices."""
        if self._paste_dialog is None:
            return None
        target = PasteTarget("space", space.id if space is not None else None)
        if copied.kind == "space":
            tree = self._get_space_tree()
            parent_type = space.space_type if space is not None else None
            fits = all(
                (node := _find_node(tree, ident)) is not None
                and node.space_type in child_types(parent_type)
                and (space is None or _find_node([node], space.id) is None)
                for ident in copied.ids
            )
            return target if fits else None
        if space is None:
            return None
        if copied.kind == "function" or (
            copied.kind == "device" and holds_devices(space.space_type)
        ):
            return target
        return None

    def _beside_device_target(
        self, space_id: int | None, trade_id: int | None, copied: Dragged
    ) -> PasteTarget | None:
        """Where copies of ``copied`` go when pasted on a device or function in the space
        ``space_id`` or the trade ``trade_id``: there, when they may go there."""
        if space_id is not None:
            space = _find_node(self._get_space_tree(), space_id)
            return self._space_copy_target(space, copied) if space else None
        if trade_id is not None:
            trade = _find_node(self._get_trade_tree(), trade_id)
            return self._trade_copy_target(trade, copied) if trade else None
        return None

    def _render_paste_items(
        self, target_for: Callable[[Dragged], PasteTarget | None]
    ) -> None:
        """ "Paste" and "Paste Special…" in a context menu, with a separator above."""
        if self._paste_dialog is not None:
            imgui.separator()
            self._paste_dialog.render_paste_items(target_for)

    def _space_may_go(
        self, tree: "list[SpaceInfo]", moving_id: int, target: "SpaceInfo | None"
    ) -> bool:
        """Whether the space ``moving_id`` may go below ``target`` (``None``: the top level),
        as "Move to" offers it: where its type may go, not below itself, not where it is."""
        moving = _find_node(tree, moving_id)
        if moving is None or _parent_id(tree, moving_id) == (
            target.id if target is not None else None
        ):
            return False
        if target is None:
            return moving.space_type in child_types(None)
        return any(
            candidate.id == target.id
            for candidate, _depth in self._move_targets(moving_id, moving.space_type)
        )

    def _start_add(self, add_type: str, parent: int | None, parent_name: str) -> None:
        """Open the "Add …" dialog for ``add_type`` (a space type or ``_TRADE``) below
        ``parent`` (named ``parent_name``): rows of how many, their name (empty: the default
        one) and a room's use."""
        trade = add_type == _TRADE
        choice = AddChoice(S.SPACE_USE, usage_options()) if add_type == ROOM else None

        def add(request: AddRequest) -> None:
            self._add_rows(add_type, parent, parent_name, request)

        # A Shift+click adds one with the defaults, without the dialog.
        if imgui.get_io().key_shift:
            add(one_row(choice))
            return
        if trade:
            # Below a trade they are its subtrades.
            title = S.TRADES_TITLE if parent is None else S.TRADES_TITLE_SUBTRADES
        else:
            title = add_title(add_type)
        self._adder.open(
            title,
            parent_name,
            add,
            default_name=S.TRADES_DEFAULT if trade else default_name(add_type),
            choice=choice,
        )

    def _add_rows(
        self, add_type: str, parent: int | None, parent_name: str, request: AddRequest
    ) -> None:
        """Add what the "Add …" dialog asks for, one undo step per row. A room with a use and
        no name is named after its use."""
        trade = add_type == _TRADE
        fallback = S.TRADES_DEFAULT if trade else default_name(add_type)
        several = (
            S.HISTORY_EV_ADDED_TRADES if trade else S.HISTORY_EV_ADDED_BUILDING_PARTS
        )
        for row in request.rows:
            name = row.name.strip()
            if not name and add_type == ROOM and row.choice:
                name = usage_label(row.choice)
            name = name or fallback
            label = (
                several.format(count=row.count, target=parent_name)
                if row.count > 1
                else None
            )
            with self._one_undo_step(label):
                for _ in range(row.count):
                    if trade and self._on_create_trade is not None:
                        self._on_create_trade(parent, name)
                    elif not trade and self._on_create_space is not None:
                        self._on_create_space(parent, add_type, name, row.choice)

    def _render_space(self, space: "SpaceInfo", flt: str = "") -> None:
        if flt and not self._space_has_match(space, flt):
            return  # while filtering, hide spaces with no match anywhere below
        # A space that matches by its own name shows all its contents; otherwise
        # only the matching devices/functions (and matching child spaces).
        self_match = not flt or self._space_matches_self(space, flt)
        if flt:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        # A click selects the space (its details show in place of the device's), the arrow and
        # a double click open it.
        flags = (
            imgui.TreeNodeFlags_.default_open
            | imgui.TreeNodeFlags_.open_on_arrow
            | imgui.TreeNodeFlags_.open_on_double_click
        )
        selected = (
            self._get_selected_space_id() if self._get_selected_space_id else None
        )
        if (
            space.id == selected
            and self._selected_function is None
            and self._selected_link is None
        ):
            flags |= imgui.TreeNodeFlags_.selected
            self._shortcut_row = ShortcutRow(
                Dragged("space", (space.id,)),
                lambda c: self._space_copy_target(space, c),
                on_delete=(
                    (lambda: self._ask_delete_space(space.id))
                    if self._on_remove_space is not None
                    else None
                ),
            )
        open_node = tree_node(f"{space_label(space)}##sp{space.id}", flags)
        if (
            self._on_select_space is not None
            and imgui.is_item_clicked()
            and not imgui.is_item_toggled_open()
        ):
            self._clear_selection()
            self._on_select_space(space.id)
        # The context menu and the space's own popups must run whether the node is expanded or not
        # (a collapsed node can still be moved/deleted), so render them before the return.
        self._render_space_context_menu(space)
        self._drag_and_drop_space(space)
        # A short description shows inline; a longer one shows while its icon is hovered.
        render_description(space.description)
        self._open_if_pending(_delete_space_dialog(space.id))
        self._render_space_delete_confirm(space.id)
        self._open_if_pending(_assign_device_dialog(space.id))
        self._render_assign_device_popup(space.id)
        if not open_node:
            return
        for child in space.children:
            self._render_space(child, flt)
        with leaf_rows():
            for device in space.devices:
                if self_match or self._device_matches(device, flt):
                    self._render_device(device, space_id=space.id)
        for function in space.functions:
            if self_match or self._function_matches(function, flt):
                self._render_function(function, space.id)
        imgui.tree_pop()

    def _clear_selection(self) -> None:
        """Nothing selected in this tree any more but what is selected next."""
        self._selected_function = None
        self._selected_trade = None
        self._selected_link = None

    def _ask_delete_space(self, space_id: int) -> None:
        """Ask before the space ``space_id`` is deleted with everything in it."""
        self._pending_popup_id = _delete_space_dialog(space_id)

    def _start_assign_device(self) -> None:
        self._assign_filter = ""

    def _start_add_functions(self, space: "SpaceInfo") -> None:
        """The "Add" dialog of functions in ``space``: rows of how many, their name (empty:
        the name of their type) and their type."""
        choice = AddChoice(
            S.SPACES_FN_TYPE,
            [(code, label) for label, code in function_type_presets()],
            DEFAULT_FUNCTION_TYPE,
        )
        target = space_label(space)

        def add(request: AddRequest) -> None:
            self._add_functions(space.id, target, request)

        if imgui.get_io().key_shift:
            add(one_row(choice))
            return
        self._adder.open(
            S.SPACES_TITLE_FUNCTIONS,
            target,
            add,
            default_name=S.SPACES_DEFAULT_FUNCTION,
            choice=choice,
        )

    def _add_functions(self, space_id: int, target: str, request: AddRequest) -> None:
        """Add the functions the dialog asks for, one undo step per row. Without a name a
        function is named after its type, a custom one "New function"."""
        if self._on_create_function is None:
            return
        for row in request.rows:
            name = row.name.strip() or default_function_name(row.choice)
            label = (
                S.HISTORY_EV_ADDED_FUNCTIONS.format(count=row.count, target=target)
                if row.count > 1
                else None
            )
            with self._one_undo_step(label):
                for _ in range(row.count):
                    self._on_create_function(space_id, row.choice, name)

    def _open_if_pending(self, popup_id: str) -> None:
        """Open ``popup_id`` here if a context menu requested it. Called at the tree scope, the
        same scope as the matching begin_dialog(), so the ids line up (see _pending_popup_id)."""
        if self._pending_popup_id == popup_id:
            self._pending_popup_id = None
            imgui.open_popup(popup_id)

    def _render_space_context_menu(self, space: "SpaceInfo") -> None:
        editing = (self._on_create_space, self._on_move_space, self._on_remove_space)
        if all(callback is None for callback in editing):  # editing disabled -> no menu
            return
        if not context_menu(f"##spctx{space.id}"):
            return
        self._render_add_menu(space)
        if self._on_move_space is not None:
            # Only where its type may go: a building to the top level, the others below the
            # types that hold them.
            targets: list[tuple[str, int | None]] = []
            if space.space_type in child_types(None):
                targets.append((S.SPACES_MOVE_ROOT, None))
            targets.extend(
                (
                    f"{'  ' * depth}{space_label(target)}##mv{space.id}_{target.id}",
                    target.id,
                )
                for target, depth in self._move_targets(space.id, space.space_type)
            )
            parent = _parent_id(self._get_space_tree(), space.id)
            chosen = render_move_menu(targets, parent)
            if chosen is not None:
                self._report(self._on_move_space(space.id, chosen[0]))
        if self._paste_dialog is not None:
            imgui.separator()
            render_copy_item(Dragged("space", (space.id,)))
            self._paste_dialog.render_paste_items(
                lambda c: self._space_copy_target(space, c)
            )
            imgui.separator()
        if self._on_remove_space is not None and render_delete_item(S.SPACES_DELETE):
            self._ask_delete_space(space.id)
        imgui.end_popup()

    def _render_add_menu(self, space: "SpaceInfo") -> None:
        """The "Add" submenu of a space: the types it holds, then devices (when it holds
        them) and functions."""
        if not imgui.begin_menu(S.SPACES_ADD_MENU):
            return
        if self._on_create_space is not None:
            for space_type in child_types(space.space_type):
                if imgui.menu_item(add_item_text(space_type), "", False)[0]:
                    self._start_add(space_type, space.id, space_label(space))
        if (
            holds_devices(space.space_type)
            and self._on_set_device_space is not None
            and imgui.menu_item(S.SPACES_ADD_DEVICES, "", False)[0]
        ):
            self._start_assign_device()
            self._pending_popup_id = _assign_device_dialog(space.id)
        if (
            self._on_create_function is not None
            and imgui.menu_item(S.SPACES_ADD_FUNCTIONS, "", False)[0]
        ):
            self._start_add_functions(space)
        imgui.end_menu()

    def _report(self, reason: str | None) -> None:
        """Show the reason an edit was refused."""
        if reason:
            self._notify(reason)

    def _move_targets(
        self, moving_id: int, space_type: str | None = None
    ) -> "list[tuple[SpaceInfo, int]]":
        """Flattened (space, depth) candidates to move ``moving_id`` under, excluding the space
        itself and its descendants (which would form a cycle); with ``space_type`` only those
        that hold its type, without it only those that hold devices."""
        out: list[tuple[SpaceInfo, int]] = []

        def fits(target: "SpaceInfo") -> bool:
            if space_type is None:
                return holds_devices(target.space_type)
            return space_type in child_types(target.space_type)

        def walk(spaces: "list[SpaceInfo]", depth: int, blocked: bool) -> None:
            for s in spaces:
                skip = blocked or s.id == moving_id
                if not skip and fits(s):
                    out.append((s, depth))
                walk(s.children, depth + 1, skip)

        walk(self._get_space_tree(), 0, False)
        return out

    def _render_move_device_menu(
        self,
        device_id: int,
        space_id: int | None,
        trade_id: int | None,
        before: Callable[[], object],
    ) -> None:
        """ "Move to" of a device: from a trade into another trade, else (from a building part
        or "Without space") into a building part that holds devices; the others show grayed
        out, for the tree around them. ``before`` runs right before it shows."""
        if trade_id is not None:
            if self._on_add_trade_device is None:
                return
            trades: list[tuple[str, int]] = []

            def walk(nodes: "list[TradeInfo]", depth: int) -> None:
                for trade in nodes:
                    label = f"{trade.number} {trade.name}".strip() or S.TRADES_DEFAULT
                    trades.append((f"{'  ' * depth}{label}##mvdtr{trade.id}", trade.id))
                    walk(trade.children, depth + 1)

            walk(self._get_trade_tree(), 0)
            chosen_trade = render_move_menu(trades, trade_id, before=before)
            if chosen_trade is not None:
                self._on_add_trade_device(chosen_trade[0], device_id)
            return
        if self._on_set_device_space is None:
            return
        spaces = self._all_spaces()
        chosen = render_move_menu(
            [
                (f"{'  ' * depth}{space_label(space)}##mvdev{space.id}", space.id)
                for space, depth in spaces
            ],
            space_id,
            {
                space.id
                for space, _depth in spaces
                if not holds_devices(space.space_type)
            },
            before=before,
        )
        if chosen is not None:
            self._report(self._on_set_device_space(device_id, chosen[0]))

    def _all_spaces(self) -> "list[tuple[SpaceInfo, int]]":
        """Every space with its depth, in tree order."""
        out: list[tuple[SpaceInfo, int]] = []

        def walk(spaces: "list[SpaceInfo]", depth: int) -> None:
            for s in spaces:
                out.append((s, depth))
                walk(s.children, depth + 1)

        walk(self._get_space_tree(), 0)
        return out

    def _ask_delete_device(self, device: "SpaceDeviceInfo") -> None:
        """Ask before ``device`` is deleted from the project."""
        self._delete_device = (device.id, self._device_label(device))
        self._open_delete_device = True

    def _render_delete_device_confirm(self) -> None:
        """ "Delete Device": asks before a device is deleted from the project."""
        if self._open_delete_device:
            imgui.open_popup(S.DEVICE_DELETE_TITLE)
            self._open_delete_device = False
        if not begin_dialog(S.DEVICE_DELETE_TITLE, width=420.0):
            return
        device = self._delete_device
        imgui.text_wrapped(
            S.DEVICE_DELETE_CONFIRM.format(name=device[1] if device else "")
        )
        clicked = dialog_buttons(S.CONTEXT_DELETE, S.BTN_CANCEL)
        if clicked == 0:
            if device is not None and self._on_delete_device is not None:
                self._on_delete_device(device[0])
            self._delete_device = None
            imgui.close_current_popup()
        elif clicked == 1:
            self._delete_device = None
            imgui.close_current_popup()
        end_dialog()

    def _render_space_delete_confirm(self, space_id: int) -> None:
        if self._on_remove_space is None or not begin_dialog(
            _delete_space_dialog(space_id), width=420.0
        ):
            return
        imgui.text_wrapped(S.SPACES_DELETE_CONFIRM)
        clicked = dialog_buttons(S.SPACES_DELETE, S.ML_CANCEL)
        if clicked == 0:
            self._on_remove_space(space_id)
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_assign_device_popup(self, space_id: int) -> None:
        if self._on_set_device_space is None or self._get_unassigned_devices is None:
            return
        if not begin_dialog(_assign_device_dialog(space_id)):
            return
        self._assign_filter = filter_box(
            f"##assignflt{space_id}", S.SPACES_FILTER_HINT, self._assign_filter
        )
        needle = self._assign_filter.strip().lower()
        devices = self._get_unassigned_devices()
        if imgui.begin_child("##assignlist", px_vec2(320.0, 260.0)):
            if not devices:
                imgui.text_disabled(S.SPACES_ASSIGN_EMPTY)
            for device in devices:
                label = self._device_label(device)
                if needle and needle not in label.lower():
                    continue
                if imgui.selectable(
                    f"{label}##assignpick{space_id}_{device.id}", False
                )[0]:
                    self._report(self._on_set_device_space(device.id, space_id))
                    imgui.close_current_popup()
        imgui.end_child()
        if dialog_buttons(S.ML_CANCEL) == 0:
            imgui.close_current_popup()
        end_dialog()

    def _render_unassigned_section(self) -> None:
        if self._on_set_device_space is None or self._get_unassigned_devices is None:
            return
        devices = self._get_unassigned_devices()
        if not devices:
            return
        if not tree_node(f"{S.SPACES_UNASSIGNED_TITLE} ({len(devices)})##unassigned"):
            return
        # A device goes into a building part by its "Move to", by drag and drop, or by the
        # building part's "Add → Devices…".
        with leaf_rows():
            for device in devices:
                self._render_device(device)
        imgui.tree_pop()

    def _device_label(self, device: "SpaceDeviceInfo") -> str:
        # On a line without a device number: its line ("1.5.-").
        address = device.individual_address or self._get_parked_address(device.id)
        ia = f"{address}  " if address else ""
        # Unnamed: the product/hardware name; the description shows behind its icon only.
        primary = device.name or device.product_name or device.hardware_name or "?"
        return f"{ia}{primary}".strip()

    @staticmethod
    def _space_matches_self(space: "SpaceInfo", flt: str) -> bool:
        return (
            flt in (space.name or "").lower() or flt in (space.space_type or "").lower()
        )

    @staticmethod
    def _device_matches(device: "SpaceDeviceInfo", flt: str) -> bool:
        fields = (
            device.name,
            device.individual_address,
            device.product_name,
            device.hardware_name,
            device.manufacturer_name,
        )
        return any(flt in (f or "").lower() for f in fields)

    @staticmethod
    def _function_matches(function: "FunctionInfo", flt: str) -> bool:
        fields = (function.usage_text, function.name, function.function_type)
        return any(flt in (f or "").lower() for f in fields)

    def _space_has_match(self, space: "SpaceInfo", flt: str) -> bool:
        if self._space_matches_self(space, flt):
            return True
        if any(self._device_matches(d, flt) for d in space.devices):
            return True
        if any(self._function_matches(fn, flt) for fn in space.functions):
            return True
        return any(self._space_has_match(child, flt) for child in space.children)

    def _render_device(
        self,
        device: "SpaceDeviceInfo",
        space_id: int | None = None,
        trade_id: int | None = None,
    ) -> None:
        detail = device.description
        leaf = self._device_label(device)
        selected = (
            self._get_selected_node_id is not None
            and self._get_selected_node_id() == device.id
        )
        # The same device can be in a space and in trades: the ids stay apart per place.
        where = f"t{trade_id}" if trade_id is not None else ""
        if selected and self._shortcut_row is None:
            self._shortcut_row = ShortcutRow(
                Dragged("device", (device.id,)),
                lambda c: self._beside_device_target(space_id, trade_id, c),
                on_delete=(
                    (lambda: self._ask_delete_device(device))
                    if self._on_delete_device is not None
                    else None
                ),
            )
        if imgui.selectable(f"{leaf}##spdev{where}{device.id}", selected)[0]:
            self._clear_selection()
            self._on_select_device_id(device.id)
        hovered = imgui.is_item_hovered()  # capture before the context menu below
        drag_source(Dragged("device", (device.id,)), leaf)
        has_menu = (
            bool(device.individual_address)
            or (space_id is not None and self._on_set_device_space is not None)
            or (trade_id is not None and self._on_remove_trade_device is not None)
            or self._on_copy_config is not None
            or self._on_compare is not None
            or self._paste_dialog is not None
            or self._on_delete_device is not None
        )
        if has_menu and context_menu(f"##spdev_ctx_{where}{device.id}"):
            # As in the Topology tree: its configuration; where it is and its address; reading,
            # comparing and unlinking; then Copy / Cut / Paste and "Delete".
            groups = MenuGroups()
            if self._on_copy_config is not None and groups.show():
                config_label = self._device_label(device)
                if imgui.menu_item(S.CONTEXT_DUPLICATE, "", False)[0]:
                    self._config_dialog.open(device.id, config_label, "duplicate")
                if imgui.menu_item(S.CONTEXT_COPY_CONFIG, "", False)[0]:
                    self._on_copy_config(device.id)
                if imgui.menu_item(
                    S.CONTEXT_PASTE_CONFIG,
                    "",
                    False,
                    self._can_paste_config_cb(device.id)
                    if self._can_paste_config_cb is not None
                    else False,
                )[0]:
                    self._config_dialog.open(device.id, config_label, "paste")
            groups.next()
            self._render_move_device_menu(device.id, space_id, trade_id, groups.show)
            render_copy_device_address_item(device.individual_address, groups.show)
            groups.next()
            if self._on_device_info is not None and groups.show():
                render_read_device_item(
                    device.id, bool(device.individual_address), self._on_device_info
                )
            if (
                self._on_compare is not None
                and groups.show()
                and imgui.menu_item(S.CONTEXT_COMPARE_DEVICE, "", False)[0]
            ):
                self._on_compare(device.id)
            # "Unlink" takes the device out of the trade or the building part it shows in.
            if (
                trade_id is not None
                and self._on_remove_trade_device is not None
                and groups.show()
                and imgui.menu_item(S.TRADES_REMOVE_DEVICE, "", False)[0]
            ):
                self._on_remove_trade_device(trade_id, device.id)
            if (
                space_id is not None
                and self._on_set_device_space is not None
                and groups.show()
                and imgui.menu_item(S.SPACES_UNASSIGN, "", False)[0]
            ):
                self._report(self._on_set_device_space(device.id, None))
            groups.next()
            if self._paste_dialog is not None and groups.show():
                render_copy_item(Dragged("device", (device.id,)))
                # What is pasted on a device goes where the device is.
                self._paste_dialog.render_paste_items(
                    lambda c: self._beside_device_target(space_id, trade_id, c)
                )
            groups.next()
            if (
                self._on_delete_device is not None
                and groups.show()
                and render_delete_item()
            ):
                self._ask_delete_device(device)
            imgui.end_popup()
        # The KNX Data Secure shield, then a short description inline or a longer one while its
        # icon is hovered.
        if render_device_marks(device.id, detail):
            hovered = False
        if hovered:
            parts = [
                p
                for p in (
                    device.manufacturer_name,
                    device.product_name,
                    device.hardware_name,
                )
                if p
            ]
            if parts:
                imgui.set_tooltip("\n".join(parts))

    # -- trades -----------------------------------------------------------------------------

    def _render_trades(self, flt: str) -> None:
        """The "Trades" root: the trades, each with its sub-trades and devices."""
        trades = self._get_trade_tree()
        open_root = tree_node(
            f"{S.SPACES_ROOT_TRADES}##trades_root", imgui.TreeNodeFlags_.default_open
        )
        if self._on_create_trade is not None and context_menu("##trades_root_ctx"):
            if imgui.menu_item(S.TRADES_ADD, "", False)[0]:
                self._start_add(_TRADE, None, S.SPACES_ROOT_TRADES)
            self._render_paste_items(lambda c: self._trade_copy_target(None, c))
            imgui.end_popup()
        self._drop_on_trade(None)
        if not open_root:
            return
        for trade in trades:
            self._render_trade(trade, flt)
        imgui.tree_pop()

    def _trade_has_match(self, trade: "TradeInfo", flt: str) -> bool:
        return (
            flt in trade.name.lower()
            or any(self._device_matches(d.device, flt) for d in trade.devices)
            or any(self._trade_has_match(child, flt) for child in trade.children)
        )

    def _render_trade(self, trade: "TradeInfo", flt: str) -> None:
        if flt and not self._trade_has_match(trade, flt):
            return
        if flt:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        # A click selects the trade (its details show in place of the device's), the arrow and
        # a double click open it.
        flags = (
            imgui.TreeNodeFlags_.open_on_arrow
            | imgui.TreeNodeFlags_.open_on_double_click
        )
        if self._selected_trade == trade.id:
            flags |= imgui.TreeNodeFlags_.selected
            remove = self._on_remove_trade
            self._shortcut_row = ShortcutRow(
                Dragged("trade", (trade.id,)),
                lambda c: self._trade_copy_target(trade, c),
                on_delete=(lambda: remove(trade.id)) if remove is not None else None,
            )
        label = f"{trade.number} {trade.name}".strip() or S.TRADES_DEFAULT
        open_node = tree_node(f"{label}##trade{trade.id}", flags)
        if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
            self._clear_selection()
            self._selected_trade = trade.id
            self._on_select_element("trade", trade.id)
        self._render_trade_context_menu(trade, label)
        if self._on_move_trade is not None:
            drag_source(Dragged("trade", (trade.id,)), label)
        self._drop_on_trade(trade)
        render_description(trade.description)
        self._open_if_pending(_trade_devices_dialog(trade.id))
        self._render_trade_devices_popup(trade)
        if not open_node:
            return
        for child in trade.children:
            self._render_trade(child, flt)
        with leaf_rows():
            for link in trade.devices:
                if not flt or self._device_matches(link.device, flt):
                    self._render_device(link.device, trade_id=trade.id)
        imgui.tree_pop()

    def _render_trade_context_menu(self, trade: "TradeInfo", label: str) -> None:
        if self._on_create_trade is None or not context_menu(f"##tradectx{trade.id}"):
            return
        if imgui.begin_menu(S.SPACES_ADD_MENU):
            if imgui.menu_item(S.TRADES_ADD_TRADES, "", False)[0]:
                self._start_add(_TRADE, trade.id, label)
            if (
                self._on_add_trade_device is not None
                and imgui.menu_item(S.SPACES_ADD_DEVICES, "", False)[0]
            ):
                self._trade_device_filter = ""
                self._pending_popup_id = _trade_devices_dialog(trade.id)
            imgui.end_menu()
        if self._on_move_trade is not None:
            targets: list[tuple[str, int | None]] = [(S.SPACES_MOVE_ROOT, None)]
            targets.extend(
                (f"{'  ' * depth}{target.name}##mvtr{trade.id}_{target.id}", target.id)
                for target, depth in self._trade_targets(trade.id)
            )
            parent = _parent_id(self._get_trade_tree(), trade.id)
            chosen = render_move_menu(targets, parent)
            if chosen is not None:
                self._on_move_trade(trade.id, chosen[0])
        if self._paste_dialog is not None:
            imgui.separator()
            render_copy_item(Dragged("trade", (trade.id,)))
            self._paste_dialog.render_paste_items(
                lambda c: self._trade_copy_target(trade, c)
            )
            imgui.separator()
        if self._on_remove_trade is not None and render_delete_item(S.SPACES_DELETE):
            self._on_remove_trade(trade.id)
        imgui.end_popup()

    def _trade_copy_target(
        self, trade: "TradeInfo | None", copied: Dragged
    ) -> PasteTarget | None:
        """Where copies of ``copied`` go when pasted on the trade (``None``: the "Trades"
        root); None when they may not go there: a trade anywhere but into itself or a trade
        below it, a device into a trade."""
        if self._paste_dialog is None:
            return None
        target = PasteTarget("trade", trade.id if trade is not None else None)
        if copied.kind == "trade":
            trades = self._get_trade_tree()
            fits = trade is None or all(
                (node := _find_node(trades, ident)) is not None
                and _find_node([node], trade.id) is None
                for ident in copied.ids
            )
            return target if fits else None
        return target if copied.kind == "device" and trade is not None else None

    def _drop_on_trade(self, trade: "TradeInfo | None") -> None:
        """The trade just drawn (``None``: the "Trades" root) as a place to drop on: the trades
        that may go below it, and devices (a device leaves the trade it was in)."""
        trades = self._get_trade_tree()

        def accepts(dragged: Dragged) -> bool:
            if dragged.kind == "trade":
                target = trade.id if trade is not None else None
                return self._on_move_trade is not None and all(
                    _parent_id(trades, moving) != target
                    and (
                        trade is None
                        or any(
                            t.id == trade.id for t, _d in self._trade_targets(moving)
                        )
                    )
                    for moving in dragged.ids
                )
            if dragged.kind != "device" or trade is None:
                return False
            here = {link.device.id for link in trade.devices}
            return self._on_add_trade_device is not None and not here.issuperset(
                dragged.ids
            )

        drop = drop_target(
            accepts, lambda dragged: self._trade_copy_target(trade, dragged) is not None
        )
        if drop is None:
            return
        if drop.copy:
            assert self._paste_dialog is not None
            self._paste_dialog.open(
                drop.dragged.kind,
                list(drop.dragged.ids),
                PasteTarget("trade", trade.id if trade is not None else None),
            )
            return
        with self._one_undo_step():
            for ident in drop.dragged.ids:
                if drop.dragged.kind == "trade":
                    assert self._on_move_trade is not None
                    self._on_move_trade(ident, trade.id if trade is not None else None)
                else:
                    assert self._on_add_trade_device is not None and trade is not None
                    self._on_add_trade_device(trade.id, ident)

    def _trade_targets(self, moving_id: int) -> "list[tuple[TradeInfo, int]]":
        """The trades ``moving_id`` can go below: all but itself and the trades below it."""
        out: list[tuple[TradeInfo, int]] = []

        def walk(trades: "list[TradeInfo]", depth: int) -> None:
            for trade in trades:
                if trade.id == moving_id:
                    continue
                out.append((trade, depth))
                walk(trade.children, depth + 1)

        walk(self._get_trade_tree(), 0)
        return out

    def _render_trade_devices_popup(self, trade: "TradeInfo") -> None:
        """ "Add Devices" of a trade: the project's devices not in it yet; a click adds one."""
        if self._on_add_trade_device is None or not begin_dialog(
            _trade_devices_dialog(trade.id)
        ):
            return
        self._trade_device_filter = filter_box(
            f"##tradedevflt{trade.id}", S.SPACES_FILTER_HINT, self._trade_device_filter
        )
        needle = self._trade_device_filter.strip().lower()
        inside = {link.device.id for link in trade.devices}
        if imgui.begin_child("##tradedevlist", px_vec2(320.0, 260.0)):
            for device in self._get_space_devices():
                label = self._device_label(device)
                if device.id in inside or (needle and needle not in label.lower()):
                    continue
                if imgui.selectable(f"{label}##tradepick{trade.id}_{device.id}", False)[
                    0
                ]:
                    self._on_add_trade_device(trade.id, device.id)
        imgui.end_child()
        if dialog_buttons(S.ML_CANCEL) == 0:
            imgui.close_current_popup()
        end_dialog()

    def _render_function(self, function: "FunctionInfo", space_id: int) -> None:
        label = _function_label(function)
        type_label = function_type_label(function.function_type)
        # A click selects the function, the arrow and a double click open it.
        flags = (
            imgui.TreeNodeFlags_.open_on_arrow
            | imgui.TreeNodeFlags_.open_on_double_click
        )
        if not function.group_addresses:
            flags |= imgui.TreeNodeFlags_.leaf
        if self._selected_function == function.id:
            flags |= imgui.TreeNodeFlags_.selected
            remove = self._on_remove_function
            self._shortcut_row = ShortcutRow(
                Dragged("function", (function.id,)),
                lambda c: self._beside_device_target(space_id, None, c),
                on_delete=(lambda: remove(function.id)) if remove is not None else None,
            )
        open_node = tree_node(f"ƒ {label}  [{type_label}]##fn{function.id}", flags)
        if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
            self._clear_selection()
            self._selected_function = function.id
            self._on_select_element("function", function.id)
        self._render_function_context_menu(function, space_id)
        if self._on_move_function is not None:
            drag_source(Dragged("function", (function.id,)), label)
        render_description(function.description)
        self._open_if_pending(_link_function_dialog(function.id))
        self._render_link_dialog(function.id)
        if not open_node:
            return
        with leaf_rows():
            for ref in function.group_addresses:
                self._render_function_link(ref)
        imgui.tree_pop()

    def _render_function_link(self, ref: "FunctionGroupAddressInfo") -> None:
        """A group address a function is linked with: a click selects the group address (its
        details show), its menu's "Delete" (or the Delete key) unlinks it."""
        role = f"  ({ref.role})" if ref.role else ""
        text = f"{ref.text}  {ref.name}".strip() + role
        selected = self._selected_link == ref.id and self._get_selected_element() in (
            None,
            ("group_address", ref.group_address_id),
        )
        remove = self._on_remove_function_ga
        if selected:
            self._shortcut_row = ShortcutRow(
                None,
                no_paste,
                on_delete=(lambda: remove(ref.id)) if remove is not None else None,
            )
        if imgui.selectable(f"{text}##fnga{ref.id}", selected)[0]:
            self._clear_selection()
            self._selected_link = ref.id
            self._on_select_element("group_address", ref.group_address_id)
        if remove is not None and context_menu(f"##fnga_ctx{ref.id}"):
            render_copy_group_address_item(ref.text)
            imgui.separator()
            if render_delete_item():
                remove(ref.id)
            imgui.end_popup()

    def _render_function_context_menu(
        self, function: "FunctionInfo", space_id: int
    ) -> None:
        editable = (
            self._on_move_function is not None
            or self._on_remove_function is not None
            or self._on_add_function_ga is not None
            or self._paste_dialog is not None
        )
        if editable and context_menu(f"##fnctx{function.id}"):
            if self._on_add_function_ga is not None:
                if imgui.menu_item(S.SPACES_FN_LINK, "", False)[0]:
                    self._link_filter = ""
                    self._pending_popup_id = _link_function_dialog(function.id)
                tree = self._get_ga_range_tree() if self._get_ga_range_tree else []
                if self._on_create_ga_in is not None and add_menu_item(
                    S.GA_ADD_GROUP_ADDRESSES,
                    ""
                    if address_groups(tree, self._group_style())
                    else S.SPACES_FN_NO_GROUP,
                ):
                    self._start_add_function_gas(function)
            if self._on_move_function is not None:
                # Into any other space.
                targets = [
                    (
                        f"{'  ' * depth}{space_label(target)}##mvfn{function.id}_{target.id}",
                        target.id,
                    )
                    for target, depth in self._all_spaces()
                ]
                chosen = render_move_menu(targets, space_id)
                if chosen is not None:
                    self._on_move_function(function.id, chosen[0])
            if self._paste_dialog is not None:
                imgui.separator()
                render_copy_item(Dragged("function", (function.id,)))
                # What is pasted on a function goes into its building part.
                self._paste_dialog.render_paste_items(
                    lambda c: self._beside_device_target(space_id, None, c)
                )
            if self._on_remove_function is not None:
                imgui.separator()
                if render_delete_item(S.SPACES_FN_DELETE):
                    self._on_remove_function(function.id)
            imgui.end_popup()

    def _start_add_function_gas(self, function: "FunctionInfo") -> None:
        """ "Add Group Addresses…" of a function: the group addresses dialog of a group, with
        the group each row's go into (at first the group of the function's first group
        address); the new ones are linked with the function, in the row's undo step."""
        create = self._on_create_ga_in
        link = self._on_add_function_ga
        tree = self._get_ga_range_tree() if self._get_ga_range_tree else []
        style = self._group_style()
        groups = address_groups(tree, style)
        if create is None or link is None or not groups:
            self._notify(S.SPACES_FN_NO_GROUP)
            return
        linked = {ref.group_address_id for ref in function.group_addresses}
        first = next(
            (g for g in groups if any(ga.id in linked for ga in g.group_addresses)),
            groups[0],
        )
        choice = AddChoice(
            S.SPACES_FN_GA_GROUP,
            [(str(g.id), group_text(g)) for g in groups],
            str(first.id),
        )
        free = style is GroupAddressStyle.FREE
        target = _function_label(function)

        def add(request: AddRequest) -> None:
            now = self._get_ga_range_tree() if self._get_ga_range_tree else []
            by_id = {g.id: g for g in address_groups(now, style)}

            def group_of(row: AddRow) -> Any:
                return by_id.get(int(row.choice)) if row.choice.isdigit() else None

            def create_linked(group_id: int, address: int, name: str) -> None:
                ga_id = create(group_id, address, name)
                if ga_id is not None:
                    link(function.id, ga_id, "")

            add_group_addresses(
                request,
                group_of,
                now,
                style,
                create_linked,
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

    def _render_link_dialog(self, function_id: int) -> None:
        """ "Link with Group Address": the group addresses to pick one from."""
        if not begin_dialog(_link_function_dialog(function_id)):
            return
        self._link_filter = filter_box(
            f"##fngaflt{function_id}", S.GA_FILTER_HINT, self._link_filter
        )
        needle = self._link_filter.strip().lower()
        if (
            imgui.begin_child("##fngalist", px_vec2(320.0, 260.0))
            and self._get_ga_range_tree is not None
        ):
            for node in self._get_ga_range_tree():
                self._render_ga_pick_range(function_id, node, needle)
        imgui.end_child()
        if dialog_buttons(S.ML_CANCEL) == 0:
            imgui.close_current_popup()
        end_dialog()

    def _render_ga_pick_range(self, function_id: int, node: Any, needle: str) -> None:
        if needle and not self._range_has_ga(node, needle):
            return
        if needle:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        if not imgui.tree_node_ex(f"{group_text(node)}##fngr{node.id}"):
            return
        for child in node.children:
            self._render_ga_pick_range(function_id, child, needle)
        for ga in node.group_addresses:
            text = f"{ga.text}  {ga.name}"
            if needle and needle not in text.lower():
                continue
            if imgui.selectable(f"{text}##fngapick{function_id}_{ga.id}", False)[0]:
                if self._on_add_function_ga is not None:
                    self._on_add_function_ga(function_id, ga.id, "")
                imgui.close_current_popup()
        imgui.tree_pop()

    @staticmethod
    def _range_has_ga(node: Any, needle: str) -> bool:
        if any(needle in f"{g.text}  {g.name}".lower() for g in node.group_addresses):
            return True
        return any(SpacesPanel._range_has_ga(child, needle) for child in node.children)

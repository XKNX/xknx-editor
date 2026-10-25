"""Buildings view: the imported location tree (building -> floor -> room -> ...) with the
devices placed in each space and the functions assigned to it."""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui

from editor_gui.plugins.project.element_status import usage_options
from editor_gui.plugins.project.space_text import (
    add_item_text,
    add_title,
    default_name,
    space_type_name,
)
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.config_dialog import DeviceConfigDialog
from editor_gui.plugins.project.ui.move_menu import render_move_menu
from editor_gui.plugins.project.ui.status_field import USAGE_ROWS as _USAGE_ROWS
from editor_gui.plugins.project.ui.topology import render_read_device_item
from editor_gui.widgets.combo import combo
from editor_gui.widgets.description import render_description, render_device_marks
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px, px_vec2
from editor_gui.widgets.tree import begin_tree_area, leaf_rows, tree_node
from xknxeditor.proj.core.space_rules import ROOM, child_types, holds_devices

if TYPE_CHECKING:
    from xknxeditor.proj.core.service import (
        FunctionInfo,
        SpaceDeviceInfo,
        SpaceInfo,
        TradeInfo,
    )


def _function_type_presets() -> list[tuple[str, str]]:
    """(label, KNX FunctionType code) for the type picker: every current (non-deprecated) type
    of knx_master.xml, in its order; role stays free text since group addresses are accepted
    without one."""
    return [
        (S.SPACES_FN_TYPE_CUSTOM, "FT-0"),
        (S.SPACES_FN_TYPE_SWITCH, "FT-1"),
        (S.SPACES_FN_TYPE_DIM, "FT-6"),
        (S.SPACES_FN_TYPE_SUN_PROTECTION, "FT-7"),
        (S.SPACES_FN_TYPE_HEATING_SWITCHING, "FT-8"),
        (S.SPACES_FN_TYPE_HEATING_CONTINUOUS, "FT-9"),
        (S.SPACES_FN_TYPE_SOCKET, "FT-10"),
    ]


# The type a new function starts with: a switchable light.
_DEFAULT_FUNCTION_TYPE = 1


def _function_type_label(code: str) -> str:
    for label, c in _function_type_presets():
        if c == code:
            return label
    return code or S.SPACES_FN_TYPE_CUSTOM


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


def _add_dialog(key: str) -> str:
    """The "Add …" dialog of the target ``key`` ("root", "trades", a space or a trade)."""
    return f"###add_{key}"


def _trade_devices_dialog(trade_id: int) -> str:
    return f"{S.TRADES_ADD_DEVICES_TITLE}###tradedev{trade_id}"


def _delete_space_dialog(space_id: int) -> str:
    return f"{S.SPACES_DELETE_TITLE}###delspace{space_id}"


def _assign_device_dialog(space_id: int) -> str:
    return f"{S.SPACES_ASSIGN_TITLE}###assigndev{space_id}"


def _new_function_dialog(space_id: int) -> str:
    return f"{S.SPACES_FN_NEW_TITLE}###newfn{space_id}"


def _rename_function_dialog(function_id: int) -> str:
    return f"{S.SPACES_FN_RENAME_TITLE}###renfn{function_id}"


def _add_function_ga_dialog(function_id: int) -> str:
    return f"{S.SPACES_FN_ADD_GA}###addfnga{function_id}"


class SpacesPanel:
    def __init__(
        self,
        get_space_tree: "Callable[[], list[SpaceInfo]]",
        on_select_device_id: Callable[[int], None],
        on_create_function: Callable[[int, str, str], object] | None = None,
        on_remove_function: Callable[[int], None] | None = None,
        on_rename_function: Callable[[int, str], None] | None = None,
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
    ) -> None:
        """``notify`` shows the reason an edit was refused."""
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
        # The "Add …" dialog: the type it adds (a space type or a trade), where, how many, the
        # name (empty: the default one) and a room's use (index into usage_options()).
        self._add_type = ""
        self._add_parent: int | None = None
        self._add_parent_name = ""
        self._add_count = 1
        self._add_name = ""
        self._add_usage = 0
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
        self._on_rename_function = on_rename_function
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
        # "New function" form state (one dialog at a time; keyed by the target space).
        self._new_fn_space: int | None = None
        self._new_fn_name = ""
        self._new_fn_type = (
            _DEFAULT_FUNCTION_TYPE  # index into _function_type_presets()
        )
        # "Rename function" + "assign group address" dialog state.
        self._rename_fn: int | None = None
        self._rename_buf = ""
        self._addga_fn: int | None = None
        self._addga_role = ""
        self._addga_filter = ""
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
        self._render_tree(tree, flt)
        imgui.end_child()
        self._config_dialog.render()

    def _render_tree(self, tree: list["SpaceInfo"], flt: str) -> None:
        """The buildings and trades below the filter box, with their menus and dialogs."""
        # "Buildings" holds the buildings, "Trades" the trades: both open by default.
        flags = imgui.TreeNodeFlags_.default_open
        buildings_open = tree_node(f"{S.SPACES_ROOT_BUILDINGS}##spaces_root", flags)
        if self._on_create_space is not None and imgui.begin_popup_context_item(
            "##spaces_root_ctx"
        ):
            if imgui.menu_item(S.SPACES_ADD_BUILDINGS, "", False)[0]:
                self._start_add(child_types(None)[0], None, S.SPACES_ROOT_BUILDINGS)
                self._pending_popup_id = _add_dialog("root")
            imgui.end_popup()
        self._open_if_pending(_add_dialog("root"))
        self._render_add_popup(_add_dialog("root"))
        if buildings_open:
            if not tree:
                imgui.text_disabled(S.SPACES_EMPTY)
            for space in tree:
                self._render_space(space, flt)
            imgui.tree_pop()
        self._render_trades(flt)
        self._render_unassigned_section()

    def _start_add(self, add_type: str, parent: int | None, parent_name: str) -> None:
        """Prepare the "Add …" dialog for ``add_type`` (a space type or ``_TRADE``) below
        ``parent``."""
        self._add_type = add_type
        self._add_parent = parent
        self._add_parent_name = parent_name
        self._add_count = 1
        self._add_name = ""
        self._add_usage = 0

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
        if space.id == selected and self._selected_function is None:
            flags |= imgui.TreeNodeFlags_.selected
        open_node = tree_node(f"{space_label(space)}##sp{space.id}", flags)
        if (
            self._on_select_space is not None
            and imgui.is_item_clicked()
            and not imgui.is_item_toggled_open()
        ):
            self._selected_function = None
            self._selected_trade = None
            self._on_select_space(space.id)
        # The context menu and the space's own popups must run whether the node is expanded or not
        # (a collapsed node can still be moved/deleted), so render them before the return.
        self._render_space_context_menu(space)
        # A short description shows inline; a longer one shows while its icon is hovered.
        render_description(space.description)
        self._open_if_pending(_add_dialog(f"sp{space.id}"))
        self._render_add_popup(_add_dialog(f"sp{space.id}"))
        self._open_if_pending(_delete_space_dialog(space.id))
        self._render_space_delete_confirm(space.id)
        self._open_if_pending(_assign_device_dialog(space.id))
        self._render_assign_device_popup(space.id)
        self._open_if_pending(_new_function_dialog(space.id))
        self._render_new_function_popup(space.id)
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
                self._render_function(function)
        devices_here = holds_devices(space.space_type)
        if (
            devices_here
            and self._on_set_device_space is not None
            and imgui.small_button(f"{S.SPACES_ASSIGN_DEVICE}##assign{space.id}")
        ):
            self._start_assign_device()
            imgui.open_popup(_assign_device_dialog(space.id))
        if self._on_create_function is not None:
            if devices_here and self._on_set_device_space is not None:
                imgui.same_line()
            if imgui.small_button(f"{S.SPACES_ADD_FUNCTION}##addfn{space.id}"):
                self._start_new_function(space.id)
                imgui.open_popup(_new_function_dialog(space.id))
        imgui.tree_pop()

    def _start_assign_device(self) -> None:
        self._assign_filter = ""

    def _start_new_function(self, space_id: int) -> None:
        self._new_fn_space = space_id
        self._new_fn_name = ""
        self._new_fn_type = _DEFAULT_FUNCTION_TYPE

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
        if not imgui.begin_popup_context_item(f"##spctx{space.id}"):
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
        if (
            self._on_remove_space is not None
            and imgui.menu_item(S.SPACES_DELETE, "", False)[0]
        ):
            self._pending_popup_id = _delete_space_dialog(space.id)
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
                    self._pending_popup_id = _add_dialog(f"sp{space.id}")
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
            self._start_new_function(space.id)
            self._pending_popup_id = _new_function_dialog(space.id)
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

    def _render_add_popup(self, popup_id: str) -> None:
        """The "Add …" dialog: how many to add (Count), their name (empty: the default one)
        and a room's use; they go below the target named under the title."""
        trade = self._add_type == _TRADE
        title = S.TRADES_TITLE if trade else add_title(self._add_type or ROOM)
        if not begin_dialog(f"{title}{popup_id}"):
            return
        imgui.text_disabled(S.SPACES_ADD_TO.format(name=self._add_parent_name))
        imgui.set_next_item_width(px(220.0))
        _, self._add_count = imgui.input_int(S.SPACES_COUNT, self._add_count)
        self._add_count = max(1, min(self._add_count, 100))
        fallback = S.TRADES_DEFAULT if trade else default_name(self._add_type)
        imgui.set_next_item_width(px(220.0))
        _, self._add_name = imgui.input_text_with_hint(
            S.SPACES_SPACE_NAME, fallback, self._add_name
        )
        uses = usage_options()
        if self._add_type == ROOM:
            # A room's use, "-" (none) by default.
            imgui.set_next_item_width(px(220.0))
            _, self._add_usage = combo(
                S.SPACE_USE,
                self._add_usage,
                [text for _usage, text in uses],
                _USAGE_ROWS,
            )
        clicked = dialog_buttons(S.SPACES_SPACE_CREATE, S.ML_CANCEL)
        if clicked == 0:
            name = self._add_name.strip() or fallback
            for _ in range(self._add_count):
                if trade and self._on_create_trade is not None:
                    self._on_create_trade(self._add_parent, name)
                elif not trade and self._on_create_space is not None:
                    usage = uses[self._add_usage][0] if self._add_type == ROOM else ""
                    self._on_create_space(self._add_parent, self._add_type, name, usage)
            imgui.close_current_popup()
        elif clicked == 1:
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
        with leaf_rows():
            self._render_unassigned_devices(devices)
        imgui.tree_pop()

    def _render_unassigned_devices(self, devices: list["SpaceDeviceInfo"]) -> None:
        assert self._on_set_device_space is not None
        # -1: no space excluded; every space that holds devices is a target.
        targets = self._move_targets(-1)
        for device in devices:
            # A selectable (not plain text) so the device opens in the editor on click, exactly
            # like a device shown inside a room.
            self._render_device(device)
            if not targets:
                continue
            imgui.same_line()
            if imgui.small_button(f"{S.SPACES_ASSIGN_DEVICE}##uassign{device.id}"):
                imgui.open_popup(f"##uassignto{device.id}")
            if imgui.begin_popup(f"##uassignto{device.id}"):
                for target, depth in targets:
                    indent = "  " * depth
                    label = f"{indent}{space_label(target)}"
                    if imgui.menu_item(
                        f"{label}##uto{device.id}_{target.id}", "", False
                    )[0]:
                        self._report(self._on_set_device_space(device.id, target.id))
                        imgui.close_current_popup()
                imgui.end_popup()

    @staticmethod
    def _device_label(device: "SpaceDeviceInfo") -> str:
        ia = f"{device.individual_address}  " if device.individual_address else ""
        # Unnamed: the product/hardware name; the description shows behind its icon only.
        primary = device.name or device.product_name or device.hardware_name or "?"
        return f"{ia}{primary}".strip()

    def _render_new_function_popup(self, space_id: int) -> None:
        if not begin_dialog(_new_function_dialog(space_id)):
            return
        imgui.set_next_item_width(px(220.0))
        _, self._new_fn_name = imgui.input_text(S.SPACES_FN_NAME, self._new_fn_name)
        presets = _function_type_presets()
        imgui.set_next_item_width(px(220.0))
        _, self._new_fn_type = combo(
            S.SPACES_FN_TYPE, self._new_fn_type, [label for label, _ in presets]
        )
        can_create = bool(self._new_fn_name.strip())
        clicked = dialog_buttons(
            S.SPACES_FN_CREATE, S.ML_CANCEL, enabled=[can_create, True]
        )
        if clicked == 0 and self._on_create_function is not None:
            _, code = presets[self._new_fn_type]
            self._on_create_function(space_id, code, self._new_fn_name.strip())
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

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
        # allow_overlap: the "Without space" section draws an "Assign device" button via
        # same_line() over this full-width selectable. Without the flag the selectable (submitted
        # first) claims the click and the button never fires.
        if imgui.selectable(
            f"{leaf}##spdev{where}{device.id}",
            selected,
            imgui.SelectableFlags_.allow_overlap,
        )[0]:
            self._selected_function = None
            self._selected_trade = None
            self._on_select_device_id(device.id)
        hovered = imgui.is_item_hovered()  # capture before the context menu below
        has_menu = (
            bool(device.individual_address)
            or (space_id is not None and self._on_set_device_space is not None)
            or (trade_id is not None and self._on_remove_trade_device is not None)
            or self._on_copy_config is not None
            or self._on_compare is not None
        )
        if has_menu and imgui.begin_popup_context_item(
            f"##spdev_ctx_{where}{device.id}"
        ):
            if (
                trade_id is not None
                and self._on_remove_trade_device is not None
                and imgui.menu_item(S.TRADES_REMOVE_DEVICE, "", False)[0]
            ):
                self._on_remove_trade_device(trade_id, device.id)
            if (
                device.individual_address
                and imgui.menu_item(S.CONTEXT_COPY_ADDRESS, "", False)[0]
            ):
                imgui.set_clipboard_text(device.individual_address)
            if self._on_copy_config is not None:
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
            if (
                space_id is not None
                and self._on_set_device_space is not None
                and imgui.menu_item(S.SPACES_UNASSIGN, "", False)[0]
            ):
                self._report(self._on_set_device_space(device.id, None))
            if self._on_device_info is not None:
                render_read_device_item(
                    device.id, bool(device.individual_address), self._on_device_info
                )
            if (
                self._on_compare is not None
                and imgui.menu_item(S.CONTEXT_COMPARE_DEVICE, "", False)[0]
            ):
                self._on_compare(device.id)
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
        if self._on_create_trade is not None and imgui.begin_popup_context_item(
            "##trades_root_ctx"
        ):
            if imgui.menu_item(S.TRADES_ADD, "", False)[0]:
                self._start_add(_TRADE, None, S.SPACES_ROOT_TRADES)
                self._pending_popup_id = _add_dialog("trades")
            imgui.end_popup()
        self._open_if_pending(_add_dialog("trades"))
        self._render_add_popup(_add_dialog("trades"))
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
        label = f"{trade.number} {trade.name}".strip() or S.TRADES_DEFAULT
        open_node = tree_node(f"{label}##trade{trade.id}", flags)
        if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
            self._selected_function = None
            self._selected_trade = trade.id
            self._on_select_element("trade", trade.id)
        self._render_trade_context_menu(trade, label)
        render_description(trade.description)
        self._open_if_pending(_add_dialog(f"tr{trade.id}"))
        self._render_add_popup(_add_dialog(f"tr{trade.id}"))
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
        if self._on_create_trade is None or not imgui.begin_popup_context_item(
            f"##tradectx{trade.id}"
        ):
            return
        if imgui.begin_menu(S.SPACES_ADD_MENU):
            if imgui.menu_item(S.TRADES_ADD_TRADES, "", False)[0]:
                self._start_add(_TRADE, trade.id, label)
                self._pending_popup_id = _add_dialog(f"tr{trade.id}")
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
        if (
            self._on_remove_trade is not None
            and imgui.menu_item(S.SPACES_DELETE, "", False)[0]
        ):
            self._on_remove_trade(trade.id)
        imgui.end_popup()

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

    def _render_function(self, function: "FunctionInfo") -> None:
        label = function.name or function.usage_text or function.function_type
        type_label = _function_type_label(function.function_type)
        # A click selects the function, the arrow and a double click open it.
        flags = (
            imgui.TreeNodeFlags_.open_on_arrow
            | imgui.TreeNodeFlags_.open_on_double_click
        )
        if self._selected_function == function.id:
            flags |= imgui.TreeNodeFlags_.selected
        open_node = tree_node(f"ƒ {label}  [{type_label}]##fn{function.id}", flags)
        if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
            self._selected_function = function.id
            self._selected_trade = None
            self._on_select_element("function", function.id)
        editable = self._on_rename_function is not None
        if editable and imgui.begin_popup_context_item(f"##fnctx{function.id}"):
            if imgui.menu_item(S.SPACES_FN_RENAME, "", False)[0]:
                self._rename_fn = function.id
                self._rename_buf = function.name
                self._pending_popup_id = _rename_function_dialog(function.id)
            if (
                self._on_remove_function is not None
                and imgui.menu_item(S.SPACES_FN_DELETE, "", False)[0]
            ):
                self._on_remove_function(function.id)
            imgui.end_popup()
        self._open_if_pending(_rename_function_dialog(function.id))
        self._render_rename_popup(function.id)
        if not open_node:
            return
        for ref in function.group_addresses:
            role = f"  ({ref.role})" if ref.role else ""
            imgui.bullet_text(f"{ref.text}{role}")
            if self._on_remove_function_ga is not None:
                imgui.same_line()
                if imgui.small_button(f"x##rmfnga{ref.id}"):
                    self._on_remove_function_ga(ref.id)
        if self._on_add_function_ga is not None:
            if imgui.small_button(f"{S.SPACES_FN_ADD_GA}##addfnga{function.id}"):
                self._addga_fn = function.id
                self._addga_role = ""
                self._addga_filter = ""
                imgui.open_popup(_add_function_ga_dialog(function.id))
            self._render_addga_popup(function.id)
        imgui.tree_pop()

    def _render_rename_popup(self, function_id: int) -> None:
        if not begin_dialog(_rename_function_dialog(function_id)):
            return
        imgui.set_next_item_width(px(240.0))
        _, self._rename_buf = imgui.input_text(S.SPACES_FN_NAME, self._rename_buf)
        clicked = dialog_buttons(S.SPACES_FN_RENAME, S.ML_CANCEL)
        if clicked == 0 and self._on_rename_function is not None:
            self._on_rename_function(function_id, self._rename_buf.strip())
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_addga_popup(self, function_id: int) -> None:
        if not begin_dialog(_add_function_ga_dialog(function_id)):
            return
        imgui.set_next_item_width(px(260.0))
        _, self._addga_role = imgui.input_text(S.SPACES_FN_ROLE, self._addga_role)
        self._addga_filter = filter_box(
            f"##fngaflt{function_id}", S.SPACES_FILTER_HINT, self._addga_filter
        )
        needle = self._addga_filter.strip().lower()
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
        if not imgui.tree_node_ex(f"{node.name or f'[{node.id}]'}##fngr{node.id}"):
            return
        for child in node.children:
            self._render_ga_pick_range(function_id, child, needle)
        for ga in node.group_addresses:
            text = f"{ga.text}  {ga.name}"
            if needle and needle not in text.lower():
                continue
            if imgui.selectable(f"{text}##fngapick{function_id}_{ga.id}", False)[0]:
                if self._on_add_function_ga is not None:
                    self._on_add_function_ga(
                        function_id, ga.id, self._addga_role.strip()
                    )
                imgui.close_current_popup()
        imgui.tree_pop()

    @staticmethod
    def _range_has_ga(node: Any, needle: str) -> bool:
        if any(needle in f"{g.text}  {g.name}".lower() for g in node.group_addresses):
            return True
        return any(SpacesPanel._range_has_ga(child, needle) for child in node.children)

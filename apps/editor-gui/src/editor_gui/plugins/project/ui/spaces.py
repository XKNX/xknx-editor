"""Buildings view: the imported location tree (building -> floor -> room -> ...) with the
devices placed in each space and the functions assigned to it."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.config_dialog import DeviceConfigDialog
from editor_gui.widgets.dpi import px, px_vec2

if TYPE_CHECKING:
    from xknxeditor.proj.core.service import (
        FunctionInfo,
        SpaceDeviceInfo,
        SpaceInfo,
    )


def _function_type_presets() -> list[tuple[str, str]]:
    """(label, KNX FunctionType code) for the type picker. Codes verified against knx_master.xml
    (current, non-deprecated); role stays free text since group addresses are accepted without one."""
    return [
        (S.SPACES_FN_TYPE_SWITCH, "FT-1"),
        (S.SPACES_FN_TYPE_DIM, "FT-6"),
        (S.SPACES_FN_TYPE_BLIND, "FT-7"),
        (S.SPACES_FN_TYPE_SOCKET, "FT-10"),
        (S.SPACES_FN_TYPE_CUSTOM, "FT-0"),
    ]


def _function_type_label(code: str) -> str:
    for label, c in _function_type_presets():
        if c == code:
            return label
    return code or S.SPACES_FN_TYPE_CUSTOM


def _space_type_presets() -> list[tuple[str, str]]:
    """(label, KNX SpaceType code) for the type picker. Codes are the common, non-deprecated
    values from the KNX project schema (SpaceType_t)."""
    return [
        (S.SPACES_TYPE_BUILDING, "Building"),
        (S.SPACES_TYPE_BUILDINGPART, "BuildingPart"),
        (S.SPACES_TYPE_FLOOR, "Floor"),
        (S.SPACES_TYPE_ROOM, "Room"),
        (S.SPACES_TYPE_CORRIDOR, "Corridor"),
        (S.SPACES_TYPE_STAIRWAY, "Stairway"),
        (S.SPACES_TYPE_DISTRIBUTION, "DistributionBoard"),
    ]


def _default_child_type_index(parent_type: str | None) -> int:
    """Sensible default type for a new child, given its parent's type (index into the presets):
    a building holds floors, a floor holds rooms, everything else defaults to a room."""
    codes = [c for _, c in _space_type_presets()]
    target = {"Building": "Floor", "BuildingPart": "Room", "Floor": "Room"}.get(
        parent_type or "", "Room"
    )
    return codes.index(target) if target in codes else codes.index("Room")


class SpacesPanel:
    def __init__(
        self,
        get_space_tree: "Callable[[], list[SpaceInfo]]",
        on_select_device_id: Callable[[int], None],
        on_create_function: Callable[[int, str, str], None] | None = None,
        on_remove_function: Callable[[int], None] | None = None,
        on_rename_function: Callable[[int, str], None] | None = None,
        on_set_function_type: Callable[[int, str], None] | None = None,
        on_add_function_ga: Callable[[int, int, str], None] | None = None,
        on_remove_function_ga: Callable[[int], None] | None = None,
        get_ga_range_tree: Callable[[], list[Any]] | None = None,
        on_create_space: Callable[[int | None, str, str], None] | None = None,
        on_rename_space: Callable[[int, str], None] | None = None,
        on_set_space_type: Callable[[int, str], None] | None = None,
        on_move_space: Callable[[int, int | None], None] | None = None,
        on_remove_space: Callable[[int], None] | None = None,
        on_set_device_space: Callable[[int, int | None], None] | None = None,
        get_unassigned_devices: "Callable[[], list[SpaceDeviceInfo]] | None" = None,
        on_copy_config: Callable[[int], None] | None = None,
        on_paste_config: Callable[[int, bool, bool], None] | None = None,
        on_duplicate_config: Callable[[int, bool, bool], None] | None = None,
        can_paste_config: Callable[[int], bool] | None = None,
        count_paste_targets: Callable[[int], int] | None = None,
    ) -> None:
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
        self._on_rename_space = on_rename_space
        self._on_set_space_type = on_set_space_type
        self._on_move_space = on_move_space
        self._on_remove_space = on_remove_space
        self._on_set_device_space = on_set_device_space
        self._get_unassigned_devices = get_unassigned_devices
        self._on_copy_config = on_copy_config
        self._can_paste_config_cb = can_paste_config
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
        self._new_fn_type = 0  # index into _function_type_presets()
        # "Rename function" + "assign group address" dialog state.
        self._rename_fn: int | None = None
        self._rename_buf = ""
        self._addga_fn: int | None = None
        self._addga_role = ""
        self._addga_filter = ""
        # "New space" + "rename space" + "assign device" dialog state (shared; one popup at a time).
        self._new_space_name = ""
        self._new_space_type = 0  # index into _space_type_presets()
        self._space_rename_buf = ""
        self._assign_filter = ""
        # A popup requested from inside a context menu must be opened one scope up, not inside the
        # context-menu popup: imgui.open_popup() and begin_popup() only match when called in the
        # same window/id scope. Opening from within the context menu (a different popup window)
        # registers the popup under a mismatched id, so begin_popup() at the tree scope never sees
        # it. The context menu stores its id here; the tree scope opens it (see _open_if_pending).
        self._pending_popup_id: str | None = None

    def render(self) -> None:
        tree = self._get_space_tree()
        if self._on_create_space is not None:
            if imgui.small_button(S.SPACES_ADD_SPACE):
                self._new_space_name = ""
                self._new_space_type = 0  # top-level default: Building
                imgui.open_popup("##newspace_root")
            self._render_new_space_popup(None, "##newspace_root")
        if not tree:
            imgui.text_disabled(S.SPACES_EMPTY)
            self._render_unassigned_section()
            return
        self._filter_text = filter_box(
            "##spaces_filter", S.SPACES_FILTER_HINT, self._filter_text
        )
        flt = self._filter_text.strip().lower()
        for space in tree:
            self._render_space(space, flt)
        self._render_unassigned_section()
        self._config_dialog.render()

    def _render_space(self, space: "SpaceInfo", flt: str = "") -> None:
        if flt and not self._space_has_match(space, flt):
            return  # while filtering, hide spaces with no match anywhere below
        # A space that matches by its own name shows all its contents; otherwise
        # only the matching devices/functions (and matching child spaces).
        self_match = not flt or self._space_matches_self(space, flt)
        label = space.name or space.space_type or "?"
        if space.space_type:
            label = f"{label}  [{space.space_type}]"
        if flt:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        open_node = imgui.tree_node_ex(
            f"{label}##sp{space.id}", imgui.TreeNodeFlags_.default_open
        )
        if space.description and imgui.is_item_hovered():
            imgui.set_tooltip(space.description)
        # The context menu and the space's own popups must run whether the node is expanded or not
        # (a collapsed node can still be renamed/moved/deleted), so render them before the return.
        self._render_space_context_menu(space)
        self._open_if_pending(f"##newsub{space.id}")
        self._render_new_space_popup(space.id, f"##newsub{space.id}")
        self._open_if_pending(f"##renspace{space.id}")
        self._render_space_rename_popup(space.id)
        self._open_if_pending(f"##delspace{space.id}")
        self._render_space_delete_confirm(space.id)
        self._render_assign_device_popup(space.id)
        if not open_node:
            return
        if space.description:
            imgui.text_disabled(space.description)
        for child in space.children:
            self._render_space(child, flt)
        for device in space.devices:
            if self_match or self._device_matches(device, flt):
                self._render_device(device, space_id=space.id)
        for function in space.functions:
            if self_match or self._function_matches(function, flt):
                self._render_function(function)
        if self._on_set_device_space is not None and imgui.small_button(
            f"{S.SPACES_ASSIGN_DEVICE}##assign{space.id}"
        ):
            self._assign_filter = ""
            imgui.open_popup(f"##assigndev{space.id}")
        if self._on_create_function is not None:
            imgui.same_line()
            if imgui.small_button(f"{S.SPACES_ADD_FUNCTION}##addfn{space.id}"):
                self._new_fn_space = space.id
                self._new_fn_name = ""
                self._new_fn_type = 0
                imgui.open_popup(f"##newfn{space.id}")
            self._render_new_function_popup(space.id)
        imgui.tree_pop()

    def _open_if_pending(self, popup_id: str) -> None:
        """Open ``popup_id`` here if a context menu requested it. Called at the tree scope, the
        same scope as the matching begin_popup(), so the ids line up (see _pending_popup_id)."""
        if self._pending_popup_id == popup_id:
            self._pending_popup_id = None
            imgui.open_popup(popup_id)

    def _render_space_context_menu(self, space: "SpaceInfo") -> None:
        if self._on_rename_space is None:  # editing disabled -> no menu
            return
        if not imgui.begin_popup_context_item(f"##spctx{space.id}"):
            return
        if (
            self._on_create_space is not None
            and imgui.menu_item(S.SPACES_ADD_SUBSPACE, "", False)[0]
        ):
            self._new_space_name = ""
            self._new_space_type = _default_child_type_index(space.space_type)
            self._pending_popup_id = f"##newsub{space.id}"
        if imgui.menu_item(S.SPACES_RENAME, "", False)[0]:
            self._space_rename_buf = space.name
            self._pending_popup_id = f"##renspace{space.id}"
        if self._on_set_space_type is not None and imgui.begin_menu(S.SPACES_TYPE):
            for label, code in _space_type_presets():
                if imgui.menu_item(label, "", space.space_type == code)[0]:
                    self._on_set_space_type(space.id, code)
            imgui.end_menu()
        if self._on_move_space is not None and imgui.begin_menu(S.SPACES_MOVE_TO):
            if imgui.menu_item(S.SPACES_MOVE_ROOT, "", False)[0]:
                self._on_move_space(space.id, None)
            imgui.separator()
            for target, depth in self._move_targets(space.id):
                indent = "  " * depth
                label = f"{indent}{target.name or target.space_type or '?'}"
                if imgui.menu_item(f"{label}##mv{space.id}_{target.id}", "", False)[0]:
                    self._on_move_space(space.id, target.id)
            imgui.end_menu()
        if (
            self._on_remove_space is not None
            and imgui.menu_item(S.SPACES_DELETE, "", False)[0]
        ):
            self._pending_popup_id = f"##delspace{space.id}"
        imgui.end_popup()

    def _move_targets(self, moving_id: int) -> "list[tuple[SpaceInfo, int]]":
        """Flattened (space, depth) candidates to move ``moving_id`` under, excluding the space
        itself and its descendants (which would form a cycle)."""
        out: list[tuple[SpaceInfo, int]] = []

        def walk(spaces: "list[SpaceInfo]", depth: int, blocked: bool) -> None:
            for s in spaces:
                skip = blocked or s.id == moving_id
                if not skip:
                    out.append((s, depth))
                walk(s.children, depth + 1, skip)

        walk(self._get_space_tree(), 0, False)
        return out

    def _render_new_space_popup(self, parent_id: int | None, popup_id: str) -> None:
        if self._on_create_space is None or not imgui.begin_popup(popup_id):
            return
        imgui.text_disabled(S.SPACES_SPACE_NEW_TITLE)
        imgui.set_next_item_width(px(220.0))
        _, self._new_space_name = imgui.input_text(
            S.SPACES_SPACE_NAME, self._new_space_name
        )
        presets = _space_type_presets()
        imgui.set_next_item_width(px(220.0))
        _, self._new_space_type = imgui.combo(
            S.SPACES_SPACE_TYPE, self._new_space_type, [label for label, _ in presets]
        )
        can_create = bool(self._new_space_name.strip())
        imgui.begin_disabled(not can_create)
        if imgui.button(S.SPACES_SPACE_CREATE):
            _, code = presets[self._new_space_type]
            self._on_create_space(parent_id, code, self._new_space_name.strip())
            imgui.close_current_popup()
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button(f"{S.ML_CANCEL}##newspace_cancel{popup_id}"):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_space_rename_popup(self, space_id: int) -> None:
        if self._on_rename_space is None or not imgui.begin_popup(
            f"##renspace{space_id}"
        ):
            return
        imgui.set_next_item_width(px(240.0))
        _, self._space_rename_buf = imgui.input_text(
            S.SPACES_SPACE_NAME, self._space_rename_buf
        )
        if imgui.button(S.SPACES_RENAME):
            self._on_rename_space(space_id, self._space_rename_buf.strip())
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(f"{S.ML_CANCEL}##renspace_cancel{space_id}"):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_space_delete_confirm(self, space_id: int) -> None:
        if self._on_remove_space is None or not imgui.begin_popup(
            f"##delspace{space_id}"
        ):
            return
        imgui.text_wrapped(S.SPACES_DELETE_CONFIRM)
        if imgui.button(S.SPACES_DELETE):
            self._on_remove_space(space_id)
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(f"{S.ML_CANCEL}##delspace_cancel{space_id}"):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_assign_device_popup(self, space_id: int) -> None:
        if self._on_set_device_space is None or self._get_unassigned_devices is None:
            return
        if not imgui.begin_popup(f"##assigndev{space_id}"):
            return
        imgui.text_disabled(S.SPACES_ASSIGN_TITLE)
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
                    self._on_set_device_space(device.id, space_id)
                    imgui.close_current_popup()
        imgui.end_child()
        imgui.end_popup()

    def _render_unassigned_section(self) -> None:
        if self._on_set_device_space is None or self._get_unassigned_devices is None:
            return
        devices = self._get_unassigned_devices()
        if not devices:
            return
        if not imgui.tree_node_ex(
            f"{S.SPACES_UNASSIGNED_TITLE} ({len(devices)})##unassigned"
        ):
            return
        targets = self._move_targets(
            -1
        )  # -1: no space excluded -> every space is a target
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
                    label = f"{indent}{target.name or target.space_type or '?'}"
                    if imgui.menu_item(
                        f"{label}##uto{device.id}_{target.id}", "", False
                    )[0]:
                        self._on_set_device_space(device.id, target.id)
                        imgui.close_current_popup()
                imgui.end_popup()
        imgui.tree_pop()

    @staticmethod
    def _device_label(device: "SpaceDeviceInfo") -> str:
        ia = f"{device.individual_address}  " if device.individual_address else ""
        primary = (
            device.name
            or device.product_name
            or device.hardware_name
            or device.description
            or "?"
        )
        return f"{ia}{primary}".strip()

    def _render_new_function_popup(self, space_id: int) -> None:
        if not imgui.begin_popup(f"##newfn{space_id}"):
            return
        imgui.text_disabled(S.SPACES_FN_NEW_TITLE)
        imgui.set_next_item_width(px(220.0))
        _, self._new_fn_name = imgui.input_text(S.SPACES_FN_NAME, self._new_fn_name)
        presets = _function_type_presets()
        imgui.set_next_item_width(px(220.0))
        _, self._new_fn_type = imgui.combo(
            S.SPACES_FN_TYPE, self._new_fn_type, [label for label, _ in presets]
        )
        can_create = bool(self._new_fn_name.strip())
        imgui.begin_disabled(not can_create)
        if imgui.button(S.SPACES_FN_CREATE) and self._on_create_function is not None:
            _, code = presets[self._new_fn_type]
            self._on_create_function(space_id, code, self._new_fn_name.strip())
            imgui.close_current_popup()
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button(f"{S.ML_CANCEL}##newfn_cancel{space_id}"):
            imgui.close_current_popup()
        imgui.end_popup()

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
        self, device: "SpaceDeviceInfo", space_id: int | None = None
    ) -> None:
        # Fall back to the product/hardware name when the device is unnamed.
        primary = (
            device.name
            or device.product_name
            or device.hardware_name
            or device.description
            or "?"
        )
        detail = (
            device.description
            if device.description and device.description != primary
            else ""
        )
        leaf = self._device_label(device)
        if detail:
            leaf = f"{leaf}  — {detail}"
        # allow_overlap: the "Without space" section draws an "Assign device" button via
        # same_line() over this full-width selectable. Without the flag the selectable (submitted
        # first) claims the click and the button never fires.
        if imgui.selectable(
            f"{leaf}##spdev{device.id}",
            False,
            imgui.SelectableFlags_.allow_overlap,
        )[0]:
            self._on_select_device_id(device.id)
        hovered = imgui.is_item_hovered()  # capture before the context menu below
        has_menu = (
            bool(device.individual_address)
            or (space_id is not None and self._on_set_device_space is not None)
            or self._on_copy_config is not None
        )
        if has_menu and imgui.begin_popup_context_item(f"##spdev_ctx_{device.id}"):
            if (
                device.individual_address
                and imgui.menu_item(S.CONTEXT_COPY_ADDRESS, "", False)[0]
            ):
                imgui.set_clipboard_text(device.individual_address)
            if self._on_copy_config is not None:
                config_label = (
                    f"{device.individual_address} {primary}"
                    if device.individual_address
                    else primary
                )
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
                self._on_set_device_space(device.id, None)
            imgui.end_popup()
        if hovered:
            parts = [
                p
                for p in (
                    device.manufacturer_name,
                    device.product_name,
                    device.hardware_name,
                    device.description,
                )
                if p
            ]
            if parts:
                imgui.set_tooltip("\n".join(parts))

    def _render_function(self, function: "FunctionInfo") -> None:
        label = function.name or function.usage_text or function.function_type
        type_label = _function_type_label(function.function_type)
        open_node = imgui.tree_node_ex(f"ƒ {label}  [{type_label}]##fn{function.id}")
        editable = self._on_rename_function is not None
        if editable and imgui.begin_popup_context_item(f"##fnctx{function.id}"):
            if imgui.menu_item(S.SPACES_FN_RENAME, "", False)[0]:
                self._rename_fn = function.id
                self._rename_buf = function.name
                self._pending_popup_id = f"##renfn{function.id}"
            if (
                self._on_remove_function is not None
                and imgui.menu_item(S.SPACES_FN_DELETE, "", False)[0]
            ):
                self._on_remove_function(function.id)
            imgui.end_popup()
        self._open_if_pending(f"##renfn{function.id}")
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
                imgui.open_popup(f"##addfnga{function.id}")
            self._render_addga_popup(function.id)
        imgui.tree_pop()

    def _render_rename_popup(self, function_id: int) -> None:
        if not imgui.begin_popup(f"##renfn{function_id}"):
            return
        imgui.set_next_item_width(px(240.0))
        _, self._rename_buf = imgui.input_text(S.SPACES_FN_NAME, self._rename_buf)
        if imgui.button(S.SPACES_FN_RENAME) and self._on_rename_function is not None:
            self._on_rename_function(function_id, self._rename_buf.strip())
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(f"{S.ML_CANCEL}##renfn_cancel{function_id}"):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_addga_popup(self, function_id: int) -> None:
        if not imgui.begin_popup(f"##addfnga{function_id}"):
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
        imgui.end_popup()

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

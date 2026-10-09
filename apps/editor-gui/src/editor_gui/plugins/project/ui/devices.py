from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.device import Device, UnloadedDevice, address_order
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.config_dialog import DeviceConfigDialog
from editor_gui.widgets.dpi import label_column_width, px, px_vec2

TreeDevice = Device | UnloadedDevice

_UNLOADED_COLOR = imgui.ImVec4(0.95, 0.6, 0.25, 1.0)


def _address_order(device: TreeDevice) -> tuple[tuple[int, int, int], int]:
    """Numeric individual-address order; devices without a valid address keep their creation
    order after the addressed ones."""
    return (address_order(device.individual_address), device.node_id)


@dataclass
class Area:
    id: int
    number: int
    name: str


@dataclass
class Line:
    id: int
    area_id: int
    number: int
    name: str


class DevicesPanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_areas: Callable[[], list[Area]],
        get_lines: Callable[[int], list[Line]],
        on_select_device: Callable[[Device], None],
        on_move_device: Callable[[Device, int, int], None],
        on_create_area: Callable[[int, str], None],
        on_remove_area: Callable[[Area], None],
        on_rename_area: Callable[[Area, str], None],
        on_create_line: Callable[[int, int, str], None],
        on_remove_line: Callable[[Line], None],
        on_rename_line: Callable[[Line, str], None],
        get_selected_node_id: Callable[[], int | None] = lambda: None,
        on_select_devices: Callable[[Device, list[int]], None] | None = None,
        get_selected_node_ids: Callable[[], set[int]] = lambda: set(),
        on_delete_device: Callable[[TreeDevice], None] = lambda _d: None,
        on_copy_config: Callable[[int], None] = lambda _n: None,
        on_paste_config: Callable[[int, bool, bool], None] = lambda _n, _p, _l: None,
        on_duplicate_config: Callable[[int, bool, bool], None] = (
            lambda _n, _p, _l: None
        ),
        can_paste_config: Callable[[int], bool] = lambda _n: False,
        count_paste_targets: Callable[[int], int] = lambda _n: 1,
        get_unloaded_devices: Callable[[], list[UnloadedDevice]] = lambda: [],
    ) -> None:
        self._get_devices = get_devices
        self._get_unloaded_devices = get_unloaded_devices
        self._get_areas = get_areas
        self._get_lines = get_lines
        self._on_select_device = on_select_device
        self._get_selected_node_id = get_selected_node_id
        # Multi-select (Ctrl/Shift). When wired, drives a device selection *set*; the plain
        # on_select_device path stays as the single-select fallback.
        self._on_select_devices = on_select_devices
        self._get_selected_node_ids = get_selected_node_ids
        self._last_selected: int | None = None
        self._on_delete_device = on_delete_device
        self._on_copy_config = on_copy_config
        self._can_paste_config_cb = can_paste_config
        self._config_dialog = DeviceConfigDialog(
            "devices", on_paste_config, on_duplicate_config, count_paste_targets
        )
        self._on_move_device = on_move_device
        self._on_create_area = on_create_area
        self._on_remove_area = on_remove_area
        self._on_rename_area = on_rename_area
        self._on_create_line = on_create_line
        self._on_remove_line = on_remove_line
        self._on_rename_line = on_rename_line
        self._dragging_device: Device | None = None
        self._filter_text: str = ""

        self._popup_area_number: int = 0
        self._popup_line_number: int = 0
        self._popup_name: str = ""
        self._popup_error: str = ""
        self._popup_target_area: Area | None = None
        self._popup_target_line: Line | None = None
        self._open_new_area_popup: bool = False
        self._open_new_line_popup: bool = False
        self._open_rename_popup: bool = False
        self._delete_target_device: TreeDevice | None = None
        self._open_delete_device_popup: bool = False

    def render(self) -> None:
        devices: list[TreeDevice] = sorted(
            [*self._get_devices(), *self._get_unloaded_devices()],
            key=_address_order,
        )
        areas = self._get_areas()
        device_tree = self._build_device_tree(devices)

        self._filter_text = filter_box(
            "##device_filter", S.DEVICE_FILTER_HINT, self._filter_text
        )
        flt = self._filter_text.strip().lower()

        if not areas and not devices:
            imgui.text_disabled(S.DEVICE_EMPTY_HINT)

        if imgui.begin_popup_context_window("##devices_context"):
            if imgui.menu_item(S.CONTEXT_ADD_AREA, "", False)[0]:
                self._popup_area_number = self._next_area_number(areas)
                self._popup_name = ""
                self._open_new_area_popup = True
            imgui.end_popup()

        if self._open_new_area_popup:
            self._popup_error = ""
            imgui.open_popup(S.POPUP_NEW_AREA)
            self._open_new_area_popup = False
        if self._open_new_line_popup:
            self._popup_error = ""
            imgui.open_popup(S.POPUP_NEW_LINE)
            self._open_new_line_popup = False
        if self._open_rename_popup:
            imgui.open_popup(S.POPUP_RENAME)
            self._open_rename_popup = False
        if self._open_delete_device_popup:
            imgui.open_popup(S.DEVICE_DELETE_TITLE)
            self._open_delete_device_popup = False

        self._render_new_area_popup()
        self._render_new_line_popup()
        self._render_rename_popup()
        self._render_delete_device_popup()
        self._config_dialog.render()

        leaf_flags = (
            imgui.TreeNodeFlags_.leaf
            | imgui.TreeNodeFlags_.no_tree_push_on_open
            | imgui.TreeNodeFlags_.span_avail_width
        )
        # Highlight every selected device (multi-select); include the single primary for safety.
        selected_ids = set(self._get_selected_node_ids())
        single = self._get_selected_node_id()
        if single is not None:
            selected_ids.add(single)

        def _leaf(device: TreeDevice) -> int:
            flags = leaf_flags
            if device.node_id in selected_ids:
                flags = flags | imgui.TreeNodeFlags_.selected
            return flags

        # Flat device order as rendered (areas -> lines -> devices, then unassigned) for Shift-range.
        ordered_ids: list[int] = []
        for _area in areas:
            for _line in self._get_lines(_area.id):
                for _d in device_tree.get(_area.number, {}).get(_line.number, []):
                    if isinstance(_d, Device) and (
                        not flt or self._device_matches(_d, flt)
                    ):
                        ordered_ids.append(_d.node_id)
        for _d in self._get_unassigned_devices(devices, areas):
            if isinstance(_d, Device) and (not flt or self._device_matches(_d, flt)):
                ordered_ids.append(_d.node_id)

        for area in areas:
            lines = self._get_lines(area.id)
            if flt and not any(
                self._device_matches(device, flt)
                for line in lines
                for device in device_tree.get(area.number, {}).get(line.number, [])
            ):
                continue  # while filtering, hide areas with no matching device
            area_label = self._format_area_label(area)
            area_flags = (
                imgui.TreeNodeFlags_.default_open
                | imgui.TreeNodeFlags_.span_avail_width
            )
            if flt:
                imgui.set_next_item_open(True, imgui.Cond_.always)
            if imgui.tree_node_ex(f"{area_label}##area_{area.id}", area_flags):
                self._render_area_context_menu(area, lines)

                for line in lines:
                    line_devices = device_tree.get(area.number, {}).get(line.number, [])
                    if flt:
                        line_devices = [
                            d for d in line_devices if self._device_matches(d, flt)
                        ]
                        if not line_devices:
                            continue  # hide lines with no match while filtering
                    line_label = self._format_line_label(area, line)
                    line_flags = (
                        imgui.TreeNodeFlags_.default_open
                        | imgui.TreeNodeFlags_.span_avail_width
                    )
                    if flt:
                        imgui.set_next_item_open(True, imgui.Cond_.always)
                    if imgui.tree_node_ex(f"{line_label}##line_{line.id}", line_flags):
                        self._render_line_context_menu(line)
                        self._render_line_drop_target(area, line)

                        for device in line_devices:
                            self._render_device_node(device, _leaf, ordered_ids)
                        imgui.tree_pop()
                imgui.tree_pop()

        unassigned = self._get_unassigned_devices(devices, areas)
        if flt:
            unassigned = [d for d in unassigned if self._device_matches(d, flt)]
        if unassigned:
            unassigned_flags = (
                imgui.TreeNodeFlags_.default_open
                | imgui.TreeNodeFlags_.span_avail_width
            )
            if flt:
                imgui.set_next_item_open(True, imgui.Cond_.always)
            if imgui.tree_node_ex(
                S.DEVICE_UNASSIGNED.format(count=len(unassigned)), unassigned_flags
            ):
                for device in unassigned:
                    self._render_device_node(device, _leaf, ordered_ids)
                imgui.tree_pop()

    def _render_device_node(
        self,
        device: TreeDevice,
        leaf: Callable[[TreeDevice], int],
        ordered_ids: list[int],
    ) -> None:
        if isinstance(device, UnloadedDevice):
            # Shown like any device, but without an application there is nothing to configure.
            imgui.push_style_color(imgui.Col_.text, _UNLOADED_COLOR)
            imgui.tree_node_ex(self._device_label(device), leaf(device))
            imgui.pop_style_color()
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.DEVICE_PRODUCT_DATA_MISSING)
            self._render_unloaded_device_context_menu(device)
            return
        imgui.tree_node_ex(self._device_label(device), leaf(device))
        if imgui.is_item_clicked():
            self._handle_device_click(device, ordered_ids)
        self._render_device_context_menu(device)
        self._render_device_drag_source(device)

    def _render_unloaded_device_context_menu(self, device: UnloadedDevice) -> None:
        if imgui.begin_popup_context_item(f"##dev_ctx_{device.node_id}"):
            if (
                device.individual_address
                and imgui.menu_item(S.CONTEXT_COPY_ADDRESS, "", False)[0]
            ):
                imgui.set_clipboard_text(device.individual_address)
            if imgui.menu_item(S.CONTEXT_DELETE, "", False)[0]:
                self._delete_target_device = device
                self._open_delete_device_popup = True
            imgui.end_popup()

    def _handle_device_click(self, device: Device, ordered_ids: list[int]) -> None:
        """Selection: plain click = single, Ctrl = toggle, Shift = range (additive with
        Ctrl). Falls back to single-select when the multi-select callback is not wired."""
        if self._on_select_devices is None:
            self._on_select_device(device)
            return
        io = imgui.get_io()
        ctrl = io.key_ctrl or io.key_super
        shift = io.key_shift
        current = set(self._get_selected_node_ids())
        nid = device.node_id
        if shift and self._last_selected in ordered_ids and nid in ordered_ids:
            i0 = ordered_ids.index(self._last_selected)
            i1 = ordered_ids.index(nid)
            lo, hi = sorted((i0, i1))
            rng = set(ordered_ids[lo : hi + 1])
            current = (current | rng) if ctrl else rng
        elif ctrl:
            current ^= {nid}
        else:
            current = {nid}
        self._last_selected = nid
        self._on_select_devices(device, sorted(current))

    def _render_device_context_menu(self, device: Device) -> None:
        if imgui.begin_popup_context_item(f"##dev_ctx_{device.node_id}"):
            label = self._device_display_name(device)
            if imgui.menu_item(S.CONTEXT_DUPLICATE, "", False)[0]:
                self._config_dialog.open(device.node_id, label, "duplicate")
            if imgui.menu_item(S.CONTEXT_COPY_CONFIG, "", False)[0]:
                self._on_copy_config(device.node_id)
            if imgui.menu_item(
                S.CONTEXT_PASTE_CONFIG,
                "",
                False,
                self._can_paste_config_cb(device.node_id),
            )[0]:
                self._config_dialog.open(device.node_id, label, "paste")
            if (
                device.individual_address
                and imgui.menu_item(S.CONTEXT_COPY_ADDRESS, "", False)[0]
            ):
                imgui.set_clipboard_text(device.individual_address)
            imgui.separator()
            if imgui.menu_item(S.CONTEXT_DELETE, "", False)[0]:
                self._delete_target_device = device
                self._open_delete_device_popup = True
            imgui.end_popup()

    def _render_delete_device_popup(self) -> None:
        if imgui.begin_popup_modal(
            S.DEVICE_DELETE_TITLE, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            device = self._delete_target_device
            name = self._device_display_name(device) if device else ""
            imgui.text_wrapped(S.DEVICE_DELETE_CONFIRM.format(name=name))
            imgui.separator()
            if imgui.button(S.CONTEXT_DELETE, px_vec2(90, 0)):
                if device is not None:
                    self._on_delete_device(device)
                self._delete_target_device = None
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button(S.BTN_CANCEL, px_vec2(90, 0)):
                self._delete_target_device = None
                imgui.close_current_popup()
            imgui.end_popup()

    def _device_display_name(self, device: TreeDevice) -> str:
        """Human label for dialogs: name, else product/app name, prefixed with the address."""
        primary = device.display_name or "?"
        if device.individual_address:
            return f"{device.individual_address} {primary}"
        return primary

    def _device_matches(self, device: TreeDevice, flt: str) -> bool:
        """Case-insensitive match of a device against the filter (name, product, address, app)."""
        if flt in (device.name or "").lower():
            return True
        if flt in (device.product_name or "").lower():
            return True
        if flt in (device.individual_address or "").lower():
            return True
        if isinstance(device, UnloadedDevice):
            return False
        app_name = getattr(device.app, "name", "") or ""
        return flt in app_name.lower()

    def _device_label(self, device: TreeDevice) -> str:
        # Imported devices are often unnamed; fall back to the product/application name.
        primary = device.display_name or "?"
        version = "" if isinstance(device, UnloadedDevice) else device.app.version or ""
        ver = f"  V{version}" if version else ""  # app program version, e.g. "V20"
        if device.individual_address:
            return f"{device.individual_address}  {primary}{ver}##dev{device.node_id}"
        return f"{primary}{ver}##dev{device.node_id}"

    def _format_area_label(self, area: Area) -> str:
        if area.name:
            return S.DEVICE_AREA_NAMED.format(name=area.name, area=area.number)
        return S.DEVICE_AREA.format(area=area.number)

    def _format_line_label(self, area: Area, line: Line) -> str:
        if line.name:
            return S.DEVICE_LINE_NAMED.format(
                name=line.name, area=area.number, line=line.number
            )
        return S.DEVICE_LINE.format(area=area.number, line=line.number)

    def _render_area_context_menu(self, area: Area, lines: list[Line]) -> None:
        if imgui.begin_popup_context_item(f"##area_ctx_{area.id}"):
            if imgui.menu_item(S.CONTEXT_ADD_LINE, "", False)[0]:
                self._popup_target_area = area
                self._popup_line_number = self._next_line_number(lines)
                self._popup_name = ""
                self._open_new_line_popup = True
            if imgui.menu_item(S.CONTEXT_RENAME, "", False)[0]:
                self._popup_target_area = area
                self._popup_name = area.name
                self._open_rename_popup = True
            imgui.separator()
            if imgui.menu_item(S.CONTEXT_DELETE, "", False)[0]:
                self._on_remove_area(area)
            imgui.end_popup()

    def _render_line_context_menu(self, line: Line) -> None:
        if imgui.begin_popup_context_item(f"##line_ctx_{line.id}"):
            if imgui.menu_item(S.CONTEXT_RENAME, "", False)[0]:
                self._popup_target_line = line
                self._popup_name = line.name
                self._open_rename_popup = True
            imgui.separator()
            if imgui.menu_item(S.CONTEXT_DELETE, "", False)[0]:
                self._on_remove_line(line)
            imgui.end_popup()

    def _render_new_area_popup(self) -> None:
        if imgui.begin_popup_modal(
            S.POPUP_NEW_AREA, flags=imgui.WindowFlags_.always_auto_resize
        )[0]:
            imgui.text(S.POPUP_NAME)
            imgui.same_line(
                label_column_width(S.POPUP_NAME, S.POPUP_NUMBER, minimum=80)
            )
            if imgui.is_window_appearing():
                imgui.set_keyboard_focus_here()
            imgui.set_next_item_width(px(150))
            _, self._popup_name = imgui.input_text("##area_name", self._popup_name)

            imgui.text(S.POPUP_NUMBER)
            imgui.same_line(
                label_column_width(S.POPUP_NAME, S.POPUP_NUMBER, minimum=80)
            )
            imgui.set_next_item_width(px(150))
            _, self._popup_area_number = imgui.input_int(
                "##area_num", self._popup_area_number
            )

            imgui.separator()
            if self._popup_error:
                imgui.text_colored(imgui.ImVec4(1.0, 0.4, 0.4, 1.0), self._popup_error)
            if imgui.button(S.BTN_ADD, px_vec2(75, 0)):
                try:
                    self._on_create_area(self._popup_area_number, self._popup_name)
                    self._popup_error = ""
                    imgui.close_current_popup()
                except ValueError as exc:
                    self._popup_error = str(exc)
            imgui.same_line()
            if imgui.button(S.BTN_CANCEL, px_vec2(75, 0)):
                imgui.close_current_popup()
            imgui.end_popup()

    def _render_new_line_popup(self) -> None:
        if imgui.begin_popup_modal(
            S.POPUP_NEW_LINE, flags=imgui.WindowFlags_.always_auto_resize
        )[0]:
            imgui.text(S.POPUP_NAME)
            imgui.same_line(
                label_column_width(S.POPUP_NAME, S.POPUP_NUMBER, minimum=80)
            )
            if imgui.is_window_appearing():
                imgui.set_keyboard_focus_here()
            imgui.set_next_item_width(px(150))
            _, self._popup_name = imgui.input_text("##line_name", self._popup_name)

            imgui.text(S.POPUP_NUMBER)
            imgui.same_line(
                label_column_width(S.POPUP_NAME, S.POPUP_NUMBER, minimum=80)
            )
            imgui.set_next_item_width(px(150))
            _, self._popup_line_number = imgui.input_int(
                "##line_num", self._popup_line_number
            )

            imgui.separator()
            if self._popup_error:
                imgui.text_colored(imgui.ImVec4(1.0, 0.4, 0.4, 1.0), self._popup_error)
            if imgui.button(S.BTN_ADD, px_vec2(75, 0)):
                try:
                    if self._popup_target_area:
                        self._on_create_line(
                            self._popup_target_area.id,
                            self._popup_line_number,
                            self._popup_name,
                        )
                    self._popup_error = ""
                    imgui.close_current_popup()
                except ValueError as exc:
                    self._popup_error = str(exc)
            imgui.same_line()
            if imgui.button(S.BTN_CANCEL, px_vec2(75, 0)):
                imgui.close_current_popup()
            imgui.end_popup()

    def _render_rename_popup(self) -> None:
        if imgui.begin_popup_modal(
            S.POPUP_RENAME, flags=imgui.WindowFlags_.always_auto_resize
        )[0]:
            imgui.text(S.POPUP_NAME)
            imgui.same_line(
                label_column_width(S.POPUP_NAME, S.POPUP_NUMBER, minimum=80)
            )
            if imgui.is_window_appearing():
                imgui.set_keyboard_focus_here()
            imgui.set_next_item_width(px(150))
            _, self._popup_name = imgui.input_text("##rename_name", self._popup_name)

            imgui.separator()
            if imgui.button(S.BTN_ADD, px_vec2(75, 0)):
                if self._popup_target_area:
                    self._on_rename_area(self._popup_target_area, self._popup_name)
                    self._popup_target_area = None
                elif self._popup_target_line:
                    self._on_rename_line(self._popup_target_line, self._popup_name)
                    self._popup_target_line = None
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button(S.BTN_CANCEL, px_vec2(75, 0)):
                self._popup_target_area = None
                self._popup_target_line = None
                imgui.close_current_popup()
            imgui.end_popup()

    def _next_area_number(self, areas: list[Area]) -> int:
        if not areas:
            return 1
        return max(a.number for a in areas) + 1

    def _next_line_number(self, lines: list[Line]) -> int:
        if not lines:
            return 1
        return max(ln.number for ln in lines) + 1

    def _build_device_tree(
        self, devices: list[TreeDevice]
    ) -> dict[int, dict[int, list[TreeDevice]]]:
        tree: dict[int, dict[int, list[TreeDevice]]] = {}
        for device in devices:
            if not device.individual_address:
                continue
            parts = device.individual_address.split(".")
            if len(parts) < 3:
                continue
            try:
                area, line = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if area not in tree:
                tree[area] = {}
            if line not in tree[area]:
                tree[area][line] = []
            tree[area][line].append(device)
        return tree

    def _get_unassigned_devices(
        self, devices: list[TreeDevice], areas: list[Area]
    ) -> list[TreeDevice]:
        area_numbers = {a.number for a in areas}
        area_lines: dict[int, set[int]] = {}
        for area in areas:
            lines = self._get_lines(area.id)
            area_lines[area.number] = {ln.number for ln in lines}

        unassigned = []
        for device in devices:
            if not device.individual_address:
                unassigned.append(device)
                continue
            parts = device.individual_address.split(".")
            if len(parts) < 3:
                unassigned.append(device)
                continue
            try:
                area, line = int(parts[0]), int(parts[1])
            except ValueError:
                unassigned.append(device)
                continue
            if area not in area_numbers or line not in area_lines.get(area, set()):
                unassigned.append(device)
        return unassigned

    def _render_device_drag_source(self, device: Device) -> None:
        if imgui.begin_drag_drop_source():
            self._dragging_device = device
            imgui.set_drag_drop_payload_py_id("DEVICE", device.node_id)
            imgui.text(device.display_name)
            imgui.end_drag_drop_source()

    def _render_line_drop_target(self, area: Area, line: Line) -> None:
        if imgui.begin_drag_drop_target():
            payload = imgui.accept_drag_drop_payload_py_id("DEVICE")
            if payload is not None and self._dragging_device is not None:
                self._on_move_device(self._dragging_device, area.number, line.number)
                self._dragging_device = None
            imgui.end_drag_drop_target()

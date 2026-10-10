import datetime
import functools
import threading
import zipfile
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.project.button_runner import ButtonRunner
from editor_gui.plugins.project.online_runner import OnlineButtonRunner
from editor_gui.plugins.project.program_queue import (
    ProgramQueue,
    QueueItem,
    ScriptRun,
)
from editor_gui.plugins.project.service import DeviceConfigClipboard
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import (
    ConfigurePanel,
    DevicesPanel,
    GroupAddressesPanel,
    HistoryPanel,
    MassLinkerPanel,
    ProjectInfoPanel,
    ProjectLogPanel,
    SpacesPanel,
    ToolsPanel,
)
from editor_gui.plugins.project.ui.dali_commissioning import DaliCommissioningPanel
from editor_gui.plugins.project.ui.devices import Area, Line
from editor_gui.plugins.project.ui.memory_preview import MemoryPreviewWindow
from editor_gui.plugins.project.ui.preflight_result import PreflightResultWindow
from editor_gui.plugins.project.ui.program_queue import ProgramQueuePanel
from editor_gui.plugins.project.ui.tools import apply_name_swap, shifted_ia
from editor_gui.widgets import ButtonActions
from editor_gui.widgets.icons import (
    Icon,
    Picture,
    has_icon,
    has_picture,
    load_icon,
    load_picture,
)
from xknxeditor.prod.baggage import Baggages
from xknxeditor.prod.errors import ArchiveError

if TYPE_CHECKING:
    from concurrent.futures import Future

    from editor_gui.device import Device, UnloadedDevice
    from editor_gui.programming import DeviceOverview
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.download.scope import DownloadScope
    from xknxeditor.prod.parser_v2.ui import UiButton


def _run_now(fn: Callable[[], None]) -> None:
    fn()


# Which commissioning "loaded" flags a successful download of each scope sets (keyed by
# DownloadScope.value). FULL/UNLOAD set all true/false; partial scopes set only their part. Flags
# not listed are left unchanged.
_ALL_LOADED = (
    "individual_address_loaded",
    "application_program_loaded",
    "communication_part_loaded",
    "medium_config_loaded",
    "parameters_loaded",
)
_COMMISSIONING_BY_SCOPE: dict[str, dict[str, bool]] = {
    "full": dict.fromkeys(_ALL_LOADED, True),
    "unload": dict.fromkeys(_ALL_LOADED, False),
    "ap1": {  # APPLICATION: parameters + application + group communication (not the address)
        "application_program_loaded": True,
        "communication_part_loaded": True,
        "medium_config_loaded": True,
        "parameters_loaded": True,
    },
    "par": {"parameters_loaded": True},  # PARAMETERS
    "grp": {"communication_part_loaded": True},  # GROUP_COMMUNICATION
}


class ProjectPlugin:
    name = "project"

    def __init__(
        self,
        api: PluginAPI,
        get_selected_node_ids: Callable[[], list[int]] | None = None,
    ) -> None:
        self._api = api
        self._get_selected_node_ids = get_selected_node_ids
        api.project.set_logger(Logger(api.log, "project"))
        self._button_runner = ButtonRunner(api.project, Logger(api.log, "project"))
        self._baggages: dict[tuple[str, str], Baggages] = {}

        # Programming queue: repeated "Program" presses serialise onto the single bus slot.
        self._program_queue = ProgramQueue(
            is_busy=lambda: api.connection.busy_operation is not None,
            start=self._start_program,
            submit=api.main_thread.submit if api.main_thread is not None else None,
            connection=lambda item: api.connection.connection_label(
                with_address=item.script is None
            ),
        )
        self._program_queue_panel = ProgramQueuePanel(
            get_active=lambda: self._program_queue.active,
            get_history=lambda: self._program_queue.history,
            get_progress=lambda: api.connection.busy_progress,
            on_cancel=self._program_queue.cancel_item,
            on_cancel_all=self._program_queue.cancel_all,
            on_clear_history=self._program_queue.clear_history,
        )
        self._online_runner = OnlineButtonRunner(
            api.project,
            api.connection,
            api.main_thread.submit if api.main_thread is not None else _run_now,
            Logger(api.log, "project"),
            api.notify,
        )

        self._memory_preview = MemoryPreviewWindow(
            get_devices=lambda: api.project.devices
        )
        self._preflight_result = PreflightResultWindow()
        # Live device readouts (read-only "read from device" action), keyed by node id. Written on
        # the async loop thread's done-callback, read on the UI thread -> guarded by a lock.
        self._device_readouts: dict[int, DeviceOverview] = {}
        self._readout_lock = threading.Lock()
        # Copied device configuration (params + GA links) for "Paste configuration".
        self._device_config_clipboard: DeviceConfigClipboard | None = None

        self._devices_panel = DevicesPanel(
            get_devices=lambda: api.project.devices,
            get_areas=self._get_areas,
            get_lines=self._get_lines,
            on_select_device=self._on_select_device,
            on_move_device=self._on_move_device,
            on_create_area=self._on_create_area,
            on_remove_area=self._on_remove_area,
            on_rename_area=self._on_rename_area,
            on_create_line=self._on_create_line,
            on_remove_line=self._on_remove_line,
            on_rename_line=self._on_rename_line,
            get_selected_node_id=self._selected_node_id,
            on_select_devices=self._on_select_devices,
            get_selected_node_ids=lambda: api.project.selected_node_ids,
            on_delete_device=self._on_delete_device,
            on_copy_config=self._on_copy_config,
            on_paste_config=self._on_paste_config,
            on_duplicate_config=self._on_duplicate_config,
            can_paste_config=self._can_paste_config,
            count_paste_targets=self._count_paste_targets,
            get_unloaded_devices=lambda: api.project.unloaded_devices,
        )

        self._configure_panel = ConfigurePanel(
            get_devices=lambda: api.project.devices,
            get_selected_device=lambda: api.project.selected_device,
            set_selected_device=self._set_selected_device,
            on_param_change=self._handle_param_change,
            get_selected_node_ids=lambda: api.project.selected_node_ids,
            on_param_change_selected=self._handle_param_change_selected,
            on_individual_address_change=self._handle_individual_address_change,
            on_name_change=self._handle_name_change,
            on_description_change=self._handle_description_change,
            set_flag=self._handle_flag_change,
            get_links_for_com_object=self._links_for_com_object,
            get_all_group_addresses=self._all_group_addresses,
            on_link_com_object=self._link_com_object,
            on_unlink_com_object=api.project.unlink_com_object_from_ga,
            on_auto_create_gas=self._auto_create_gas,
            get_device_info=api.project.get_device_info,
            on_program_device=self._program_device,
            on_eval_device=self._eval_device,
            open_memory_preview=self._memory_preview.open,
            on_param_change_all=self._handle_param_change_all,
            notify=api.notify,
            get_online_versions=self._online_versions_for_order,
            on_update_application=self._update_application,
            on_read_device_info=self._read_device_info,
            on_restart_device=self._restart_device,
            on_master_reset_device=self._master_reset_device,
            get_device_readout=self._device_readout,
            suggest_ga=self._suggest_ga_for_object,
            on_create_and_link=self._create_and_link_ga,
            group_style=lambda: api.project.group_address_style,
            next_free_sub=self._next_free_sub,
            get_ga_range_tree=api.project.get_group_range_tree,
            render_dali=self._render_dali_tab,
            on_navigate_ga=self._navigate_to_group_address,
            on_paste_links=self._paste_com_object_links,
            buttons=ButtonActions(
                on_click=self._handle_button,
                state=self._button_state,
                error=lambda device, button: device.param_errors.get(button.id),
            ),
            get_help=self._help_text,
            get_icon=self._icon,
            get_picture=self._picture,
        )
        self._dali_panel = DaliCommissioningPanel(self._run_dali)

        self._group_addresses_panel = GroupAddressesPanel(
            get_range_tree=api.project.get_group_range_tree,
            get_assignments_for_ga=api.project.get_assignments_for_ga,
            get_devices=lambda: api.project.devices,
            on_create_ga=self._on_create_ga,
            on_rename_ga=api.project.rename_group_address,
            on_set_ga_dpt=api.project.set_group_address_dpt,
            on_remove_ga=api.project.remove_group_address,
            take_requested_ga=api.project.take_requested_group_address,
            on_create_range=api.project.create_group_range,
            on_rename_range=api.project.rename_group_range,
            on_remove_range=api.project.remove_group_range,
            group_style=lambda: api.project.group_address_style,
            on_select_device_id=self._select_device_by_id,
        )

        self._spaces_panel = SpacesPanel(
            get_space_tree=api.project.get_space_tree,
            on_select_device_id=self._select_device_by_id,
            on_create_function=api.project.create_function,
            on_remove_function=api.project.remove_function,
            on_rename_function=api.project.rename_function,
            on_set_function_type=api.project.set_function_type,
            on_add_function_ga=api.project.add_function_group_address,
            on_remove_function_ga=api.project.remove_function_group_address,
            get_ga_range_tree=api.project.get_group_range_tree,
            on_create_space=api.project.create_space,
            on_rename_space=api.project.rename_space,
            on_set_space_type=api.project.set_space_type,
            on_move_space=api.project.move_space,
            on_remove_space=api.project.remove_space,
            on_set_device_space=api.project.set_device_space,
            get_unassigned_devices=api.project.get_unassigned_devices,
            on_copy_config=self._on_copy_config,
            on_paste_config=self._on_paste_config,
            on_duplicate_config=self._on_duplicate_config,
            can_paste_config=self._can_paste_config,
            count_paste_targets=self._count_paste_targets,
        )

        self._project_info_panel = ProjectInfoPanel(
            get_project_info=api.project.get_project_metadata,
        )
        self._project_log_panel = ProjectLogPanel(
            get_traces=api.project.get_project_traces,
        )

        self._history_panel = HistoryPanel(
            get_entries=self._get_history_entries,
            get_cursor=lambda: api.project.cursor,
            on_jump_to=self._handle_jump_to,
        )

        self._mass_linker_panel = MassLinkerPanel(
            get_devices=lambda: api.project.devices,
            get_range_tree=api.project.get_group_range_tree,
            is_open=lambda: api.project.is_open,
            on_link_ga_co=self._ml_link_ga_co,
            on_link_co_co=self._ml_link_co_co,
            get_selected_node_id=self._selected_node_id,
            group_style=lambda: api.project.group_address_style,
            get_links_for_co=api.project.get_links_for_com_object,
        )

        self._tools_panel = ToolsPanel(
            get_devices=lambda: api.project.devices,
            get_device_info=api.project.get_device_info,
            is_open=lambda: api.project.is_open,
            on_extended_copy=self._tools_extended_copy,
            on_shift_addresses=self._tools_shift_addresses,
            on_navigate=self._select_device_by_id,
            on_replace_device=self._tools_replace_device,
            get_space_path=self._device_space_path,
            get_device_gas=self._device_gas,
        )

        self._panels = [
            PanelDefinition(
                name="devices",
                label=S.PANEL_DEVICES,
                dock="LeftSpace",
                render=self._devices_panel.render,
            ),
            PanelDefinition(
                name="buildings",
                label=S.PANEL_BUILDINGS,
                dock="LeftSpace",
                render=self._spaces_panel.render,
            ),
            PanelDefinition(
                name="group_addresses",
                label=S.PANEL_GROUP_ADDRESSES,
                dock="LeftSpace",
                render=self._group_addresses_panel.render,
            ),
            PanelDefinition(
                name="editor",
                label=S.PANEL_EDITOR,
                dock="MainDockSpace",
                render=self._render_configure,
            ),
            PanelDefinition(
                name="device",
                label=S.PANEL_DEVICE,
                dock="DeviceSpace",
                render=self._render_device,
            ),
            PanelDefinition(
                name="manufacturer",
                label=S.PANEL_MANUFACTURER,
                dock="DeviceSpace",
                render=self._render_manufacturer,
            ),
            PanelDefinition(
                name="mass_linker",
                label=S.PANEL_MASS_LINKER,
                dock="MainDockSpace",
                render=self._mass_linker_panel.render,
            ),
            PanelDefinition(
                name="tools",
                label=S.PANEL_TOOLS,
                dock="MainDockSpace",
                render=self._tools_panel.render,
            ),
            PanelDefinition(
                name="history",
                label=S.PANEL_HISTORY,
                dock="RightSpace",
                render=self._history_panel.render,
            ),
            PanelDefinition(
                name="project_info",
                label=S.PANEL_PROJECT_INFO,
                dock="RightSpace",
                render=self._project_info_panel.render,
            ),
            PanelDefinition(
                name="project_log",
                label=S.PANEL_PROJECT_LOG,
                dock="RightSpace",
                render=self._project_log_panel.render,
            ),
            PanelDefinition(
                name="operations",
                label=S.PANEL_OPERATIONS,
                dock="RightSpace",
                render=self._program_queue_panel.render,
            ),
        ]

    def _get_areas(self) -> list[Area]:
        return [
            Area(id=a.id, number=a.area_number, name=a.name)
            for a in self._api.project.get_areas()
        ]

    def _get_lines(self, area_id: int) -> list[Line]:
        return [
            Line(id=ln.id, area_id=ln.area_id, number=ln.line_number, name=ln.name)
            for ln in self._api.project.get_lines(area_id)
        ]

    def _on_select_device(self, device: "Device") -> None:
        self._api.project.selected_device = device

    def _on_select_devices(self, primary: "Device", node_ids: list[int]) -> None:
        """Devices-tree multi-selection: set the full set + the primary (last-clicked) device."""
        self._api.project.set_multi_selection(primary.node_id, node_ids)

    def _on_copy_config(self, node_id: int) -> None:
        clip = self._api.project.copy_device_config(node_id)
        if clip is not None:
            self._device_config_clipboard = clip

    def _can_paste_config(self, node_id: int) -> bool:
        clip = self._device_config_clipboard
        if clip is None:
            return False
        device = self._api.project.find_device_by_node_id(node_id)
        return device is not None and getattr(device.app, "id", None) == clip.app_id

    def _paste_config_targets(self, node_id: int) -> list[int]:
        """Devices a paste onto ``node_id`` should reach: the whole multi-selection when the clicked
        device is part of it, otherwise just that device. Filtered to devices whose application
        matches the clipboard (paste_device_config is a no-op for a mismatched app)."""
        clip = self._device_config_clipboard
        if clip is None:
            return []
        selected = self._api.project.selected_node_ids
        candidates = (
            selected if node_id in selected and len(selected) > 1 else {node_id}
        )
        targets: list[int] = []
        for nid in candidates:
            device = self._api.project.find_device_by_node_id(nid)
            if device is not None and getattr(device.app, "id", None) == clip.app_id:
                targets.append(nid)
        return targets

    def _count_paste_targets(self, node_id: int) -> int:
        return len(self._paste_config_targets(node_id))

    def _on_paste_config(
        self, node_id: int, include_params: bool, include_links: bool
    ) -> None:
        clip = self._device_config_clipboard
        if clip is None:
            return
        for nid in self._paste_config_targets(node_id):
            self._api.project.paste_device_config(
                nid,
                clip,
                include_params=include_params,
                include_links=include_links,
            )

    def _on_duplicate_config(
        self, node_id: int, include_params: bool, include_links: bool
    ) -> None:
        self._api.project.clone_device(
            node_id,
            1,
            include_params=include_params,
            include_links=include_links,
        )

    def _on_delete_device(self, device: "Device | UnloadedDevice") -> None:
        node_id = device.node_id
        was_selected = self._selected_node_id() == node_id or (
            node_id in self._api.project.selected_node_ids
        )
        self._api.project.remove_device(node_id)
        if was_selected:
            self._api.project.set_multi_selection(None, [])

    def _on_create_area(self, area_number: int, name: str) -> None:
        self._api.project.create_area(area_number, name)

    def _on_remove_area(self, area: Area) -> None:
        self._api.project.remove_area(area.id, area.number, area.name)

    def _on_rename_area(self, area: Area, new_name: str) -> None:
        if area.name != new_name:
            self._api.project.rename_area(area.id, area.name, new_name)

    def _on_create_line(self, area_id: int, line_number: int, name: str) -> None:
        self._api.project.create_line(area_id, line_number, name)

    def _on_remove_line(self, line: Line) -> None:
        self._api.project.remove_line(line.id, line.area_id, line.number, line.name)

    def _on_rename_line(self, line: Line, new_name: str) -> None:
        if line.name != new_name:
            self._api.project.rename_line(line.id, line.name, new_name)

    def _on_move_device(
        self, device: "Device", area_number: int, line_number: int
    ) -> None:
        devices = self._api.project.devices
        used_numbers: set[int] = set()
        for d in devices:
            if not d.individual_address:
                continue
            parts = d.individual_address.split(".")
            if len(parts) < 3:
                continue
            try:
                a, ln, dev = int(parts[0]), int(parts[1]), int(parts[2])
            except ValueError:
                continue
            if a == area_number and ln == line_number:
                used_numbers.add(dev)

        device_number = 1
        while device_number in used_numbers:
            device_number += 1

        new_address = f"{area_number}.{line_number}.{device_number}"
        self._handle_individual_address_change(device, new_address)

    def _set_selected_device(self, device: "Device") -> None:
        self._api.project.selected_device = device

    def _select_device_by_id(self, node_id: int) -> None:
        device = self._api.project.find_device_by_node_id(node_id)
        if device is not None:
            self._api.project.selected_device = device
            # Bring the editor to the front so the jump target is visible (e.g. from the GA
            # assignment list or a Health finding).
            self._api.project.focus_editor()

    def _selected_node_id(self) -> int | None:
        """Node id of the globally selected device (Tools/Mass Linker 'selected device' scope)."""
        device = self._api.project.selected_device
        return device.node_id if device is not None else None

    def _device_room(self, device: "Device") -> str:
        """Name of the space (room) that contains ``device``, or "" if none — used to suggest a
        group-address name. Walks the building/space tree matching the device id."""

        def walk(spaces: list[Any]) -> str | None:
            for space in spaces:
                if any(d.id == device.node_id for d in space.devices):
                    return space.name
                hit = walk(space.children)
                if hit:
                    return hit
            return None

        return walk(self._api.project.get_space_tree()) or ""

    def _device_space_path(self, node_id: int) -> str:
        """Full 'Building / Floor / Room' path of the space containing ``node_id``, or "" if none."""

        def walk(spaces: list[Any], trail: list[str]) -> str | None:
            for space in spaces:
                here = [*trail, space.name]
                if any(d.id == node_id for d in space.devices):
                    return " / ".join(here)
                hit = walk(space.children, here)
                if hit:
                    return hit
            return None

        return walk(self._api.project.get_space_tree(), []) or ""

    def _device_gas(self, device: "Device") -> list[str]:
        """Every group address a device links, as 'address name' strings (deduplicated, in order)."""
        seen: set[str] = set()
        result: list[str] = []
        for co in device.com_objects:
            if co.db_id is None:
                continue
            for assignment in self._api.project.get_links_for_com_object(co.db_id):
                ga = self._api.project.get_group_address(assignment.group_address_id)
                if ga is None:
                    continue
                text = f"{ga.address} {ga.name}".strip()
                if text and text not in seen:
                    seen.add(text)
                    result.append(text)
        return result

    def _suggest_ga_for_object(
        self, device: "Device", com_object: Any
    ) -> tuple[str, str]:
        """Recommendation for a new group address for ``com_object``: the next free address and a
        name combining the device's room, a device identifier, the object/channel name and its
        function, e.g. "Flur - Taster - TW 1 - Switch" (each part included only when present and
        not already covered). The device identifier keeps names unique when several devices with
        the same objects share a room: it prefers the device description, then its short name."""
        address = self._api.project.next_free_group_address() or ""
        parts: list[str] = []
        for token in (
            self._device_room(device),
            self._device_ga_token(device),
            com_object.name or "",
            getattr(com_object, "function_text", "") or "",
        ):
            token = token.strip()
            if token and token not in parts:
                parts.append(token)
        return address, " - ".join(parts)

    def _device_ga_token(self, device: "Device") -> str:
        """Identifier of ``device`` for a group-address name: its description if set, otherwise the
        short name (custom/app name, falling back to product or hardware name)."""
        info = self._api.project.get_device_info(device.node_id)
        candidates = (
            (info.description, info.name, info.product_name, info.hardware_name)
            if info is not None
            else (device.name,)
        )
        for candidate in candidates:
            if candidate and candidate.strip():
                return candidate.strip()
        return ""

    def _next_free_sub(self, main: int, middle: int) -> int:
        """First free sub-group (0..255) within a 3-level main/middle block, so entering "2/1"
        auto-fills the next free third value. Skips 0/0/0."""
        block = (main << 11) | (middle << 8)
        used: set[int] = set()

        def walk(nodes: list[Any]) -> None:
            for node in nodes:
                walk(node.children)
                for ga in node.group_addresses:
                    if block <= ga.address <= block + 255:
                        used.add(ga.address - block)

        walk(self._api.project.get_group_range_tree())
        for sub in range(256):
            if (block | sub) != 0 and sub not in used:
                return sub
        return 0

    def _create_and_link_ga(
        self, device: "Device", com_object: Any, address: str, name: str
    ) -> None:
        """Create a single group address (given address or auto) with the object's DPT and link it
        to ``com_object`` (sending when the object has no sending link yet)."""
        if com_object.db_id is None:
            return
        ga_id = self._api.project.create_group_address(
            address.strip() or None, name.strip() or com_object.name
        )
        if ga_id is None:
            return
        token = self._dpt_token(com_object.dpt)
        if token is not None:
            self._api.project.set_group_address_dpt(ga_id, token)
        existing = self._api.project.get_links_for_com_object(com_object.db_id)
        self._api.project.link_com_object_to_ga(
            com_object.db_id,
            ga_id,
            is_sending=not any(link.is_sending for link in existing),
        )
        self._api.log.info(
            "group address created and linked",
            plugin="project",
            device=device.node_id,
            object=com_object.number,
            ga=ga_id,
        )

    def _online_versions_for_order(self, order_number: str) -> list[int]:
        """Distinct application program versions available online for an order number (descending),
        from the cached catalog index. Empty until the online catalog has been fetched."""
        items = self._api.catalog.online_products_for_order(order_number)
        versions = {
            item.application_version
            for item in items
            if item.application_version is not None
        }
        return sorted(versions, reverse=True)

    def _update_application(self, device: "Device") -> bool:
        """Update the device to the newest available version of its application, keeping parameters
        and links ("Update Application Program"). Imports the newer .knxprod if needed. Returns
        whether an update was applied (the panel shows a success alert on ``True``)."""
        result = self._api.project.update_application(device)
        if self._api.notify is not None:
            if result is None:
                self._api.notify(S.UPDATE_APP_FAILED)
            else:
                self._api.notify(
                    S.UPDATE_APP_DONE.format(
                        version=result.new_version,
                        kept=result.kept,
                        dropped=result.dropped,
                    )
                )
        return result is not None

    def _restart_device(self, device: "Device") -> None:
        """Restart (reboot) the device over the bus (non-destructive)."""
        self._api.connection.restart_device(device)

    def _master_reset_device(self, device: "Device", erase_code: int) -> None:
        """DESTRUCTIVE: master-reset the device (factory reset etc.) over the bus."""
        self._api.connection.master_reset_device(device, erase_code)

    def _render_dali_tab(self, device: "Device") -> None:
        """Render the DALI commissioning tab body (MDT DALI devices), passing bus-connected state."""
        self._dali_panel.render(device, connected=self._api.connection.xknx is not None)

    def _run_dali(self, device: "Device", op: "Any") -> bool:
        """Start a DALI commissioning coroutine over the bus; True if it was accepted (bus free)."""
        return self._api.connection.run_dali(device, op) is not None

    def _read_device_info(self, device: "Device") -> None:
        """Kick off a read-only read of the device's general info over the bus. The result is stored
        (thread-safe) for the Configure panel to display when it arrives."""
        future = self._api.connection.read_device_info(device)
        if future is None:
            return
        future.add_done_callback(
            functools.partial(self._on_device_info, node_id=device.node_id)
        )

    def _on_device_info(self, future: "Future[Any]", node_id: int) -> None:
        """Runs on the async loop thread; stash the readout for the UI thread to pick up."""
        if future.cancelled() or future.exception() is not None:
            return
        with self._readout_lock:
            self._device_readouts[node_id] = future.result()

    def _device_readout(self, node_id: int) -> "DeviceOverview | None":
        with self._readout_lock:
            return self._device_readouts.get(node_id)

    def _on_create_ga(self, address: str, name: str) -> None:
        ga_id = self._api.project.create_group_address(address, name)
        self._api.log.info(
            "group address created",
            plugin="project",
            address=address or "auto",
            name=name,
            id=ga_id,
        )

    def _links_for_com_object(
        self, com_object_db_id: int
    ) -> list[tuple[int, int, str, bool]]:
        result: list[tuple[int, int, str, bool]] = []
        for link in self._api.project.get_links_for_com_object(com_object_db_id):
            ga = self._api.project.get_group_address(link.group_address_id)
            if ga is not None:
                text = f"{ga.address}  {ga.name}" if ga.name else ga.address
                result.append((link.id, link.group_address_id, text, link.is_sending))
        return result

    def _navigate_to_group_address(self, ga_id: int) -> None:
        # Select the clicked link's group address in the Group Addresses tree and bring it to front.
        self._api.project.request_group_address(ga_id)
        self._api.project.focus_group_addresses()

    def _all_group_addresses(self) -> list[tuple[int, str]]:
        # Include the name so the link picker shows it and can be filtered by name, not just address.
        return [
            (g.id, f"{g.address}  {g.name}" if g.name else g.address)
            for g in self._api.project.group_addresses
        ]

    def _link_com_object(self, com_object_db_id: int, group_address_id: int) -> None:
        # The first group address a com-object links to becomes its sending address.
        existing = self._api.project.get_links_for_com_object(com_object_db_id)
        self._api.project.link_com_object_to_ga(
            com_object_db_id, group_address_id, is_sending=not existing
        )

    def _paste_com_object_links(
        self, target_db_id: int, links: list[tuple[int, bool]], replace: bool
    ) -> None:
        # Assign the copied links to the target, preserving sending/receiving. Replace clears the
        # target's current links first; merge keeps them and only adds GAs not already linked (and
        # only sets a sending link if the target has none yet).
        existing = self._api.project.get_links_for_com_object(target_db_id)
        if replace:
            for a in existing:
                self._api.project.unlink_com_object_from_ga(a.id)
            for ga_id, is_sending in links:
                self._api.project.link_com_object_to_ga(
                    target_db_id, ga_id, is_sending=is_sending
                )
            return
        linked_gas = {a.group_address_id for a in existing}
        has_sender = any(a.is_sending for a in existing)
        for ga_id, is_sending in links:
            if ga_id in linked_gas:
                continue
            sending = is_sending and not has_sender
            self._api.project.link_com_object_to_ga(
                target_db_id, ga_id, is_sending=sending
            )
            linked_gas.add(ga_id)
            has_sender = has_sender or sending

    def _auto_create_gas(
        self,
        device: "Device",
        com_objects: list[Any],
        start_address: str = "",
        name_template: str = "{object}",
    ) -> None:
        """Create a group address for each selected com-object and link it as the sending address —
        the bulk 'create group addresses' flow. ``name_template`` supports ``{object}``,
        ``{device}``, ``{room}``, ``{function}``, ``{number}``, ``{dpt}`` and ``{n}`` (batch index,
        1-based; ``{n:02}`` zero-pads); ``start_address`` (optional) seeds sequential addressing,
        else each gets the next free address."""
        from xknxeditor.proj.core.addressing import format_ga, parse_ga

        style = self._api.project.group_address_style
        room = self._device_room(device)
        base: int | None = None
        if start_address:
            try:
                base = parse_ga(start_address, style)
            except (ValueError, IndexError):
                self._api.log.warning(
                    "invalid start address; using auto",
                    plugin="project",
                    address=start_address,
                )
        created = 0
        for i, co in enumerate(com_objects):
            if co.db_id is None:
                continue
            try:
                name = (name_template or "{object}").format(
                    object=co.name,
                    device=device.name,
                    room=room,
                    function=getattr(co, "function_text", "") or "",
                    number=co.number,
                    dpt=getattr(co.dpt, "name", "") or "",
                    n=i + 1,
                )
            except (KeyError, IndexError, ValueError):
                name = co.name or f"{device.name} {co.number}"
            address = format_ga(base + i, style) if base is not None else None
            ga_id = self._api.project.create_group_address(address, name)
            if ga_id is None:
                continue
            token = self._dpt_token(co.dpt)
            if token is not None:
                self._api.project.set_group_address_dpt(ga_id, token)
            existing = self._api.project.get_links_for_com_object(co.db_id)
            self._api.project.link_com_object_to_ga(
                co.db_id, ga_id, is_sending=not existing
            )
            created += 1
        self._api.log.info(
            "auto-created group addresses",
            plugin="project",
            device=device.name,
            count=created,
        )

    @staticmethod
    def _dpt_token(dpt: Any) -> str | None:
        """The GA datapoint-type token for a com-object's DPT (``DPST-x-y`` / ``DPT-x``)."""
        if dpt is None or not dpt.major:
            return None
        # Honor an explicit sub-type (so 16.000 stays DPST-16-0); fall back to the
        # sub-number for fakes that predate the has_subtype flag.
        has_subtype = getattr(dpt, "has_subtype", bool(dpt.minor))
        return f"DPST-{dpt.major}-{dpt.minor}" if has_subtype else f"DPT-{dpt.major}"

    def _ml_link_ga_co(self, pairs: list[tuple[Any, int]]) -> tuple[int, list[str]]:
        """Mass Linker: link each (com-object, existing group-address id) pair. The first group
        address a com-object links to becomes its sending address."""
        count = 0
        errors: list[str] = []
        for co, ga_id in pairs:
            if co.db_id is None:
                errors.append(f"{co.name}: no persistent id")
                continue
            existing = self._api.project.get_links_for_com_object(co.db_id)
            if any(link.group_address_id == ga_id for link in existing):
                errors.append(f"{co.name}: already linked")
                continue
            # First *sending* link only: keep an existing sender rather than adding a second.
            is_sending = not any(link.is_sending for link in existing)
            link_id = self._api.project.link_com_object_to_ga(
                co.db_id, ga_id, is_sending=is_sending
            )
            if link_id is None:
                errors.append(f"{co.name}: link failed")
                continue
            count += 1
        self._api.log.info(
            "mass link object->GA", plugin="project", linked=count, errors=len(errors)
        )
        return count, errors

    def _ml_link_co_co(
        self,
        specs: list[tuple[Any, Any, str, str]],
    ) -> tuple[int, list[str]]:
        """Mass Linker: for each (source, target, address, name) spec, create the group address and
        link both objects to it (source sending, target receiving). ``address`` (a group-address
        string, or empty for auto) and ``name`` are resolved per pair by the panel (first-free
        suggestion + editable override), matching the bulk linker's object<->object tab."""
        errors: list[str] = []
        count = 0
        used_addresses: set[str] = set()
        for src, tgt, address, name in specs:
            if src.db_id is None or tgt.db_id is None:
                errors.append(f"{src.name} <-> {tgt.name}: no persistent id")
                continue
            if src.db_id == tgt.db_id:
                errors.append(f"{src.name}: cannot link an object to itself")
                continue
            addr = address.strip() or None
            name = name.strip() or (src.name or "GA")
            if addr is not None and addr in used_addresses:
                # Two rows target the same explicit address; skip the later ones instead of
                # creating a partially-applied batch (some GAs made, later ones failing).
                errors.append(
                    f"{name}: address {addr} used twice in this batch, skipped"
                )
                continue
            ga_id = self._api.project.create_group_address(addr, name)
            if ga_id is None:
                errors.append(f"{name}: create failed")
                continue
            token = self._dpt_token(src.dpt)
            if token is not None:
                self._api.project.set_group_address_dpt(ga_id, token)
            src_existing = self._api.project.get_links_for_com_object(src.db_id)
            src_link = self._api.project.link_com_object_to_ga(
                src.db_id,
                ga_id,
                is_sending=not any(link.is_sending for link in src_existing),
            )
            tgt_link = self._api.project.link_com_object_to_ga(
                tgt.db_id, ga_id, is_sending=False
            )
            if src_link is None or tgt_link is None:
                # Roll back so a failed pair does not leave a stray/half-linked GA behind.
                self._api.project.remove_group_address(ga_id)
                errors.append(f"{name}: link failed, rolled back")
                continue
            if addr is not None:
                used_addresses.add(addr)
            count += 1
        self._api.log.info(
            "mass link object<->object",
            plugin="project",
            linked=count,
            errors=len(errors),
        )
        return count, errors

    def _tools_extended_copy(
        self, node_id: int, count: int, find: str, replace: str, create_gas: bool
    ) -> tuple[int, list[str]]:
        """Clone a device ``count`` times; optionally rewrite the copy name (find/replace) and
        auto-create a group address for every com-object of each copy."""
        errors: list[str] = []
        try:
            new_ids = self._api.project.clone_device(node_id, count)
        except Exception as exc:
            return 0, [f"clone failed: {exc}"]
        created = 0
        for nid in new_ids:
            dev = self._api.project.find_device_by_node_id(nid)
            if dev is None:
                errors.append(f"copy {nid}: not found after clone")
                continue
            if find:
                # Apply the swap to the clone's own (unique, suffixed) name, not the source name -
                # otherwise every copy collapses to the same name when count > 1.
                new_name = apply_name_swap(dev.name, find, replace)
                if new_name != dev.name:
                    self._api.project.set_device_name(nid, dev.name, new_name)
                    dev.name = new_name
            if create_gas:
                self._auto_create_gas(dev, dev.get_visible_com_objects())
            created += 1
        self._api.log.info(
            "extended copy", plugin="project", source=node_id, copies=created
        )
        return created, errors

    def _tools_replace_device(
        self, target_node_id: int, template_node_id: int
    ) -> tuple[int, list[str]]:
        """Replace a device by a template (another project device), keeping its group-address
        links. Composition: capture the target's links, clone the template, remove the target,
        move the clone to the target's individual address, then re-attach the links to the clone's
        com-objects matched by number + object size. Not atomic (clone/remove are separate events).
        """
        errors: list[str] = []
        target = self._api.project.find_device_by_node_id(target_node_id)
        template = self._api.project.find_device_by_node_id(template_node_id)
        if target is None or template is None:
            return 0, ["device not found"]
        target_ia = target.individual_address
        target_name = target.name
        # number -> (object_size, [(ga_id, is_sending)]) for the target's linked objects.
        old_links: dict[int, tuple[str, list[tuple[int, bool]]]] = {}
        for co in target.get_visible_com_objects():
            if co.db_id is None:
                continue
            links = self._api.project.get_links_for_com_object(co.db_id)
            if links:
                old_links[co.number] = (
                    co.object_size,
                    [(link.group_address_id, link.is_sending) for link in links],
                )
        new_ids = self._api.project.clone_device(template_node_id, 1)
        if not new_ids:
            return 0, ["could not create replacement (application not resolved)"]
        new_id = new_ids[0]
        new_dev = self._api.project.find_device_by_node_id(new_id)
        if new_dev is None:
            return 0, ["replacement device not found after clone"]
        self._api.project.set_device_name(new_id, new_dev.name, target_name)
        new_dev.name = target_name
        self._api.project.remove_device(target_node_id)  # frees the individual address
        self._api.project.set_device_individual_address(
            new_id, new_dev.individual_address or "", target_ia
        )
        new_dev.individual_address = target_ia
        new_by_number = {
            co.number: co
            for co in new_dev.get_visible_com_objects()
            if co.db_id is not None
        }
        mapped = 0
        for number, (size, gas) in old_links.items():
            nco = new_by_number.get(number)
            if nco is None or (size and nco.object_size and size != nco.object_size):
                errors.append(f"object #{number}: no matching object on replacement")
                continue
            assert (
                nco.db_id is not None
            )  # new_by_number only holds objects with a db_id
            for ga_id, is_sending in gas:
                self._api.project.link_com_object_to_ga(
                    nco.db_id, ga_id, is_sending=is_sending
                )
            mapped += 1
        self._api.log.info(
            "replace device",
            plugin="project",
            target=target_node_id,
            template=template_node_id,
            mapped=mapped,
            errors=len(errors),
        )
        return mapped, errors

    def _tools_shift_addresses(
        self, node_ids: list[int], offset: int
    ) -> tuple[int, list[str]]:
        """Shift the device octet of each selected individual address by ``offset``.

        All-or-nothing: the whole destination state is preflighted for out-of-range values and
        collisions (the project service silently drops clashing writes, so we cannot rely on
        per-write feedback). If any conflict is found, nothing is written."""
        devices = self._api.project.devices
        by_id = {d.node_id: d for d in devices}
        selected_ids = [n for n in node_ids if n in by_id]
        errors: list[str] = []

        targets: dict[int, str] = {}
        for nid in selected_ids:
            dev = by_id[nid]
            new = shifted_ia(dev.individual_address, offset)
            if new is None:
                errors.append(
                    f"{dev.name or dev.node_id}: '{dev.individual_address}' "
                    f"+{offset} out of range"
                )
            else:
                targets[nid] = new

        # Full final state: movers take their target, everyone else keeps their address. A shared
        # address string means a real clash (the string includes area.line, so lines never mix).
        final: dict[str, list[Device]] = {}
        for d in devices:
            ia = targets.get(d.node_id, d.individual_address)
            if ia:
                final.setdefault(ia, []).append(d)
        for ia, group in final.items():
            if len(group) > 1:
                who = ", ".join(d.name or str(d.node_id) for d in group)
                errors.append(f"{ia}: would collide ({who})")

        if errors:
            return 0, errors  # abort; write nothing

        def _octet(device: "Device") -> int:
            parts = device.individual_address.split(".")
            return int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else 0

        # Apply movers in the direction that frees each address before it is targeted.
        movers = sorted((by_id[n] for n in targets), key=_octet, reverse=offset > 0)
        changed = 0
        for dev in movers:
            old = dev.individual_address
            if self._api.project.set_device_individual_address(
                dev.node_id, old, targets[dev.node_id]
            ):
                dev.individual_address = targets[dev.node_id]
                changed += 1
        self._api.log.info(
            "shift addresses", plugin="project", offset=offset, changed=changed
        )
        return changed, errors

    def _help_text(self, device: "Device", context: str) -> str | None:
        """A help page from the ContextHelpFile baggage of the device's source .knxprod."""
        help_file = device.app.program.context_help_file
        if not help_file:
            return None
        baggages = self._device_baggages(device)
        return baggages.help_text(help_file, context) if baggages else None

    def _icon(self, device: "Device", name: str) -> Icon | None:
        """An icon from the IconFile baggage of the device's source .knxprod."""
        icon_file = device.app.program.icon_file
        if not icon_file:
            return None
        key = f"{device.app.id}|{name}"
        baggages = self._device_baggages(device) if not has_icon(key) else None
        return load_icon(key, baggages.icon(icon_file, name) if baggages else None)

    def _picture(self, device: "Device", ref_id: str) -> Picture | None:
        """A TypePicture image from the baggages of the device's source .knxprod."""
        key = f"{device.app.id}|{ref_id}"
        baggages = self._device_baggages(device) if not has_picture(key) else None
        return load_picture(key, baggages.file(ref_id) if baggages else None)

    def _device_baggages(self, device: "Device") -> Baggages | None:
        info = self._api.project.get_device_info(device.node_id)
        if info is None or info.hardware2program_ref_id is None:
            return None
        source = self._api.catalog.get_program_source(info.hardware2program_ref_id)
        if source is None:
            return None
        baggages = self._baggages.get(source)
        if baggages is None:
            try:
                baggages = Baggages.from_archive(*source)
            except (OSError, ArchiveError, zipfile.BadZipFile) as exc:
                self._api.log.warning(
                    "help unavailable",
                    plugin="project",
                    source=source[0],
                    error=str(exc),
                )
                baggages = Baggages({})
            self._baggages[source] = baggages
        return baggages

    def _handle_param_change(
        self, device: "Device", param_id: str, new_value: str
    ) -> None:
        try:
            self._api.project.set_param(device, param_id, new_value)
        except ValueError as exc:
            self._api.log.warning(
                "parameter change rejected",
                plugin="project",
                param=param_id,
                error=str(exc),
            )

    def _handle_button(self, device: "Device", button: "UiButton") -> None:
        self._api.log.info(
            "button clicked", plugin="project", button=button.id, handler=button.handler
        )
        if button.online is None:
            self._button_runner.start(device, button)
            return
        self._program_queue.enqueue(
            QueueItem(
                node_id=device.node_id,
                address=device.individual_address or "",
                name=device.name,
                scope=None,
                script=ScriptRun(button),
            )
        )

    def _button_state(
        self, device: "Device", button: "UiButton"
    ) -> tuple[bool, str | None]:
        if device.script_running:
            return False, S.BUTTON_DEVICE_BUSY
        if button.online is not None:
            info = self._api.project.get_device_info(device.node_id)
            if info is None or not info.individual_address_loaded:
                return False, S.BUTTON_NEEDS_ADDRESS_LOADED
        return True, None

    def _handle_param_change_all(
        self, device: "Device", param_id: str, new_value: str
    ) -> None:
        n = self._api.project.set_param_on_matching(device, param_id, new_value)
        self._api.log.info(
            "multi-fill parameter", plugin="project", param=param_id, devices=n
        )

    def _handle_param_change_selected(
        self, node_ids: list[int], param_id: str, new_value: str
    ) -> None:
        n = self._api.project.set_param_on_selected(node_ids, param_id, new_value)
        self._api.log.info(
            "multi-edit parameter", plugin="project", param=param_id, devices=n
        )

    def _handle_individual_address_change(
        self, device: "Device", new_address: str
    ) -> None:
        old_address = device.individual_address
        if old_address == new_address:
            return
        # Persist first; only mirror onto the live object when the project accepted the address.
        # Otherwise a rejected value (e.g. a collision) would linger on the cached device and could
        # be programmed onto hardware.
        if self._api.project.set_device_individual_address(
            device.node_id, old_address, new_address
        ):
            device.individual_address = new_address

    def _handle_name_change(self, device: "Device", new_name: str) -> None:
        old_name = device.name
        if old_name != new_name:
            device.name = new_name
            self._api.project.set_device_name(device.node_id, old_name, new_name)

    def _handle_description_change(
        self, device: "Device", new_description: str
    ) -> None:
        old_description = device.description
        if old_description != new_description:
            device.description = new_description
            self._api.project.set_device_description(
                device.node_id, old_description, new_description
            )

    def _handle_flag_change(
        self, device: "Device", co_id: str, flag_name: str, new_value: bool
    ) -> None:
        self._api.project.set_flag(device, co_id, flag_name, new_value)

    def _program_device(self, device: "Device", scope: "DownloadScope") -> None:
        """Program button: enqueue the device. Runs immediately if the bus is free, otherwise it is
        appended to the programming queue (dedupe per device) and drained one at a time."""
        self._api.log.debug(
            "program: enqueue device",
            plugin="project",
            device=device.individual_address or device.name,
            scope=getattr(scope, "value", str(scope)),
        )
        self._program_queue.enqueue(
            QueueItem(
                node_id=device.node_id,
                address=device.individual_address or "",
                name=device.name,
                scope=scope,
            )
        )

    def _start_program(self, item: QueueItem) -> "Future[Any] | None":
        """Start one queued programming via the normal single-device path (slot, progress, notice,
        commissioning all handled by ``program_device`` + the commissioning callback). Returns the
        Future, or None if it could not start (not connected / device gone)."""
        if item.script is not None:
            return self._online_runner.start(item)
        if item.scope is None:
            return None
        device = self._api.project.find_device_by_node_id(item.node_id)
        if device is None:
            self._api.log.debug(
                "program: cannot start, device gone",
                plugin="project",
                node_id=item.node_id,
            )
            return None
        future = self._api.connection.program_device(
            device, item.scope, self._group_communication_for(device)
        )
        if future is None:
            if self._api.connection.xknx is None:
                item.message = S.SCRIPT_NOT_CONNECTED
            self._api.log.debug(
                "program: cannot start, not connected",
                plugin="project",
                device=item.address or item.name,
            )
            return None
        self._api.log.debug(
            "program: started",
            plugin="project",
            device=item.address or item.name,
            scope=getattr(item.scope, "value", "full"),
        )
        # On success, record what was loaded (per-device "loaded" ticks + last download) so the
        # cockpit and a re-export reflect the device's commissioning state. The done-callback fires on
        # the async loop thread, so the actual project edit is marshalled onto the UI thread.
        node_id = item.node_id
        scope_value = getattr(item.scope, "value", "full")

        def _on_done(f: "Future[Any]") -> None:
            if f.cancelled() or f.exception() is not None:
                self._api.log.debug(
                    "program: finished without commissioning record",
                    plugin="project",
                    device=item.address or item.name,
                    cancelled=f.cancelled(),
                    error=str(f.exception()) if f.exception() else None,
                )
                return
            self._api.log.debug(
                "program: succeeded", plugin="project", device=item.address or item.name
            )
            self._record_commissioning(node_id, scope_value)

        future.add_done_callback(_on_done)
        return future

    # --- programming queue (UI-thread facade for main.py / the queue panel) ---
    def tick_program_queue(self) -> None:
        self._program_queue.tick()

    def take_operations_focus(self) -> bool:
        """Whether an operation was queued since the last call; selects the Active tab if so."""
        if not self._program_queue.take_focus_request():
            return False
        self._program_queue_panel.select_active()
        return True

    def _record_commissioning(self, node_id: int, scope_value: str) -> None:
        flags = _COMMISSIONING_BY_SCOPE.get(scope_value)
        if flags is None:
            return
        now = (
            None
            if scope_value == "unload"
            else datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")
        )

        def _apply() -> None:
            self._api.project.set_device_commissioning(
                node_id, last_download=now, **flags
            )

        if self._api.main_thread is not None:
            self._api.main_thread.submit(_apply)
        else:
            _apply()

    def _eval_device(self, device: "Device", scope: "DownloadScope") -> None:
        label = device.name or device.individual_address or "device"
        # The test reads the live device, so it needs a bus connection; without one
        # there is nothing to compare against.
        if self._api.connection.xknx is None:
            self._preflight_result.submit_error(
                label, scope.name, S.PREFLIGHT_NO_CONNECTION
            )
            return
        # The test validates our image generation against the device's programmed state, which
        # matches the project only as long as it is unedited since it was opened. Opening a
        # previously-edited (saved) project is fine; only edits made in this session block the test.
        if self._api.project.edited_since_open():
            self._preflight_result.submit_error(
                label, scope.name, S.PREFLIGHT_PROJECT_MODIFIED
            )
            return
        runtime = self._runtime_managed_addresses(device)
        driven = self._parameter_driven_bits(device)
        future = self._api.connection.evaluate_device(
            device, scope, self._group_communication_for(device)
        )
        if future is not None:
            future.add_done_callback(
                functools.partial(
                    self._on_eval_done, label, scope.name, runtime, driven
                )
            )

    @staticmethod
    def _runtime_managed_addresses(device: "Device") -> set[int]:
        """Best-effort set of device-managed (runtime) memory addresses; empty on error."""
        from editor_gui.programming import runtime_managed_addresses

        try:
            return runtime_managed_addresses(device)
        except Exception:
            return set()

    @staticmethod
    def _parameter_driven_bits(device: "Device") -> dict[int, int]:
        """Best-effort {address: parameter-driven bitmask}; empty on error."""
        from editor_gui.programming import parameter_driven_bits

        try:
            return parameter_driven_bits(device)
        except Exception:
            return {}

    def _on_eval_done(
        self,
        label: str,
        scope_name: str,
        runtime: set[int],
        driven: dict[int, int],
        future: "Future[Any]",
    ) -> None:
        """Runs on the async loop thread; only hands the result to the (thread-safe) window."""
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._preflight_result.submit_error(
                label, scope_name, f"{type(exc).__name__}: {exc}"
            )
            return
        self._preflight_result.submit_result(
            label, scope_name, future.result(), runtime, driven
        )

    def _group_communication_for(self, device: "Device") -> "GroupCommunication | None":
        """Collect the device's group address links into a GroupCommunication."""
        return self._api.project.group_communication_for(device)

    def _get_history_entries(self):
        return self._api.project.history()

    def _handle_jump_to(self, event_id: int) -> None:
        self._api.project.jump_to(event_id)

    def _render_configure(self) -> None:
        # Focusing this tab when another view selected a device is handled centrally in
        # KnxGuiApp.render_overlays (a background dock tab does not render, so it must be focused
        # from a per-frame global callback, not from here).
        self._sync_selected_device_from_editor()
        self._configure_panel.render()

    def _render_device(self) -> None:
        self._sync_selected_device_from_editor()
        self._configure_panel.render_device()

    def _render_manufacturer(self) -> None:
        self._sync_selected_device_from_editor()
        self._configure_panel.render_manufacturer()

    def _sync_selected_device_from_editor(self) -> None:
        if not self._get_selected_node_ids:
            return
        selected_ids = self._get_selected_node_ids()
        if len(selected_ids) != 1:
            return
        node_id = selected_ids[0]
        if (
            self._api.project.selected_device
            and self._api.project.selected_device.node_id == node_id
        ):
            return
        device = self._api.project.find_device_by_node_id(node_id)
        if device:
            self._api.project.selected_device = device

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def render_overlays(self) -> None:
        self._button_runner.render()
        self._memory_preview.render()
        self._preflight_result.render()

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

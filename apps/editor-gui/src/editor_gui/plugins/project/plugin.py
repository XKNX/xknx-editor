import asyncio
import functools
import secrets
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from xknx.telegram.apci import GroupValueRead, GroupValueWrite

from editor_gui.device_info import (
    same_application,
    serial_from_project,
    serial_text,
    serial_to_project,
)
from editor_gui.dpt_formats import cached_master_formats, cached_master_names
from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.project.button_runner import ButtonRunner
from editor_gui.plugins.project.compare_match import compare_mismatch, matching_devices
from editor_gui.plugins.project.device_info_view import device_info_sections
from editor_gui.plugins.project.ga_name_template import (
    DEFAULT_GA_NAME_TEMPLATE,
    render_name_template,
)
from editor_gui.plugins.project.medium_text import medium_error_text
from editor_gui.plugins.project.online_runner import OnlineButtonRunner
from editor_gui.plugins.project.paste import (
    PasteError,
    PasteKind,
    PasteTarget,
    paste_error_text,
)
from editor_gui.plugins.project.program_queue import (
    ProgramQueue,
    QueueItem,
    ScriptRun,
)
from editor_gui.plugins.project.service import (
    DeviceConfigClipboard,
    ElementDetails,
    additional_address_error_text,
    interface_bus_access,
)
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import (
    ConfigurePanel,
    GroupAddressesPanel,
    HistoryPanel,
    MassLinkerPanel,
    ProjectInfoPanel,
    ProjectLogPanel,
    SpacesPanel,
    ToolsPanel,
    TopologyPanel,
)
from editor_gui.plugins.project.ui.comment import CommentPanel
from editor_gui.plugins.project.ui.dali_commissioning import DaliCommissioningPanel
from editor_gui.plugins.project.ui.device_certificate import (
    CertificateState,
    DeviceCertificateDialog,
)
from editor_gui.plugins.project.ui.device_compare import DeviceComparePanel
from editor_gui.plugins.project.ui.element_details import ElementDetailsPanel
from editor_gui.plugins.project.ui.group_value import GroupValueActions
from editor_gui.plugins.project.ui.memory_preview import MemoryPreviewWindow
from editor_gui.plugins.project.ui.paste_special import PasteSpecialDialog
from editor_gui.plugins.project.ui.plugin_params_panel import PluginParamsPanel
from editor_gui.plugins.project.ui.preflight_result import PreflightResultWindow
from editor_gui.plugins.project.ui.program_queue import ProgramQueuePanel
from editor_gui.plugins.project.ui.project_diff import (
    DeviceSnap,
    DiffOutcome,
    GaSnap,
    ProjectSnapshot,
    diff_projects,
)
from editor_gui.plugins.project.ui.read_device_dialog import ReadDeviceDialog
from editor_gui.plugins.project.ui.secure_commissioning import SecureCommissioningDialog
from editor_gui.plugins.project.ui.siemens_dali import SiemensDaliPanel
from editor_gui.plugins.project.ui.space_details import SpaceDetailsPanel
from editor_gui.plugins.project.ui.tools import apply_name_swap, shifted_ia
from editor_gui.plugins.project.ui.topology import Area, Line, Segment
from editor_gui.widgets import ButtonActions
from editor_gui.widgets.dpt_value import GroupValueBus
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
from xknxeditor.proj.core.medium import MediumError
from xknxeditor.proj.core.paste import PasteOptions

if TYPE_CHECKING:
    from concurrent.futures import Future

    from editor_gui.device import ComObject, Device, UnloadedDevice
    from editor_gui.device_info import DeviceInfo
    from editor_gui.plugin_params import CollectionBinding
    from editor_gui.plugins.project.ui.configure import ProgramRequest
    from xknxeditor.catalog import ProgramSummary
    from xknxeditor.dali import DaliBusState, EcgAssignment, Scene
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.download.ip_secure import IpSecureConfig
    from xknxeditor.download.scope import DownloadScope
    from xknxeditor.prod import Application
    from xknxeditor.prod.parser_v2.ui import UiButton
    from xknxeditor.proj.core.service import SpaceInfo
    from xknxeditor.recover import AppId


# A_Restart_Master_Reset erase code of a factory reset with the individual address.
_FACTORY_RESET = 2

# App settings of KNX Secure (whether adding a secure device asks for its certificate).
_SECURITY_SETTINGS = "security"


def _run_now(fn: Callable[[], None]) -> None:
    fn()


# Bus operations the programming queue starts and shows as its own entries.
_QUEUED_OPERATIONS = frozenset({"script", "program", "device_info"})


def _unqueued(operation: tuple[str, str] | None) -> tuple[str, str] | None:
    """The running bus operation when the queue has no entry for it (a device test, a scan …)."""
    if operation is None or operation[0] in _QUEUED_OPERATIONS:
        return None
    return operation


def _project_version(device: "Device") -> int | None:
    """The version of the device's application in the project (from its id, ``...-26-...`` -> 38),
    else the application's own."""
    if device.app is None:
        return None
    from xknxeditor.prod.app_id import parse_app_id

    parsed = parse_app_id(device.app.id)
    return (
        parsed.version if parsed is not None else device.app.program.application_version
    )


class ProjectPlugin:
    name = "project"

    def __init__(
        self,
        api: PluginAPI,
        get_selected_node_ids: Callable[[], list[int]] | None = None,
        export_group_addresses: Callable[[], None] | None = None,
        get_latest_value: Callable[[str, str | None], tuple[str, str, float] | None]
        | None = None,
        get_interfaces: Callable[[], list[tuple[str, str, int, str]]] = list,
    ) -> None:
        """``get_interfaces`` lists the KNXnet/IP interfaces found on the network, each as
        (name, IP address, port, individual address)."""
        self._api = api
        self._get_selected_node_ids = get_selected_node_ids
        api.project.set_logger(Logger(api.log, "project"))
        self._button_runner = ButtonRunner(api.project, Logger(api.log, "project"))
        # The unqueued bus operation the Operations panel was last brought to the front for.
        self._shown_bus_operation: tuple[str, str] | None = None
        self._baggages: dict[tuple[str, str], Baggages] = {}

        # Programming queue: repeated "Program" presses serialise onto the single bus slot.
        self._program_queue = ProgramQueue(
            is_busy=lambda: api.connection.busy_operation is not None,
            start=self._start_program,
            cancel=api.connection.cancel_operation,
            submit=api.main_thread.submit if api.main_thread is not None else None,
            connection=lambda item: api.connection.connection_label(
                with_address=item.script is None
            ),
            on_finished=self._on_operation_finished,
            describe_error=self._describe_program_error,
            error_text=self._program_error_text,
        )
        self._program_queue_panel = ProgramQueuePanel(
            get_active=lambda: self._program_queue.active,
            get_history=lambda: self._program_queue.history,
            get_progress=lambda: api.connection.busy_progress,
            on_cancel=self._program_queue.cancel_item,
            on_cancel_all=self._program_queue.cancel_all,
            on_clear_history=self._program_queue.clear_history,
            get_bus_operation=self.unqueued_bus_operation,
            is_connecting=lambda item: api.connection.waiting_for_connection(
                item.future
            ),
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
        self._read_device_dialog = ReadDeviceDialog(self._request_device_info)
        # The device a certificate is asked for, while the dialog is open.
        self._certificate_device: Device | None = None
        self._certificate_dialog = DeviceCertificateDialog(
            self._add_certificate, api.project.remove_device_certificate
        )
        api.ask_device_certificate = self._ask_certificate_on_add
        api.queue_program = self._queue_program
        api.cancel_operation = self._program_queue.cancel_item
        self._secure_commissioning_dialog = SecureCommissioningDialog()
        # Live device readouts (read-only "read from device" action), keyed by node id. Written on
        # the async loop thread's done-callback, read on the UI thread -> guarded by a lock.
        # Copied device configuration (params + GA links) for "Paste configuration".
        self._device_config_clipboard: DeviceConfigClipboard | None = None

        self._topology_panel = TopologyPanel(
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
            on_device_info=self._open_read_dialog,
            on_compare=self._open_compare,
            on_select_element=api.project.select_element,
            get_selected_topology=lambda: api.project.selected_topology,
            get_device_segments=api.project.device_segments,
            on_add_segment=self._on_add_segment,
            on_remove_segment=self._on_remove_segment,
            on_move_device_to_segment=self._on_move_device_to_segment,
            on_move_line=lambda line, area: self._report(
                api.project.move_line(line, area)
            ),
            on_move_segment=lambda segment, line: self._report(
                api.project.move_segment(segment, line)
            ),
            one_undo_step=api.project.one_undo_step,
            paste_dialog=self._paste_dialog("topology"),
        )
        self._compare_panel = DeviceComparePanel(
            get_devices=lambda: api.project.devices,
            get_links=lambda db_id: [
                (a.group_address_id, a.is_sending)
                for a in api.project.get_links_for_com_object(db_id)
            ],
            get_address=lambda ga_id: (
                ga.address
                if (ga := api.project.get_group_address(ga_id)) is not None
                else ""
            ),
            get_revision=lambda: api.project.revision,
        )
        self._focus_tools = False

        group_bus = GroupValueBus(
            connected=api.connection.can_reach_bus,
            read=lambda address: api.connection.send_group_value(
                address, GroupValueRead()
            ),
            write=lambda address, payload: api.connection.send_group_value(
                address, GroupValueWrite(payload)
            ),
            formats=lambda: cached_master_formats(api.connection.master),
            names=lambda: cached_master_names(api.connection.master),
        )
        self._object_group_value = GroupValueActions(group_bus, "object_write_value")

        self._configure_panel = ConfigurePanel(
            get_devices=lambda: api.project.devices,
            application_ready=api.project.application_ready,
            get_selected_device=lambda: api.project.selected_device,
            set_selected_device=self._set_selected_device,
            on_param_change=self.handle_param_change,
            get_selected_node_ids=lambda: api.project.selected_node_ids,
            on_param_change_selected=self._handle_param_change_selected,
            on_individual_address_change=self._handle_individual_address_change,
            on_name_change=self._handle_name_change,
            on_description_change=self._handle_description_change,
            set_flag=self._handle_flag_change,
            set_dpt=self._handle_com_object_datapoint_type,
            set_priority=self._handle_com_object_priority,
            set_description=self._handle_com_object_description,
            get_links_for_com_object=self._links_for_com_object,
            get_all_group_addresses=self._all_group_addresses,
            on_link_com_object=self._link_com_object,
            on_unlink_com_object=api.project.unlink_com_object_from_ga,
            on_auto_create_gas=self.auto_create_gas,
            get_device_info=api.project.get_device_info,
            on_program_device=self._program_device,
            on_eval_device=self._eval_device,
            open_memory_preview=self._memory_preview.open,
            on_param_change_all=self._handle_param_change_all,
            notify=api.notify,
            on_update_application=self._update_application,
            get_update_version=self._api.project.newer_application_version,
            get_change_targets=self._api.project.application_change_targets,
            on_change_application=self._change_application,
            on_read_device_info=self._read_device_info,
            on_restart_device=self._restart_device,
            on_master_reset_device=self._master_reset_device,
            suggest_ga=self._suggest_ga_for_object,
            get_name_template=self.ga_name_template,
            set_name_template=self._set_ga_name_template,
            get_latest_value=get_latest_value,
            on_create_and_link=self._create_and_link_ga,
            group_style=lambda: api.project.group_address_style,
            next_free_sub=self._next_free_sub,
            get_ga_range_tree=api.project.get_group_range_tree,
            render_dali=self._render_dali_tab,
            render_siemens_dali=self._render_siemens_dali_tab,
            render_plugin_params=self._render_plugin_params_tab,
            on_navigate_ga=self._navigate_to_group_address,
            on_paste_links=self._paste_com_object_links,
            ga_menu=self._group_object_ga_menu,
            buttons=ButtonActions(
                on_click=self._handle_button,
                state=self._button_state,
                error=lambda device, button: device.param_errors.get(button.id),
            ),
            get_help=self._help_text,
            get_icon=self._icon,
            get_picture=self._picture,
            on_serial_number_change=self._set_serial_number,
            get_status=lambda device: api.project.completion_status(
                "device", device.node_id
            ),
            on_status_change=lambda device, status: api.project.set_completion_status(
                "device", device.node_id, status
            ),
            get_secure_commissioning=api.project.secure_commissioning,
            on_secure_commissioning_change=self._change_secure_commissioning,
            on_add_certificate=self._ask_certificate_for,
            has_certificate=lambda d: (
                api.project.device_certificate_state(d)[0] is not None
            ),
            get_ip_secure=api.project.ip_secure_settings,
            get_routing_blocking=lambda d: (
                api.project.unicast_broadcast_blocking(d)
                if api.project.security_proxy_coupler(d)
                else "-"
            ),
            get_bus_current=api.project.device_bus_current_text,
            on_routing_blocking=api.project.set_unicast_broadcast_blocking,
            on_ip_secure_change=self._change_ip_secure,
            get_additional_address_count=api.project.additional_address_count,
            on_additional_address_change=self._change_additional_address,
            on_assign_additional_addresses=self._assign_additional_addresses,
            get_ip_settings=api.project.ip_settings,
            on_ip_settings_change=api.project.set_ip_settings,
            use_serial_number_default=lambda: api.connection.use_serial_number,
            find_serial_owner=self._serial_owner,
            find_product=api.catalog.find_product,
        )
        self._dali_panel = DaliCommissioningPanel(
            self._run_dali, self._persist_dali_backup, self._dali_channel_count
        )
        self._siemens_dali_panel = SiemensDaliPanel(self._run_siemens_dali)
        self._plugin_params_panel = PluginParamsPanel(
            self.handle_param_change,
            self.add_plugin_collection_item,
            self.remove_plugin_collection_item,
            self._handle_program_dca,
        )

        self._group_addresses_panel = GroupAddressesPanel(
            get_range_tree=api.project.get_group_range_tree,
            on_select_element=api.project.select_element,
            get_assignments_for_ga=api.project.get_assignments_for_ga,
            get_devices=lambda: api.project.devices,
            on_create_ga=self._on_create_ga,
            on_rename_ga=api.project.rename_group_address,
            on_remove_ga=api.project.remove_group_address,
            take_requested_ga=api.project.take_requested_group_address,
            on_create_range=api.project.create_group_range,
            on_rename_range=api.project.rename_group_range,
            on_remove_range=api.project.remove_group_range,
            group_style=lambda: api.project.group_address_style,
            on_select_device_id=self._select_device_by_id,
            bus=group_bus,
            on_import=api.project.import_group_addresses,
            on_export=export_group_addresses,
            get_latest_value=get_latest_value,
            on_move_ga=api.project.move_group_address,
            on_move_range=api.project.move_group_range,
            notify=lambda text: api.notify(text) if api.notify is not None else None,
            one_undo_step=api.project.one_undo_step,
            paste_dialog=self._paste_dialog("group_addresses"),
        )

        self._element_details = ElementDetailsPanel(
            on_rename=api.project.rename_element,
            on_set_description=api.project.set_element_description,
            on_set_address=api.project.set_element_address,
            on_set_status=api.project.set_completion_status,
            on_set_medium=api.project.set_element_medium,
            on_set_security=api.project.set_element_security,
            on_set_central=api.project.set_group_address_central,
            on_set_ip_routing=lambda latency, address: api.project.set_ip_routing(
                latency=latency, multicast_address=address
            ),
            on_set_backbone_security=api.project.set_element_backbone_security,
            on_set_bus_connection=api.project.set_backbone_bus_connection,
            found_interfaces=lambda: [
                (
                    interface_bus_access(name, host, port),
                    f"{name} ({host}:{port} Udp - {address})".replace(" - )", ")"),
                )
                for name, host, port, address in get_interfaces()
            ],
            on_set_dpt=api.project.set_element_dpt,
            on_set_domain_address=api.project.set_element_domain_address,
            on_set_number=lambda trade_id, number: api.project.set_trade_field(
                trade_id, "number", number
            ),
            get_bus_load=api.project.bus_load_texts,
            dpt_names=lambda: cached_master_names(api.connection.master),
        )
        self._space_details = SpaceDetailsPanel(
            on_rename=api.project.rename_space,
            on_set_description=api.project.set_space_description,
            on_set_number=api.project.set_space_number,
            on_set_usage=api.project.set_space_usage,
            get_status=lambda space_id: api.project.completion_status(
                "space", space_id
            ),
            on_set_status=lambda space_id, status: api.project.set_completion_status(
                "space", space_id, status
            ),
            line_choices=api.project.line_choices,
            get_current_line=api.project.current_line,
            on_set_current_line=api.project.set_space_default_line,
        )
        self._spaces_panel = SpacesPanel(
            get_space_tree=api.project.get_space_tree,
            on_select_device_id=self._select_device_by_id,
            on_create_function=api.project.create_function,
            on_remove_function=api.project.remove_function,
            on_move_function=api.project.move_function,
            on_rename_function=api.project.rename_function,
            on_set_function_type=api.project.set_function_type,
            on_add_function_ga=api.project.add_function_group_address,
            on_remove_function_ga=api.project.remove_function_group_address,
            get_ga_range_tree=api.project.get_group_range_tree,
            on_create_space=api.project.create_space,
            get_selected_space_id=lambda: api.project.selected_space_id,
            on_select_space=self._select_space,
            on_move_space=api.project.move_space,
            on_remove_space=api.project.remove_space,
            on_set_device_space=api.project.set_device_space,
            get_unassigned_devices=api.project.get_unassigned_devices,
            on_copy_config=self._on_copy_config,
            on_paste_config=self._on_paste_config,
            on_duplicate_config=self._on_duplicate_config,
            can_paste_config=self._can_paste_config,
            count_paste_targets=self._count_paste_targets,
            on_device_info=self._open_read_dialog,
            on_compare=self._open_compare,
            # A function's and a trade's details (name, description, status) show in the Device
            # tab.
            on_select_element=api.project.select_element,
            get_selected_node_id=self._selected_node_id,
            get_trade_tree=api.project.get_trade_tree,
            on_create_trade=api.project.create_trade,
            on_move_trade=api.project.move_trade,
            on_remove_trade=api.project.remove_trade,
            on_add_trade_device=api.project.add_trade_device,
            on_remove_trade_device=api.project.remove_trade_device,
            get_space_devices=api.project.get_space_devices,
            notify=api.notify if api.notify is not None else lambda _t: None,
            one_undo_step=api.project.one_undo_step,
            paste_dialog=self._paste_dialog("buildings"),
            on_delete_device=self._delete_device,
        )

        self._project_info_panel = ProjectInfoPanel(
            get_project_info=api.project.get_project_metadata,
            on_rename=api.project.set_project_name,
            on_set_password=api.project.set_project_password,
            needs_password=api.project.project_needs_password,
            get_certificates=api.project.device_certificate_list,
            on_remove_certificate=api.project.remove_device_certificate,
            on_add_certificate=self._add_project_certificate,
            get_status=lambda: api.project.completion_status("project", 0),
            on_set_status=lambda status: api.project.set_completion_status(
                "project", 0, status
            ),
        )
        self._project_log_panel = ProjectLogPanel(
            get_traces=api.project.get_project_traces,
        )

        self._comment_panel = CommentPanel(api.project)
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
            on_link_co_co=self.mass_link_objects,
            get_selected_node_id=self._selected_node_id,
            group_style=lambda: api.project.group_address_style,
            get_links_for_co=api.project.get_links_for_com_object,
        )

        self._tools_panel = ToolsPanel(
            get_devices=lambda: api.project.devices,
            get_device_info=api.project.get_device_info,
            is_open=lambda: api.project.is_open,
            on_extended_copy=self.tools_extended_copy,
            on_shift_addresses=self.tools_shift_addresses,
            on_navigate=self._select_device_by_id,
            on_replace_device=self.tools_replace_device,
            get_space_path=self._device_space_path,
            get_device_gas=self._device_gas,
            get_group_addresses=lambda: api.project.group_addresses,
            get_group_address_style=lambda: api.project.group_address_style,
            on_shift_group_addresses=self.tools_shift_group_addresses,
            on_diff_projects=self.tools_diff_projects,
            compare=self._compare_panel,
        )

        self._panels = [
            PanelDefinition(
                name="topology",
                label=S.PANEL_TOPOLOGY,
                dock="LeftSpace",
                render=self._topology_panel.render,
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
                name="comment",
                label=S.PANEL_COMMENT,
                dock="DeviceSpace",
                render=self._comment_panel.render,
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
                dock="BottomSpace",
                render=self._project_info_panel.render,
            ),
            PanelDefinition(
                name="project_log",
                label=S.PANEL_PROJECT_LOG,
                dock="BottomSpace",
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
            Line(
                id=ln.id,
                area_id=ln.area_id,
                number=ln.line_number,
                name=ln.name,
                segments=[Segment(id=s.id, name=s.name) for s in ln.segments],
                medium_type=ln.medium_type,
                main_segment_id=ln.main_segment_id,
            )
            for ln in self._api.project.get_lines(area_id)
        ]

    def _on_add_segment(self, line: Line) -> None:
        try:
            segment_id = self._api.project.add_segment(line.id)
        except MediumError as error:
            self._medium_error(error)
            return
        if segment_id is not None:
            self._api.project.select_element("segment", segment_id)

    def _on_remove_segment(self, segment: Segment) -> None:
        try:
            self._api.project.remove_segment(segment.id)
        except MediumError as error:
            self._medium_error(error)

    def _on_move_device_to_segment(self, device: "Device", segment: Segment) -> None:
        try:
            self._api.project.move_device_to_segment(device.node_id, segment.id)
        except MediumError as error:
            self._medium_error(error)
        except ValueError as error:
            self._api.log.warning(
                "could not move the device", plugin="project", error=str(error)
            )

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
        self._delete_device(device.node_id)

    def _delete_device(self, node_id: int) -> None:
        """Delete a device from the project (undoable); it is no longer selected."""
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
        # The lowest number of the line that neither a device nor an additional address uses.
        device_number = (
            self._api.project.next_free_device_number(area_number, line_number) or 1
        )
        new_address = f"{area_number}.{line_number}.{device_number}"
        self._handle_individual_address_change(device, new_address)

    def _set_selected_device(self, device: "Device") -> None:
        self._api.project.selected_device = device

    def _select_space(self, space_id: int) -> None:
        self._api.project.selected_space_id = space_id

    def selected_element(self) -> "ElementDetails | None":
        """The area, line, group range or group address selected (its details replace the
        device's), if any."""
        selected = self._api.project.selected_element
        return (
            None if selected is None else self._api.project.element_details(*selected)
        )

    def selected_space(self) -> "SpaceInfo | None":
        """The space selected in the Buildings tree (its details replace the device's), if any."""
        space_id = self._api.project.selected_space_id
        return self._api.project.find_space(space_id) if space_id is not None else None

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
        name rendered from the program-wide name template (see :mod:`ga_name_template`), filled with
        the device's building/floor/room, identifiers, and the object's name and function. The
        default template reproduces "Flur-Taster-TW 1-Switch"."""
        address = self._api.project.next_free_group_address() or ""
        values = self._ga_name_values(device, com_object)
        return address, render_name_template(self.ga_name_template(), values)

    def _ga_name_values(self, device: "Device", com_object: Any) -> dict[str, object]:
        """Placeholder values for the group-address name template (see :mod:`ga_name_template`),
        shared by the single-link suggestion and the bulk create flow."""
        building, floor, room = self._device_space_fields(device.node_id)
        info = self._api.project.get_device_info(device.node_id)
        return {
            "building": building,
            "floor": floor,
            "room": room,
            "device": self._device_ga_token(device),
            "devicename": (getattr(info, "name", "") or device.name or "").strip(),
            "devicedescription": (getattr(info, "description", "") or "").strip(),
            "product": getattr(info, "product_name", "") or "",
            "manufacturer": getattr(info, "manufacturer_name", "") or "",
            "order": getattr(info, "order_number", "") or "",
            "address": getattr(info, "individual_address", "") or "",
            "object": com_object.name or "",
            "ganame": com_object.name or "",
            "function": getattr(com_object, "function_text", "") or "",
            "number": getattr(com_object, "number", "") or "",
            "dpt": getattr(getattr(com_object, "dpt", None), "name", "") or "",
        }

    def ga_name_template(self) -> str:
        """The program-wide group-address name template (``app.json``), or the default."""
        from editor_gui.settings import load_settings

        return str(
            load_settings("app").get("ga_name_template") or DEFAULT_GA_NAME_TEMPLATE
        )

    def _set_ga_name_template(self, template: str) -> None:
        """Persist the group-address name template program-wide (``app.json``)."""
        from editor_gui.settings import load_settings, save_settings

        data = load_settings("app")
        data["ga_name_template"] = template
        save_settings("app", data)

    def _device_space_fields(self, node_id: int) -> tuple[str, str, str]:
        """(building, floor, room) names of the space holding ``node_id``: building and floor by
        their space type, room the direct container. Each "" when absent."""
        building_types = {"Building", "BuildingPart"}

        def walk(
            spaces: list[Any], building: str, floor: str
        ) -> tuple[str, str, str] | None:
            for space in spaces:
                here_building = (
                    space.name if space.space_type in building_types else building
                )
                here_floor = space.name if space.space_type == "Floor" else floor
                if any(d.id == node_id for d in space.devices):
                    return here_building, here_floor, space.name
                hit = walk(space.children, here_building, here_floor)
                if hit is not None:
                    return hit
            return None

        return walk(self._api.project.get_space_tree(), "", "") or ("", "", "")

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

    def _in_background(self, text: str, work: Callable[[], None]) -> None:
        """Run ``work`` behind the progress modal, or directly without one."""
        if self._api.run_background is not None:
            self._api.run_background(text, work)
        else:
            work()

    def _update_application(self, device: "Device") -> None:
        """Update the device to the local application version that replaces its own ("Update
        Application Program Version")."""

        def work() -> None:
            result = self._api.project.update_application(device)
            if result is None and self._api.notify is not None:
                self._api.notify(S.UPDATE_APP_FAILED)

        self._in_background(S.UPDATING_APPLICATION, work)

    def _change_application(self, device: "Device", program: "ProgramSummary") -> None:
        """Switch the device to another application program of its hardware, from that
        application's defaults ("Change Application Program")."""

        def work() -> None:
            changed = self._api.project.change_application(device, program)
            if not changed and self._api.notify is not None:
                self._api.notify(S.CHANGE_APP_FAILED)

        self._in_background(S.CHANGING_APPLICATION, work)

    def _restart_device(self, device: "Device") -> None:
        """Restart (reboot) the device over the bus (non-destructive)."""
        self._api.connection.restart_device(device)

    def _master_reset_device(self, device: "Device", erase_code: int) -> None:
        """DESTRUCTIVE: master-reset the device (factory reset etc.) over the bus. A factory reset
        with its address (erase code 2) gives a secure device its FDSK back, which the project
        records."""
        future = self._api.connection.master_reset_device(device, erase_code)
        if future is None or erase_code != _FACTORY_RESET:
            return

        def done(f: "Future[Any]") -> None:
            if f.cancelled() or f.exception() is not None:
                return
            apply = functools.partial(self._api.project.record_factory_reset, device)
            if self._api.main_thread is not None:
                self._api.main_thread.submit(apply)
            else:
                apply()

        future.add_done_callback(done)

    def _render_dali_tab(self, device: "Device") -> None:
        """Render the DALI commissioning tab body (MDT DALI devices), passing bus-connected state."""
        self._dali_panel.render(device, connected=self._api.connection.can_reach_bus())

    def _run_dali(self, device: "Device", op: "Any", channel: int = 0) -> bool:
        """Start a DALI commissioning coroutine over the bus; True if it was accepted (bus free)."""
        return self._api.connection.run_dali(device, op, channel=channel) is not None

    def _dali_channel_count(self, device: "Device") -> int:
        """How many DALI bus channels this gateway has (1 for a 1x64, 2 for a 2x64)."""
        from editor_gui.programming_dali import dali_channel_count

        info = self._api.project.get_device_info(device.node_id)
        order = getattr(info, "order_number", "") if info is not None else ""
        return dali_channel_count(order if isinstance(order, str) else "")

    def _persist_dali_backup(
        self,
        device: "Device",
        scan: "DaliBusState",
        assignments: "list[EcgAssignment]",
        scenes: "list[Scene]",
        channel: int,
    ) -> None:
        """Write a DALI channel's state back into the project blob after a bus commissioning
        run (see :func:`editor_gui.programming_dali.persist_dali_backup`)."""
        from editor_gui.programming_dali import persist_dali_backup

        persist_dali_backup(
            self._api.project,
            device,
            scan,
            assignments,
            scenes,
            channel,
            label=S.DALI_PERSIST_LABEL,
        )

    def _render_siemens_dali_tab(self, device: "Device") -> None:
        """Render the Siemens N141/03 DALI read-only snapshot tab body."""
        self._siemens_dali_panel.render(
            device, connected=self._api.connection.can_reach_bus()
        )

    def _run_siemens_dali(self, device: "Device", op: "Any") -> bool:
        """Start a Siemens DALI read coroutine over the bus; True if the bus slot was free."""
        return self._api.connection.run_siemens_dali(device, op) is not None

    def _render_plugin_params_tab(self, device: "Device") -> None:
        """Render the generic sectioned view of the device's raw plugin parameters."""
        self._plugin_params_panel.render(device)

    def add_plugin_collection_item(
        self,
        device: "Device",
        binding: "CollectionBinding",
        type_id: str,
        name: str,
    ) -> None:
        """Generic Add dispatcher. Picks the vendor-specific writer based on the active
        row's ref_id prefix (``siemens_blob:`` for N141/03 today)."""
        from sqlalchemy.orm import Session

        from editor_gui.plugin_blob_readers.siemens_dali.writer import (
            EditError,
            add_item,
        )

        entry = next(
            (
                e
                for e in getattr(device, "binary_data", []) or []
                if e.data and "BLOB_PARA" in (e.name or "")
            ),
            None,
        )
        if entry is None:
            self._api.log.warning(
                "add collection item: device has no BLOB_PARA",
                plugin="project",
                device=device.individual_address,
            )
            return
        try:
            entry.data = add_item(entry.data, binding, type_id, name)
        except EditError as exc:
            self._api.log.warning(
                "add collection item rejected",
                plugin="project",
                device=device.individual_address,
                container=binding.container_tag,
                error=str(exc),
            )
            return
        session = Session.object_session(entry)
        if session is not None:
            session.commit()
        self._api.log.info(
            "collection item added",
            plugin="project",
            device=device.individual_address,
            container=binding.container_tag,
            name=name,
            type=type_id,
        )

    def remove_plugin_collection_item(
        self,
        device: "Device",
        binding: "CollectionBinding",
        item_id: str,
    ) -> None:
        from sqlalchemy.orm import Session

        from editor_gui.plugin_blob_readers.siemens_dali.writer import (
            EditError,
            remove_item,
        )

        entry = next(
            (
                e
                for e in getattr(device, "binary_data", []) or []
                if e.data and "BLOB_PARA" in (e.name or "")
            ),
            None,
        )
        if entry is None:
            self._api.log.warning(
                "remove collection item: device has no BLOB_PARA",
                plugin="project",
                device=device.individual_address,
            )
            return
        try:
            entry.data = remove_item(entry.data, binding, item_id)
        except EditError as exc:
            self._api.log.warning(
                "remove collection item rejected",
                plugin="project",
                device=device.individual_address,
                container=binding.container_tag,
                item=item_id,
                error=str(exc),
            )
            return
        session = Session.object_session(entry)
        if session is not None:
            session.commit()
        self._api.log.info(
            "collection item removed",
            plugin="project",
            device=device.individual_address,
            container=binding.container_tag,
            item=item_id,
        )

    def _handle_program_dca(self, device: "Device") -> None:
        """Program the Siemens N141/03 DaliDevice property-image onto the device over the
        live bus. The project plugin forwards this to ``ConnectionService``; the actual
        write pass lives in ``plugin_blob_readers.siemens_dali.download``."""
        entry = next(
            (
                e
                for e in getattr(device, "binary_data", []) or []
                if e.data and "BLOB_PARA" in (e.name or "")
            ),
            None,
        )
        if entry is None:
            self._api.log.warning(
                "program DCA: no BLOB_PARA on device",
                plugin="project",
                device=device.individual_address,
            )
            return
        future = self._api.connection.run_siemens_dca_program(device, entry.data)
        if future is None:
            self._api.log.warning(
                "program DCA: cannot start (bus busy or not connected)",
                plugin="project",
                device=device.individual_address,
            )

    def _read_device_info(self, device: "Device") -> None:
        self._open_read_dialog(device.node_id)

    def _open_read_dialog(self, node_id: int) -> None:
        """Ask whether to include the group communication, then queue the read-only readout."""
        device = self._api.project.find_device_by_node_id(node_id)
        if device is None:
            return
        label = f"{device.individual_address or ''} {device.name}".strip()
        self._read_device_dialog.open(node_id, label)

    def _open_compare(self, node_id: int) -> None:
        """Show "Compare Devices" in the Tools panel, with ``node_id`` as the first device."""
        self._tools_panel.open_compare(node_id)
        self._focus_tools = True

    def take_focus_tools(self) -> bool:
        """Whether the Tools panel was asked for since the last call (the app brings it to the
        front)."""
        asked, self._focus_tools = self._focus_tools, False
        return asked

    def _format_group_address(self, address: int) -> str:
        from xknxeditor.proj.core.addressing import format_ga

        return format_ga(address, self._api.project.group_address_style)

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

    def _group_object_ga_menu(self, ga_id: int, com_object: "ComObject") -> None:
        """Read/Write Value on a group address of a group object; a group address without a data
        type is written as the object's type."""
        ga = self._api.project.get_group_address(ga_id)
        if ga is None:
            return
        self._object_group_value.render_menu_items(
            ga.address, ga.name, ga.datapoint_type or com_object.dpt.token
        )

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

    def auto_create_gas(
        self,
        device: "Device",
        com_objects: list[Any],
        start_address: str = "",
        name_template: str = "{object}",
    ) -> None:
        """Create a group address for each selected com-object and link it as the sending address —
        the bulk 'create group addresses' flow. ``name_template`` supports every placeholder of the
        single-link name template (see :mod:`ga_name_template`) plus ``{n}`` (batch index, 1-based;
        ``{n:02}`` zero-pads); ``start_address`` (optional) seeds sequential addressing, else each
        gets the next free address."""
        from xknxeditor.proj.core.addressing import format_ga, parse_ga

        style = self._api.project.group_address_style
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
            values = self._ga_name_values(device, co)
            values["n"] = i + 1
            name = render_name_template(name_template or "{object}", values) or (
                co.name or f"{device.name} {co.number}"
            )
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

    def mass_link_objects(
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

    def tools_extended_copy(
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
                self.auto_create_gas(dev, dev.get_visible_com_objects())
            created += 1
        self._api.log.info(
            "extended copy", plugin="project", source=node_id, copies=created
        )
        return created, errors

    def tools_replace_device(
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

    def tools_shift_addresses(
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

    def tools_shift_group_addresses(
        self, ga_ids: list[int], offset: int
    ) -> tuple[int, list[str]]:
        """Shift the raw value of each selected group address by ``offset``.

        Mirrors the individual-address shift: the full destination state is preflighted for
        out-of-range values and collisions (against moving and stationary addresses alike), and
        nothing is written if any conflict is found. Com-object links are preserved because the
        project renumbers via its change-address command. Moves are applied in the order that
        frees each target before it is used, so no intermediate write clashes."""
        from xknxeditor.proj.core.addressing import format_ga

        gas = self._api.project.group_addresses
        by_id = {g.id: g for g in gas}
        selected_ids = [g for g in ga_ids if g in by_id]
        style = self._api.project.group_address_style
        errors: list[str] = []

        targets: dict[int, int] = {}
        for gid in selected_ids:
            ga = by_id[gid]
            new = ga.raw + offset
            if not 1 <= new <= 0xFFFF:
                errors.append(f"{ga.address} {ga.name}: offset leaves the valid range")
            else:
                targets[gid] = new

        # Full final state: movers take their target raw value, everyone else keeps theirs.
        final: dict[int, list[str]] = {}
        for g in gas:
            raw = targets.get(g.id, g.raw)
            final.setdefault(raw, []).append(g.name or str(g.id))
        for raw, who in final.items():
            if len(who) > 1:
                errors.append(
                    f"{format_ga(raw, style)}: would collide ({', '.join(who)})"
                )

        if errors:
            return 0, errors  # abort; write nothing

        # Apply in the direction that frees each target before it is written to.
        movers = sorted(
            (by_id[g] for g in targets), key=lambda g: g.raw, reverse=offset > 0
        )
        changed = 0
        for ga in movers:
            if self._api.project.set_group_address_address(
                ga.id, format_ga(targets[ga.id], style)
            ):
                changed += 1
        self._api.log.info(
            "shift group addresses", plugin="project", offset=offset, changed=changed
        )
        return changed, errors

    def tools_diff_projects(
        self, other_path: str, password: str | None = None
    ) -> DiffOutcome:
        """Compare the open project against the project at ``other_path`` (.xknx or .knxproj),
        opened with ``password`` when it is protected.

        On failure the outcome has no result but the message to show, and ``needs_password`` when
        the other project needs a (different) password. The other project is opened read-only on
        its own engine, so the open project is untouched."""
        from xknxproject.exceptions import InvalidPasswordException

        from xknxeditor.proj import InvalidProjectPassword, ProjectPasswordRequired

        try:
            base = self._project_snapshot_current()
            other = self._load_project_snapshot(other_path, password)
        except (
            InvalidPasswordException,
            InvalidProjectPassword,
            ProjectPasswordRequired,
        ):
            error = S.TOOLS_DIFF_WRONG_PASSWORD if password else ""
            return DiffOutcome(None, error, needs_password=True)
        except Exception as e:
            self._api.log.warning(
                "project diff failed",
                plugin="project",
                error=f"{type(e).__name__}: {e}",
            )
            return DiffOutcome(None, str(e))
        return DiffOutcome(diff_projects(base, other))

    def _project_snapshot_current(self) -> ProjectSnapshot:
        """Snapshot the open project from the GUI service (already loaded and cached)."""
        devices: dict[str, DeviceSnap] = {}
        for d in self._api.project.devices:
            ia = d.individual_address or ""
            if not ia:
                continue
            info = self._api.project.get_device_info(d.node_id)
            devices[ia] = DeviceSnap(
                ia=ia,
                name=d.name,
                product=info.product_name if info else "",
                order=info.order_number if info else "",
            )
        gas = {
            g.address: GaSnap(
                address=g.address, name=g.name, dpt=g.datapoint_type or ""
            )
            for g in self._api.project.group_addresses
        }
        return ProjectSnapshot(name="current", devices=devices, group_addresses=gas)

    def _load_project_snapshot(
        self, path: str, password: str | None = None
    ) -> ProjectSnapshot:
        """Open a project file read-only and snapshot it (a .knxproj is converted first).
        ``password`` opens a password-protected project or archive."""
        from xknxeditor.proj import ProjectService as CoreProjectService
        from xknxeditor.proj import import_knxproj

        src = Path(path)
        tmpdir: tempfile.TemporaryDirectory[str] | None = None
        try:
            if src.suffix.lower() == ".xknx":
                xknx_path = src
            else:
                tmpdir = tempfile.TemporaryDirectory(prefix="xknx-diff-")
                xknx_path = Path(tmpdir.name) / "other.xknx"
                # The temporary conversion is protected like any project: with the archive's
                # password, else (KNX Secure data in an unprotected archive) a throwaway one.
                password = password or secrets.token_urlsafe(16)
                import_knxproj(
                    src, xknx_path, password=password, project_password=password
                )
            svc = CoreProjectService()
            pid = svc.open(xknx_path, password)
            try:
                devices: dict[str, DeviceSnap] = {}
                for d in svc.devices(pid):
                    info = svc.device(pid, d.id)
                    ia = info.individual_address or ""
                    if not ia:
                        continue
                    devices[ia] = DeviceSnap(
                        ia=ia,
                        name=info.name,
                        product=info.product_name,
                        order=info.order_number,
                    )
                gas = {
                    g.text: GaSnap(
                        address=g.text, name=g.name, dpt=g.datapoint_type or ""
                    )
                    for g in svc.group_addresses(pid)
                }
            finally:
                svc.close(pid)
            return ProjectSnapshot(name=src.stem, devices=devices, group_addresses=gas)
        finally:
            if tmpdir is not None:
                tmpdir.cleanup()

    def _help_text(self, device: "Device", context: str) -> str | None:
        """A help page from the ContextHelpFile baggage of the device's source .knxprod."""
        if device.app is None or not device.app.loaded:
            return None
        help_file = device.app.program.context_help_file
        if not help_file:
            return None
        baggages = self._device_baggages(device)
        return baggages.help_text(help_file, context) if baggages else None

    def _icon(self, device: "Device", name: str) -> Icon | None:
        """An icon from the IconFile baggage of the device's source .knxprod."""
        if device.app is None or not device.app.loaded:
            return None
        icon_file = device.app.program.icon_file
        if not icon_file:
            return None
        key = f"{device.app.id}|{name}"
        baggages = self._device_baggages(device) if not has_icon(key) else None
        return load_icon(key, baggages.icon(icon_file, name) if baggages else None)

    def _picture(self, device: "Device", ref_id: str) -> Picture | None:
        """A TypePicture image from the baggages of the device's source .knxprod."""
        if device.app is None:
            return None
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

    def handle_param_change(
        self, device: "Device", param_id: str, new_value: str
    ) -> None:
        # BLOB-backed rows (Siemens N141 DCA plug-in state) take a dedicated write path:
        # locate the DeviceBinaryData entry, call the vendor writer, replace the compressed
        # payload. The standard set_param only knows ParameterRef ids.
        if param_id.startswith("siemens_blob:"):
            self._apply_siemens_blob_edit(device, param_id, new_value)
            return
        try:
            self._api.project.set_param(device, param_id, new_value)
        except ValueError as exc:
            self._api.log.warning(
                "parameter change rejected",
                plugin="project",
                param=param_id,
                error=str(exc),
            )

    def _apply_siemens_blob_edit(
        self, device: "Device", ref_id: str, new_value: str
    ) -> None:
        from sqlalchemy.orm import Session

        from editor_gui.plugin_blob_readers.siemens_dali.writer import (
            EditError,
            apply_edit,
        )

        entries = [
            e for e in getattr(device, "binary_data", []) or [] if e.data is not None
        ]
        if not entries:
            self._api.log.warning(
                "siemens blob edit: device has no binary_data entry",
                plugin="project",
                device=device.individual_address,
                ref=ref_id,
            )
            return
        # The Siemens plug-in writes a single BLOB_PARA per device; pick it up by name.
        entry = next((e for e in entries if "BLOB_PARA" in (e.name or "")), entries[0])
        try:
            new_blob = apply_edit(entry.data, ref_id, new_value)
        except EditError as exc:
            self._api.log.warning(
                "siemens blob edit rejected",
                plugin="project",
                device=device.individual_address,
                ref=ref_id,
                error=str(exc),
            )
            return
        entry.data = new_blob
        # Persist the mutation on the ORM session that owns this entry; without an explicit
        # commit the change would survive in memory but be dropped on project close.
        session = Session.object_session(entry)
        if session is not None:
            session.commit()
        self._api.log.info(
            "siemens blob edit applied",
            plugin="project",
            device=device.individual_address,
            ref=ref_id,
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
        try:
            accepted = self._api.project.set_device_individual_address(
                device.node_id, old_address, new_address
            )
        except MediumError as error:
            self._medium_error(error)
            return
        if accepted:
            device.individual_address = new_address

    def _medium_error(self, error: MediumError) -> None:
        """Tell the user why the media of the topology do not allow a change."""
        self._api.log.warning(
            "rejected by the medium", plugin="project", error=str(error)
        )
        if self._api.notify is not None:
            self._api.notify(medium_error_text(error))

    def _report(self, reason: str | None) -> None:
        """Tell the user why an edit was refused (``reason``; None: it was not)."""
        if reason and self._api.notify is not None:
            self._api.notify(reason)

    def _paste_dialog(self, dialog_id: str) -> PasteSpecialDialog:
        """The Paste Special dialog of the tree panel ``dialog_id``."""
        project = self._api.project
        return PasteSpecialDialog(
            dialog_id,
            group_style=lambda: project.group_address_style,
            group_ranges=project.get_group_range_tree,
            names=project.paste_names,
            on_paste=self._paste,
            notify=self._report,
        )

    def _paste(
        self,
        kind: PasteKind,
        ids: list[int],
        target: PasteTarget,
        options: PasteOptions,
    ) -> str | None:
        """Paste the copies the Paste Special dialog asked for; the reason when it cannot."""
        try:
            self._api.project.paste(kind, ids, target, options)
        except PasteError as exc:
            return paste_error_text(exc)
        return None

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

    def _handle_com_object_datapoint_type(
        self, device: "Device", co_id: str, value: str | None
    ) -> None:
        self._api.project.set_com_object_datapoint_type(device, co_id, value)

    def _handle_com_object_priority(
        self, device: "Device", co_id: str, value: str | None
    ) -> None:
        self._api.project.set_com_object_priority(device, co_id, value)

    def _handle_com_object_description(
        self, device: "Device", co_id: str, value: str | None
    ) -> None:
        self._api.project.set_com_object_description(device, co_id, value)

    def _program_device(self, device: "Device", request: "ProgramRequest") -> None:
        """Program button: enqueue the device. Runs immediately if the bus is free, otherwise it is
        appended to the programming queue (dedupe per device) and drained one at a time."""
        from xknxeditor.download.scope import DownloadScope

        scope = request.scope
        serial = (
            self._project_serial(device.node_id)
            if request.use_serial_number
            or (scope is DownloadScope.FULL and self._api.connection.use_serial_number)
            else None
        )
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
                without_plugin=request.without_plugin,
                current_address=request.current_address,
                serial_number=serial,
            )
        )

    def _queue_program(
        self,
        node_id: int,
        scope: "DownloadScope",
        *,
        origin: str,
        without_plugin: bool = False,
        current_address: str | None = None,
        serial_number: bytes | None = None,
    ) -> QueueItem | None:
        """Queue a programming started outside the editor's UI (``origin``, e.g. an MCP client),
        shown in Operations like one started here. Returns the item, whose ``ended`` future
        resolves once it has ended, or None for a device not in the project."""
        from concurrent.futures import Future

        device = self._api.project.find_device_by_node_id(node_id)
        if device is None:
            return None
        item = QueueItem(
            node_id=device.node_id,
            address=device.individual_address or "",
            name=device.name,
            scope=scope,
            without_plugin=without_plugin,
            current_address=current_address,
            serial_number=serial_number,
            origin=origin,
            ended=Future(),
        )
        self._program_queue.enqueue(item)
        return item

    def _ask_certificate(self, device: "Device", item: QueueItem) -> None:
        """Ask for the device's FDSK; once added, the same download is queued again. "Plain"
        deactivates its secure commissioning and downloads it in the clear."""

        def plain() -> None:
            self._deactivate_secure_commissioning(device, then=again)

        def again() -> None:
            self._program_queue.enqueue(
                QueueItem(
                    node_id=item.node_id,
                    address=item.address,
                    name=item.name,
                    scope=item.scope,
                    without_plugin=item.without_plugin,
                    current_address=item.current_address,
                    serial_number=item.serial_number,
                )
            )

        if self._api.main_thread is not None:
            self._api.main_thread.submit(
                functools.partial(
                    self._open_certificate_dialog, device, then=again, plain=plain
                )
            )
        else:
            self._open_certificate_dialog(device, then=again, plain=plain)

    def _open_certificate_dialog(
        self,
        device: "Device",
        then: Callable[[], None] | None = None,
        plain: Callable[[], None] | None = None,
        on_dont_ask: Callable[[bool], None] | None = None,
    ) -> None:
        """The certificate dialog for ``device``, with the certificate the project holds for it."""
        self._certificate_device = device
        label = f"{device.individual_address or '-'} {device.display_name}".strip()
        serial, key_in_use = self._api.project.device_certificate_state(device)
        self._certificate_dialog.open(
            label,
            then,
            plain,
            on_dont_ask,
            state=CertificateState(serial, key_in_use),
        )

    def _ask_certificate_on_add(self, node_id: int) -> None:
        """A device was added with secure commissioning: ask for its certificate right away,
        unless the user chose not to be asked ("Later" postpones it to the download)."""
        from editor_gui.settings import load_settings

        if not load_settings(_SECURITY_SETTINGS).get("ask_certificate_on_add", True):
            return
        device = self._api.project.find_device_by_node_id(node_id)
        if device is None:
            return
        self._open_certificate_dialog(device, on_dont_ask=self._dont_ask_certificate)

    @staticmethod
    def _dont_ask_certificate(dont_ask: bool) -> None:
        from editor_gui.settings import load_settings, save_settings

        data = load_settings(_SECURITY_SETTINGS)
        data["ask_certificate_on_add"] = not dont_ask
        save_settings(_SECURITY_SETTINGS, data)

    def _add_project_certificate(self) -> None:
        """ "Add Device Certificate…" from the project's certificate list (no device chosen)."""
        self._certificate_device = None
        self._certificate_dialog.open(S.PROJECT_CERTIFICATES_TITLE)

    def _ask_certificate_for(self, device: "Device") -> None:
        """ "Add Device Certificate" / "Device Certificate" from the device settings."""
        self._open_certificate_dialog(device)

    def _change_ip_secure(
        self, device: "Device", field: str, value: object
    ) -> str | None:
        """An edit in the device's KNX IP Secure settings; the message to show when rejected."""
        from xknxeditor.proj import ProjectPasswordRequired

        project = self._api.project
        try:
            if field == "secure_tunnelling":
                project.set_ip_secure_settings(device, secure_tunnelling=bool(value))
            elif field == "interface_password" and isinstance(value, tuple):
                ref, password = cast("tuple[str, str]", value)
                project.set_ip_secure_settings(
                    device, interface_password=(ref, password)
                )
            elif field == "authentication_code" and isinstance(value, str):
                project.set_ip_secure_settings(device, authentication_code=value)
            elif field == "commissioning_password" and isinstance(value, str):
                project.set_ip_secure_settings(device, commissioning_password=value)
        except ProjectPasswordRequired:
            return S.SECURE_DOWNLOAD_PASSWORD_REQUIRED
        except ValueError:
            return S.IP_SECURE_PASSWORD_INVALID
        return None

    def _change_additional_address(
        self, device: "Device", index: int, field: str, value: str
    ) -> str | None:
        """An edit of the device's additional address ``index`` (``field`` ``"address"``,
        ``"name"`` or ``"description"``); the message to show when rejected."""
        from xknxeditor.proj import AdditionalAddressError

        project = self._api.project
        try:
            if field == "address":
                project.set_additional_address(device.node_id, index, value)
            elif field == "name":
                project.set_additional_address_name(device.node_id, index, value)
            elif field == "description":
                project.set_additional_address_description(device.node_id, index, value)
        except AdditionalAddressError as exc:
            return additional_address_error_text(exc)
        return None

    def _assign_additional_addresses(self, device: "Device") -> str | None:
        """Assign the device's missing additional addresses; the message when the line has too
        few free."""
        from xknxeditor.proj import AdditionalAddressError

        try:
            self._api.project.assign_additional_addresses(device)
        except AdditionalAddressError as exc:
            return additional_address_error_text(exc)
        return None

    def _change_secure_commissioning(self, device: "Device", activated: bool) -> None:
        """The device setting "Secure Commissioning"; activating needs a project password."""
        from xknxeditor.proj import ProjectPasswordRequired

        if not activated:
            self._deactivate_secure_commissioning(device)
            return
        try:
            self._api.project.set_secure_commissioning(device, True)
        except ProjectPasswordRequired:
            self._api.log.error(S.SECURE_DOWNLOAD_PASSWORD_REQUIRED, plugin="project")

    def _deactivate_secure_commissioning(
        self, device: "Device", then: Callable[[], None] | None = None
    ) -> None:
        """Deactivate the device's secure commissioning, after confirming what it changes when its
        group addresses are affected; ``then`` runs once it is done."""
        impact = self._api.project.secure_commissioning_impact(device)

        def deactivate() -> None:
            self._api.project.set_secure_commissioning(device, False)
            if then is not None:
                then()

        if impact is None or not (impact.unsecured or impact.removed_from):
            deactivate()
            return

        def names(ids: list[int]) -> list[str]:
            labels: list[str] = []
            for ga_id in ids:
                ga = self._api.project.get_group_address(ga_id)
                if ga is not None:
                    labels.append(f"{ga.address} {ga.name}".strip())
            return labels

        label = f"{device.individual_address or '-'} {device.display_name}".strip()
        self._secure_commissioning_dialog.open(
            label, names(impact.unsecured), names(impact.removed_from), deactivate
        )

    def _add_certificate(self, code: str) -> str | None:
        """Store the entered FDSK for the device it was asked for; an error message otherwise.
        A device without a serial number takes the one of the certificate."""
        from xknxeditor.proj import ProjectPasswordRequired
        from xknxeditor.proj.core.fdsk import InvalidFdskError, parse_fdsk

        device = self._certificate_device
        try:
            fdsk = parse_fdsk(code)
        except InvalidFdskError:
            return S.DEVICE_CERTIFICATE_INVALID
        if device is None:
            # Added from the project's list: the certificate names its device by serial number.
            try:
                self._api.project.add_device_certificate(code)
            except ProjectPasswordRequired:
                return S.SECURE_DOWNLOAD_PASSWORD_REQUIRED
            return None
        serial = self._project_serial(device.node_id)
        if serial is not None and serial != fdsk.serial_number:
            return S.DEVICE_CERTIFICATE_OTHER_DEVICE.format(
                serial=serial_text(fdsk.serial_number) or ""
            )
        try:
            self._api.project.add_device_certificate(code)
        except ProjectPasswordRequired:
            return S.SECURE_DOWNLOAD_PASSWORD_REQUIRED
        if serial is None:
            self._set_serial_number(device, fdsk.serial_number)
        self._certificate_device = None
        return None

    def _project_serial(self, node_id: int) -> bytes | None:
        info = self._api.project.get_device_info(node_id)
        return serial_from_project(info.serial_number) if info is not None else None

    def _set_serial_number(self, device: "Device", serial: bytes | None) -> None:
        self._api.project.set_device_commissioning(
            device.node_id, serial_number=serial_to_project(serial) if serial else ""
        )

    def _serial_owner(self, device: "Device", serial: bytes) -> str | None:
        """Another project device that already has ``serial``, as ``address name``."""
        for other in self._api.project.devices:
            if (
                other.node_id != device.node_id
                and self._project_serial(other.node_id) == serial
            ):
                return f"{other.individual_address or '-'} {other.name}".strip()
        return None

    def _request_device_info(self, node_id: int, group_communication: bool) -> None:
        """Queue a device info readout; its result shows in the Operations panel."""
        device = self._api.project.find_device_by_node_id(node_id)
        if device is None:
            return
        self._program_queue.enqueue(
            QueueItem(
                node_id=device.node_id,
                address=device.individual_address or "",
                name=device.name,
                scope=None,
                device_info=group_communication,
            )
        )

    def _start_device_info(self, item: QueueItem) -> "Future[Any] | None":
        device = self._api.project.find_device_by_node_id(item.node_id)
        if device is None:
            return None
        future = self._api.connection.device_info(
            device,
            group_communication=bool(item.device_info),
            resolve_application=functools.partial(self._resolve_application, device),
        )
        if future is None and self._api.connection.xknx is None:
            item.message = S.SCRIPT_NOT_CONNECTED
        return future

    async def _confirm_full_download(self, item: QueueItem, reason: str) -> bool:
        """Ask in the Operations panel whether to overwrite the device's application."""
        self._api.log.info(
            "full download needs confirmation", plugin="project", reason=reason
        )
        from concurrent.futures import Future

        answer: Future[bool] = Future()
        item.full_download_question = answer
        try:
            return await asyncio.wrap_future(answer)
        finally:
            item.full_download_question = None

    def _program_error_text(self, error: BaseException) -> str | None:
        """Translated text of the download errors the editor explains itself."""
        from editor_gui.plugins.connection.service import DeviceCertificateRequired
        from xknxeditor.download.partial import (
            DifferentProgramLoaded,
            IncompatibleMaskVersion,
        )

        if isinstance(error, DeviceCertificateRequired):
            return S.DEVICE_CERTIFICATE_REQUIRED
        if isinstance(error, DifferentProgramLoaded):
            return S.DIFFERENT_PROGRAM_LOADED
        if isinstance(error, IncompatibleMaskVersion):
            return S.INCOMPATIBLE_MASK_VERSION.format(
                found=error.found, expected=error.expected
            )
        return None

    def _describe_program_error(
        self, item: QueueItem, error: BaseException
    ) -> str | None:
        """For a failed device check, the project devices the reported value belongs to. A
        download that found the device's certificate missing only once it had connected for it
        asks for the certificate, as one that found it before."""
        from editor_gui.plugins.connection.service import DeviceCertificateRequired

        if isinstance(error, DeviceCertificateRequired):
            device = self._api.project.find_device_by_node_id(item.node_id)
            if device is not None and item.origin is None:
                self._ask_certificate(device, item)
            return None
        mismatch = compare_mismatch(error)
        if mismatch is None:
            return None
        devices = [d for d in self._api.project.devices if d.node_id != item.node_id]
        matches = matching_devices(mismatch, devices)
        if not matches:
            return None
        names = ", ".join(
            f"{d.display_name} ({d.individual_address or '-'})" for d in matches
        )
        return S.PROGRAM_COMPARE_MATCHES.format(devices=names)

    def _on_operation_finished(self, item: QueueItem, result: Any) -> None:
        from editor_gui.device_info import DeviceInfo

        if item.device_info is None or not isinstance(result, DeviceInfo):
            return
        from xknxeditor.proj.core.addressing import format_ga

        self._api.log.info(
            "device info",
            plugin="project",
            address=result.individual_address,
            mask=f"{result.mask_version:#06x}"
            if result.mask_version is not None
            else None,
            application_version=result.application.application_version
            if result.application is not None
            else None,
            serial=result.serial_number.hex().upper() if result.serial_number else None,
            group_objects=len(result.group_objects),
        )
        device = self._api.project.find_device_by_node_id(item.node_id)
        if device is not None:
            self._store_read_serial(device, result)
        style = self._api.project.group_address_style
        names = self._api.project.group_address_names()

        def group_address(value: int) -> str:
            text = format_ga(value, style)
            name = names.get(value)
            return f"{text}  {name}" if name else text

        item.details = device_info_sections(
            result,
            manufacturers=self._manufacturer_names(),
            application_name=self._application_name(device, result),
            group_address=group_address,
            project_version=_project_version(device) if device is not None else None,
            expected=self._api.project.expected_group_addresses(device)
            if device is not None
            else None,
        )

    def _store_read_serial(self, device: "Device", result: "DeviceInfo") -> None:
        """Save the serial number read from the device, when it runs the project device's
        application (so another product at that address is not taken for it)."""
        serial = result.serial_number
        if (
            serial is None
            or len(serial) != 6
            or result.application is None
            or device.app is None
            or not same_application(device.app.id, result.application)
            or self._project_serial(device.node_id) == serial
        ):
            return
        self._api.project.set_device_commissioning(
            device.node_id, serial_number=serial_to_project(serial)
        )

    def _resolve_application(
        self, device: "Device", app_id: "AppId"
    ) -> "Application | None":
        """The application a device reports: the project's when it is the same, else the catalog's."""
        if device.app is not None and same_application(device.app.id, app_id):
            return device.app
        for product in self._api.catalog.find_products_for_application(
            manufacturer_id=app_id.manufacturer_id,
            application_number=app_id.application_number,
            application_version=app_id.application_version,
        ):
            if product.application_id is not None:
                application = self._api.catalog.get_application(product.application_id)
                if application is not None:
                    return application
        return None

    def _manufacturer_names(self) -> dict[str, str]:
        master = self._api.connection.master
        return dict(master.manufacturers) if master is not None else {}

    def _application_name(self, device: "Device | None", info: Any) -> str | None:
        """The name of the application the device reports, from the project or the catalog."""
        from xknxeditor.recover import match_application

        app_id = info.application
        if app_id is None:
            return None
        if (
            device is not None
            and device.app is not None
            and same_application(device.app.id, app_id)
        ):
            return device.app.name
        products = match_application(self._api.catalog, app_id)
        return products[0].name if products else None

    def _start_program(self, item: QueueItem) -> "Future[Any] | None":
        """Start one queued programming via the normal single-device path (slot, progress, notice,
        commissioning all handled by ``program_device`` + the commissioning callback). Returns the
        Future, or None if it could not start (not connected / device gone)."""
        if item.script is not None:
            return self._online_runner.start(item)
        if item.device_info is not None:
            return self._start_device_info(item)
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
        from editor_gui.plugins.connection.service import DeviceCertificateRequired
        from xknxeditor.proj import ProjectPasswordRequired

        try:
            tool_key = self._api.project.download_tool_key(device)
        except ProjectPasswordRequired:
            item.message = S.SECURE_DOWNLOAD_PASSWORD_REQUIRED
            return None
        ip_secure = self._api.project.ip_secure_config(
            device, self._api.connection.keyring
        )
        try:
            future = self._api.connection.program_device(
                device,
                item.scope,
                self._group_communication_for(device),
                without_plugin=item.without_plugin,
                current_address=item.current_address,
                waiting=functools.partial(setattr, item, "waiting_for_button", True),
                serial_number=item.serial_number,
                confirm_full_download=functools.partial(
                    self._confirm_full_download, item
                ),
                ip_config=self._api.project.ip_download_config(device),
                tool_key=tool_key,
                ip_secure=ip_secure,
                disable_security=self._api.project.download_disables_security(device),
                routing_blocking=self._api.project.download_routing_blocking(device),
            )
        except DeviceCertificateRequired:
            item.message = S.DEVICE_CERTIFICATE_REQUIRED
            # The caller of an operation started elsewhere gets the message instead of a dialog.
            if item.origin is None:
                self._ask_certificate(device, item)
            return None
        if future is None:
            if device.download_plugin_required and not item.without_plugin:
                item.message = S.PLUGIN_DOWNLOAD_REQUIRED
            elif self._api.connection.xknx is None:
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
            result = f.result()
            self._record_commissioning(
                node_id,
                scope_value,
                result if isinstance(result, bytes) else None,
                getattr(result, "mac_address", None),
                result=result,
                ip_secure=ip_secure,
            )

        future.add_done_callback(_on_done)
        return future

    # --- programming queue (UI-thread facade for main.py / the queue panel) ---
    def tick_program_queue(self) -> None:
        self._program_queue.tick()

    def take_operations_focus(self) -> bool:
        """Whether an operation was queued, or a bus operation without a queue entry started (a
        device test, a scan …), since the last call; selects the Active tab if so."""
        queued = self._program_queue.take_focus_request()
        operation = self.unqueued_bus_operation()
        started = operation is not None and operation != self._shown_bus_operation
        self._shown_bus_operation = operation
        if not (queued or started):
            return False
        self._program_queue_panel.select_active()
        return True

    @property
    def program_queue(self) -> ProgramQueue:
        """The operations the Operations panel lists: running, waiting and ended ones."""
        return self._program_queue

    def unqueued_bus_operation(self) -> tuple[str, str] | None:
        """The running bus operation the queue has no entry for (a device test, a scan …), as
        kind and address; the Operations panel shows it above the queue."""
        return _unqueued(self._api.connection.busy_operation)

    def _record_commissioning(
        self,
        node_id: int,
        scope_value: str,
        serial: bytes | None = None,
        mac_address: bytes | None = None,
        *,
        result: object = None,
        ip_secure: "IpSecureConfig | None" = None,
    ) -> None:
        def _apply() -> None:
            self._api.project.record_download(node_id, scope_value, serial, mac_address)
            device = self._api.project.find_device_by_node_id(node_id)
            if result is not None and device is not None:
                self._api.project.record_secure_download(device, result)
                # IP Secure settings are only written over a secured connection.
                if ip_secure is not None and getattr(result, "secured", False):
                    self._api.project.record_ip_secure_download(device, ip_secure)
            if scope_value != "unload":
                self._find_ip_after_download(node_id)

        if self._api.main_thread is not None:
            self._api.main_thread.submit(_apply)
        else:
            _apply()

    def _find_ip_after_download(self, node_id: int) -> None:
        """Look for a downloaded IP device on the network when its address is assigned
        automatically and not known yet, and keep the address it answers from."""
        serial = self._api.project.serial_to_find_ip(node_id)
        future = self._api.connection.find_device_ip(serial) if serial else None
        if future is None:
            return

        def found(done: "Future[str | None]") -> None:
            address = None if done.exception() is not None else done.result()
            if address is None:
                return

            def keep() -> None:
                self._api.project.record_found_ip(node_id, address)

            if self._api.main_thread is not None:
                self._api.main_thread.submit(keep)
            else:
                keep()

        future.add_done_callback(found)

    def _eval_device(self, device: "Device", scope: "DownloadScope") -> None:
        label = device.name or device.individual_address or "device"
        # The test reads the live device, so it needs a bus connection; without one (and none
        # to open for it) there is nothing to compare against.
        if not self._api.connection.can_reach_bus():
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
            device,
            scope,
            self._group_communication_for(device),
            self._api.project.ip_download_config(device),
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
        space = self.selected_space()
        if space is not None:
            self._space_details.render(space)
            return
        element = self.selected_element()
        if element is not None:
            self._element_details.render(element)
            return
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

    @property
    def show_context_help(self) -> bool:
        return self._configure_panel.show_help

    @show_context_help.setter
    def show_context_help(self, shown: bool) -> None:
        self._configure_panel.show_help = shown

    def reset_layout(self) -> None:
        """Sizes the user dragged in the panels back to their defaults (Restore Default Layout)."""
        self._configure_panel.reset_help_height()

    def import_group_addresses(self) -> None:
        """Ask for a group address file and import it into the open project."""
        self._group_addresses_panel.start_import()

    def render_overlays(self) -> None:
        self._comment_panel.tick()
        self._group_addresses_panel.render_import()
        self._read_device_dialog.render()
        self._certificate_dialog.render()
        self._secure_commissioning_dialog.render()
        self._object_group_value.render()
        self._button_runner.render()
        self._memory_preview.render()
        self._preflight_result.render()

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

import threading
import webbrowser
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from imgui_bundle import imgui, imspinner
from xknx.exceptions import CouldNotParseAddress
from xknx.telegram import IndividualAddress

from editor_gui import doc_links
from editor_gui.device import ComObject, Device, device_label
from editor_gui.device_info import (
    application_program_label,
    application_version_text,
    parse_serial_text,
    serial_from_project,
    serial_text,
)
from editor_gui.device_plugins import device_plugins, parameters_in_plugin
from editor_gui.hardware_text import bus_current_text, rail_text
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import label_column_width, px, px_vec2
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.fonts import heading, italic
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from xknxeditor.download.scope import DownloadScope
from xknxeditor.prod.app_id import parse_app_id
from xknxeditor.prod.parser_v2.ui import UiNode

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import IpSecureSettings
    from xknxeditor.catalog import ProductSummary, ProgramSummary
    from xknxeditor.proj.core.addressing import GroupAddressStyle
    from xknxeditor.proj.core.service import DeviceInfo
from editor_gui.programming_dali import is_dali_dca_app
from editor_gui.settings import load_settings, save_settings
from editor_gui.widgets import (
    ButtonActions,
    GroupObjectsTable,
    channel_apply_targets,
    count_parameters,
    differing_param_refs,
    render_ui_tree,
    take_selected_help,
)
from editor_gui.widgets.backgrounds import child_background, side_background
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import begin_combo, combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.group_objects_widgets import (
    GroupAddressCatalog,
    GroupLinkResolver,
)
from editor_gui.widgets.icons import Icon, Picture
from editor_gui.widgets.markdown import render_markdown
from editor_gui.widgets.module_table import (
    ModuleTable,
    build_module_tables,
    render_module_tables,
)

_log = structlog.get_logger("configure")
_PLUGIN_NOTICE_COLOR = imgui.ImVec4(0.95, 0.75, 0.35, 1.0)
_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)


def _move_cursor_up(distance: float) -> None:
    pos = imgui.get_cursor_screen_pos()
    imgui.set_cursor_screen_pos(imgui.ImVec2(pos.x, pos.y - distance))


# Height of the line above the help that resizes it when dragged, and the thickness of the line.
_HELP_SPLITTER = 7.0
_HELP_LINE = 3.0
# The help's height (at 100 % scaling) until the user drags it, and the app setting that keeps
# the dragged one.
_HELP_HEIGHT = 160.0
_HELP_HEIGHT_SETTING = "help_height"
# Room at 100 % scaling between the help's edges and its text.
_HELP_PADDING = (18.0, 13.0)
# Product ids of OpenKNX devices (manufacturer KNX Association, 0x00FA).
_OPENKNX_PRODUCT_PREFIX = "M-00FA_"


def overwrite_current_address(
    text: str, address: str | None
) -> tuple[str | None, str | None]:
    """The normalised current address entered for an overwrite, or why it cannot be used."""
    text = text.strip()
    try:
        current = IndividualAddress(text)
    except CouldNotParseAddress:
        return None, S.OVERWRITE_ADDRESS_INVALID
    if text.count(".") != 2:
        return None, S.OVERWRITE_ADDRESS_INVALID
    if address and str(current) == address:
        return None, S.OVERWRITE_ADDRESS_SAME.format(address=address)
    return str(current), None


_SCOPE_ORDER = [
    DownloadScope.FULL,
    DownloadScope.PARAMETERS,
    DownloadScope.GROUP_COMMUNICATION,
    DownloadScope.APPLICATION,
    DownloadScope.INDIVIDUAL_ADDRESS,
    DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS,
    DownloadScope.UNLOAD,
    DownloadScope.UNLOAD_ALL,
]


def _scope_labels() -> list[str]:
    """Labels of ``_SCOPE_ORDER``."""
    return [
        S.SCOPE_FULL,
        S.SCOPE_PARAMETERS,
        S.SCOPE_GROUP_COMMUNICATION,
        S.SCOPE_APPLICATION,
        S.SCOPE_INDIVIDUAL_ADDRESS,
        S.SCOPE_OVERWRITE_INDIVIDUAL_ADDRESS,
        S.SCOPE_UNLOAD,
        S.SCOPE_UNLOAD_ALL,
    ]


def _same_line_if_fits(label: str) -> None:
    """Put the next button beside the previous item if it fits, else below it."""
    style = imgui.get_style()
    width = imgui.calc_text_size(label).x + style.frame_padding.x * 2
    right = imgui.get_item_rect_max().x + style.item_spacing.x + width
    limit = imgui.get_window_pos().x + imgui.get_window_width() - style.window_padding.x
    if right <= limit:
        imgui.same_line()


def _no_application_tooltip(device: Device) -> None:
    """Why the last (disabled) programming button does nothing for this device."""
    if not device.has_application and imgui.is_item_hovered(
        imgui.HoveredFlags_.allow_when_disabled
    ):
        imgui.set_tooltip(S.CONFIGURE_NO_APPLICATION)


def _same_line_if_fits_checkbox(label: str) -> None:
    """Put the next checkbox beside the previous item if it fits, else below it."""
    style = imgui.get_style()
    width = (
        imgui.get_text_line_height()
        + style.frame_padding.y / 2
        + style.item_inner_spacing.x
        + imgui.calc_text_size(label).x
    )
    right = imgui.get_item_rect_max().x + style.item_spacing.x * 2 + width
    limit = imgui.get_window_pos().x + imgui.get_window_width() - style.window_padding.x
    if right <= limit:
        imgui.same_line(0.0, style.item_spacing.x * 2)


def _edit_being_stored() -> bool:
    """Whether a field that was edited lost the focus and reports it (to store the edit) in this
    frame or the next."""
    data = imgui.get_current_context().deactivated_item_data
    return (
        data.id_ != 0
        and data.has_been_edited_before
        and data.elapse_frame >= imgui.get_frame_count()
    )


def _reset_presets() -> list[tuple[str, int]]:
    """(label, KNX A_Restart_Master_Reset erase code). Codes per the KNX Application Layer spec
    (verified: 2=FactoryReset, 7=FactoryResetWithoutIA, 3=ResetIA, 4=ResetAP, 5=ResetParam,
    6=ResetLinks, 1=ConfirmedRestart)."""
    return [
        (S.RESET_FACTORY, 2),
        (S.RESET_FACTORY_KEEP_IA, 7),
        (S.RESET_IA, 3),
        (S.RESET_AP, 4),
        (S.RESET_PARAM, 5),
        (S.RESET_LINKS, 6),
        (S.RESET_CONFIRMED_RESTART, 1),
    ]


# The dialog of a Program press keeps its identity while its title follows the case.
_PROGRAM_DIALOG_ID = "###program_device"


@dataclass(frozen=True, slots=True)
class ProgramRequest:
    """A Program press: the scope and the choices made in its dialog, if one was needed."""

    scope: DownloadScope
    without_plugin: bool = False
    current_address: str | None = None
    use_serial_number: bool = False


def direct_program_request(
    scope: DownloadScope,
    *,
    plugin_required: bool,
    use_serial_number: bool,
    serial_owner: str | None,
) -> ProgramRequest | None:
    """The request a Program press starts right away, or ``None`` when its dialog has to ask
    first: an unload, which leaves the device without a working application, the current
    address to overwrite, the missing download plugin, or a serial number another device of
    the project has already (``serial_owner``). ``use_serial_number`` is the connection's
    setting, for a device whose serial number is known."""
    if scope in (
        DownloadScope.UNLOAD,
        DownloadScope.UNLOAD_ALL,
        DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS,
    ):
        return None
    if plugin_required and not scope.individual_address_only:
        return None
    if use_serial_number and serial_owner is not None:
        return None
    return ProgramRequest(scope=scope, use_serial_number=use_serial_number)


def _render_loading_application() -> None:
    """Shown while the device's application loads in the background."""
    imgui.spacing()
    imspinner.spinner_ang(
        "##application-loading",
        px(6),
        px(2),
        color=imgui.ImColor(imgui.get_style_color_vec4(imgui.Col_.tab_selected)),
    )
    imgui.same_line()
    imgui.text_disabled(S.CONFIGURE_LOADING_APPLICATION)


class ConfigurePanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_selected_device: Callable[[], Device | None],
        set_selected_device: Callable[[Device], None],
        on_param_change: Callable[[Device, str, str], None],
        on_individual_address_change: Callable[[Device, str], None],
        on_name_change: Callable[[Device, str], None],
        on_description_change: Callable[[Device, str], None],
        set_flag: Callable[[Device, str, str, bool], None],
        get_links_for_com_object: GroupLinkResolver,
        get_all_group_addresses: GroupAddressCatalog,
        on_link_com_object: Callable[[int, int], None],
        on_unlink_com_object: Callable[[int], None],
        on_auto_create_gas: Callable[[Device, list[ComObject], str, str], None],
        get_device_info: "Callable[[int], DeviceInfo | None]",
        on_program_device: Callable[[Device, ProgramRequest], None] | None = None,
        on_eval_device: Callable[[Device, DownloadScope], None] | None = None,
        open_memory_preview: Callable[[Device], None] | None = None,
        on_param_change_all: Callable[[Device, str, str], None] | None = None,
        notify: Callable[[str], None] | None = None,
        set_dpt: Callable[[Device, str, str | None], None] | None = None,
        set_priority: Callable[[Device, str, str | None], None] | None = None,
        set_description: Callable[[Device, str, str | None], None] | None = None,
        on_update_application: Callable[[Device], None] | None = None,
        get_update_version: Callable[[Device], int | None] | None = None,
        get_change_targets: "Callable[[Device], list[ProgramSummary]] | None" = None,
        on_change_application: "Callable[[Device, ProgramSummary], None] | None" = None,
        on_read_device_info: Callable[[Device], None] | None = None,
        on_restart_device: Callable[[Device], None] | None = None,
        on_master_reset_device: Callable[[Device, int], None] | None = None,
        suggest_ga: Callable[[Device, ComObject], tuple[str, str]] | None = None,
        get_name_template: Callable[[], str] | None = None,
        set_name_template: Callable[[str], None] | None = None,
        on_create_and_link: Callable[[Device, ComObject, str, str], None] | None = None,
        group_style: "Callable[[], GroupAddressStyle] | None" = None,
        next_free_sub: Callable[[int, int], int] | None = None,
        get_ga_range_tree: Callable[[], list[Any]] | None = None,
        get_selected_node_ids: Callable[[], set[int]] | None = None,
        on_param_change_selected: Callable[[list[int], str, str], None] | None = None,
        render_dali: Callable[[Device], None] | None = None,
        on_navigate_ga: Callable[[int], None] | None = None,
        on_paste_links: Callable[[int, list[tuple[int, bool]], bool], None]
        | None = None,
        ga_menu: Callable[[int, ComObject], None] | None = None,
        buttons: ButtonActions | None = None,
        get_help: Callable[[Device, str], str | None] | None = None,
        get_icon: Callable[[Device, str], Icon | None] | None = None,
        get_picture: Callable[[Device, str], Picture | None] | None = None,
        on_serial_number_change: Callable[[Device, bytes | None], None] | None = None,
        get_secure_commissioning: Callable[[Device], bool | None] | None = None,
        on_secure_commissioning_change: Callable[[Device, bool], None] | None = None,
        on_add_certificate: Callable[[Device], None] | None = None,
        has_certificate: Callable[[Device], bool] | None = None,
        get_ip_secure: "Callable[[Device], IpSecureSettings | None] | None" = None,
        get_routing_blocking: Callable[[Device], str | None] | None = None,
        on_routing_blocking: Callable[[Device, str | None], None] | None = None,
        on_ip_secure_change: Callable[[Device, str, object], str | None] | None = None,
        get_additional_address_count: Callable[[Device], int] | None = None,
        on_additional_address_change: Callable[[Device, int, str, str], str | None]
        | None = None,
        on_assign_additional_addresses: Callable[[Device], str | None] | None = None,
        use_serial_number_default: Callable[[], bool] | None = None,
        find_serial_owner: Callable[[Device, bytes], str | None] | None = None,
        find_product: "Callable[[str], ProductSummary | None] | None" = None,
        application_ready: Callable[[Device], bool] | None = None,
    ) -> None:
        self._get_devices = get_devices
        # Whether a device's application is loaded (it is loaded in the background otherwise).
        self._application_ready = application_ready
        self._get_selected_device = get_selected_device
        self._set_selected_device = set_selected_device
        self._on_param_change = on_param_change
        self._buttons = buttons
        self._get_help = get_help
        self._get_icon = get_icon
        self._get_picture = get_picture
        self._help_pane_h = 0.0  # dragged height of the help pane
        self._help: tuple[int, str] | None = None
        self.show_help = True
        # "Multi fill": when on, a parameter edit is applied to every device that runs
        # the same application program, not just the selected one.
        self._on_param_change_all = on_param_change_all
        self._apply_all = False
        # "Apply to all channels": mirror a parameter edit to the same parameter in every
        # other repeated channel of a device (PM 1, PM 2, …), for apps that expand channels.
        self._apply_all_channels = False
        # Multi-device edit: when >1 same-application device is selected in the tree, edits
        # apply to all of them and diverging parameters show "<differs>". The differing-ref set is
        # cached (recomputed only when the selection changes / after an edit) — computing it calls
        # get_ui() on every selected device, which is far too costly to redo every frame.
        self._get_selected_node_ids = get_selected_node_ids
        self._on_param_change_selected = on_param_change_selected
        self._diff_refs: frozenset[str] = frozenset()
        self._diff_selection: frozenset[tuple[int, int]] = frozenset()
        self._multi_targets: list[int] = []
        # The device shown last, and whether it is still shown although another one is
        # selected (see _current_device).
        self._shown_device: Device | None = None
        self._holding = False
        self._on_individual_address_change = on_individual_address_change
        self._on_name_change = on_name_change
        self._on_description_change = on_description_change
        self._on_program_device = on_program_device
        self._on_eval_device = on_eval_device
        self._open_memory_preview = open_memory_preview
        self._get_links = get_links_for_com_object
        self._get_all_gas = get_all_group_addresses
        self._get_device_info = get_device_info
        self._find_product = find_product
        self._notify = notify
        self._on_update_application = on_update_application
        self._get_update_version = get_update_version
        self._get_change_targets = get_change_targets
        self._on_change_application = on_change_application
        self._on_read_device_info = on_read_device_info
        self._on_restart_device = on_restart_device
        self._on_master_reset_device = on_master_reset_device
        self._reset_armed = False  # confirm checkbox in the master-reset popup
        self._reset_erase_idx = 0
        # Parameter count + module ("Channels") tables, derived from the device UI tree every frame
        # otherwise (count_parameters + build_module_tables walk the whole tree with regex). Keyed by
        # id(ui_nodes): device.get_ui() returns a fresh list when a parameter edit rebuilds the UI, so
        # the id changes exactly when the derived data must be recomputed.
        self._ui_derived_key: int | None = None
        self._ui_derived: tuple[int, list[ModuleTable]] | None = None
        self._manual_busy: bool = False  # a manual lookup thread is running
        self._group_objects_table = GroupObjectsTable(
            set_flag,
            on_link_com_object,
            on_unlink_com_object,
            on_auto_create_gas,
            suggest_ga=suggest_ga,
            get_name_template=get_name_template,
            set_name_template=set_name_template,
            on_create_and_link=on_create_and_link,
            group_style=group_style,
            next_free_sub=next_free_sub,
            get_ga_range_tree=get_ga_range_tree,
            on_navigate_ga=on_navigate_ga,
            on_paste_links=on_paste_links,
            on_set_dpt=set_dpt,
            on_set_priority=set_priority,
            on_set_description=set_description,
            ga_menu=ga_menu,
        )
        self._name_buffer: str = ""
        # The name field was pre-filled with the default name when it was activated.
        self._name_prefilled = False
        self._address_buffer: str = ""
        self._description_buffer: str = ""
        self._buffer_device_id: int | None = None
        self._download_scope: DownloadScope = DownloadScope.PARAMETERS
        self._confirm_program_open: bool = False
        # Confirmed to program a device whose hardware needs the manufacturer's plugin.
        self._program_without_plugin: bool = False
        self._overwrite_current_address = ""
        self._program_use_serial = False
        self._serial_buffer = ""
        self._on_serial_number_change = on_serial_number_change
        self._get_secure_commissioning = get_secure_commissioning
        self._on_secure_commissioning_change = on_secure_commissioning_change
        self._on_add_certificate = on_add_certificate
        self._has_certificate = has_certificate
        self._get_ip_secure = get_ip_secure
        self._get_routing_blocking = get_routing_blocking
        self._on_routing_blocking = on_routing_blocking
        self._on_ip_secure_change = on_ip_secure_change
        # The KNX IP Secure section's edit buffers (field key -> text) and its "show" switch.
        self._ip_secure_buffers: dict[str, str] = {}
        self._ip_secure_device: int | None = None
        self._ip_secure_show = False
        # The additional individual addresses: how many the application has, the edits (index,
        # field "address"/"name"/"description", text -> the message when rejected), the
        # assignment of the missing ones, the edit buffers ("field:index" -> text) and the
        # message of the last rejected edit.
        self._get_additional_address_count = get_additional_address_count
        self._on_additional_address_change = on_additional_address_change
        self._on_assign_additional_addresses = on_assign_additional_addresses
        self._additional_buffers: dict[str, str] = {}
        self._additional_device: int | None = None
        self._additional_error = ""
        self._use_serial_number_default = use_serial_number_default
        self._find_serial_owner = find_serial_owner
        self._render_dali = (
            render_dali  # DALI commissioning tab body (MDT DALI devices only)
        )
        self._param_filter: str = ""
        self._module_filter: str = ""  # "Channels" tab (module-instance table) filter
        self._lv_seq: int = 0  # per-frame counter for unique _render_label_value ids
        self._label_w = 0.0  # start of the value column next to field labels

    def _current_device(self) -> Device | None:
        """The selected device, defaulting to the first one.

        A field stores its edit when it loses the focus, which is reported to the field a frame
        later. When that happens by selecting another device, the device shown so far stays
        shown until then, so the edit is stored for it and not dropped or given to the other."""
        devices = self._get_devices()
        if not devices:
            return None
        device = self._get_selected_device()
        if device is None:
            device = devices[0]
            self._set_selected_device(device)
        shown = self._shown_device
        self._holding = False
        if (
            shown is not None
            and shown.node_id != device.node_id
            and _edit_being_stored()
        ):
            # The current object of that device: storing the edit may have rebuilt it.
            current = next((d for d in devices if d.node_id == shown.node_id), None)
            if current is not None:
                self._holding = True
                self._shown_device = current
                return current
        self._shown_device = device
        return device

    def _begin_details(self) -> None:
        self._lv_seq = 0  # reset per-frame id counter for _render_label_value
        self._label_w = label_column_width(
            S.CONFIGURE_NAME,
            S.CONFIGURE_INDIVIDUAL_ADDRESS,
            S.CONFIGURE_MANUFACTURER,
            S.CONFIGURE_APPLICATION,
            S.CONFIGURE_APP_VERSION,
            S.CONFIGURE_SCHEMA_VERSION,
            S.CONFIGURE_ORDER_NUMBER,
            S.CONFIGURE_HARDWARE,
            S.CONFIGURE_PRODUCT,
            S.CONFIGURE_DESCRIPTION,
            S.CONFIGURE_PRODUCT_REF,
            S.CONFIGURE_PROGRAM_REF,
            S.CONFIGURE_RAIL,
            S.CONFIGURE_BUS_CURRENT,
            S.CONFIGURE_IP_ASSIGN,
            S.CONFIGURE_IP_ADDRESS,
            S.CONFIGURE_IP_SUBNET,
            S.CONFIGURE_IP_GATEWAY,
            S.CONFIGURE_IP_MAC,
            S.READOUT_MASK,
            S.READOUT_SERIAL,
            S.READOUT_ORDER,
            S.READOUT_HARDWARE,
        )

    def render_device(self) -> None:
        """Device panel: name, address, description and the device actions."""
        self._begin_details()
        if self._current_device() is None:
            imgui.text_disabled(S.CONFIGURE_NO_DEVICES)
            return
        self._render_device_section()

    def render_manufacturer(self) -> None:
        """Manufacturer panel: product and application details of the selected device."""
        self._begin_details()
        device = self._current_device()
        if device is None:
            imgui.text_disabled(S.CONFIGURE_NO_DEVICES)
            return
        self._render_manufacturer_section(device)

    def _render_device_section(self) -> None:
        """Name, address, description and actions of the selected device."""
        device = self._current_device()
        if device is None:
            return

        if self._buffer_device_id != device.node_id:
            self._name_buffer = device.name
            self._address_buffer = device.individual_address
            self._description_buffer = device.description
            self._serial_buffer = serial_text(self._project_serial(device)) or ""
            self._buffer_device_id = device.node_id

        self._render_header_fields(device)
        if self._ready(device):
            self._render_plugin_notices(device)
        self._render_device_actions(device)
        # Application program (change/update), the manual, and the from-device read-out follow
        # directly below the "Read from Device" action.
        imgui.spacing()
        self._render_application_program(device)
        imgui.spacing()
        self._render_manual_button(self._get_device_info(device.node_id))
        self._render_ip_config(device)
        self._render_additional_addresses(device)
        # Whether the device is a KNX IP Secure device comes from its application: shown once it
        # is loaded, so selecting a device never parses its application on the render thread.
        if self._ready(device):
            self._render_ip_secure(device)

    def _ready(self, device: Device) -> bool:
        return self._application_ready is None or self._application_ready(device)

    def _programming_labels(self) -> list[str]:
        labels: list[str] = []
        if self._on_eval_device is not None:
            labels.append(S.BTN_EVAL_DEVICE)
        if self._on_program_device is not None:
            labels.append(S.BTN_PROGRAM_DEVICE)
        if self._open_memory_preview is not None:
            labels.append(S.BTN_PREVIEW_MEMORY)
        return labels

    def _render_title_row(self, device: Device) -> None:
        """The device label, with the download scope and the programming buttons right-aligned
        beside it, or below it when the panel is too narrow."""
        imgui.align_text_to_frame_padding()
        imgui.text(device_label(device))
        buttons = self._programming_labels()
        if not buttons:
            return
        style = imgui.get_style()
        width = (
            imgui.calc_text_size(S.CONFIGURE_DOWNLOAD_SCOPE).x
            + style.item_spacing.x
            + self._scope_combo_width()
            + sum(
                style.item_spacing.x
                + imgui.calc_text_size(label).x
                + style.frame_padding.x * 2
                for label in buttons
            )
        )
        imgui.same_line()
        free = imgui.get_content_region_avail().x - width
        if free >= style.item_spacing.x * 2:
            imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + free)
        else:
            imgui.new_line()
        self._render_programming(device)

    def _render_programming(self, device: Device) -> None:
        self._render_scope_selector()
        # Workflow order: dry run (preview changes) → program → preview memory image.
        if self._on_eval_device is not None:
            _same_line_if_fits(S.BTN_EVAL_DEVICE)
            enabled = (
                bool(device.individual_address)
                and device.has_application
                and not self._download_scope.individual_address_only
            )
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_EVAL_DEVICE):
                self._on_eval_device(device, self._download_scope)
            imgui.end_disabled()
            _no_application_tooltip(device)

        if self._on_program_device is not None:
            _same_line_if_fits(S.BTN_PROGRAM_DEVICE)
            enabled = bool(device.individual_address) and device.has_application
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_PROGRAM_DEVICE):
                # Programming starts right away; the dialog only opens when it has to ask
                # something first (see direct_program_request).
                request = self._direct_program_request(device)
                if request is None:
                    self._confirm_program_open = True
                elif self._on_program_device is not None:
                    self._on_program_device(device, request)
            imgui.end_disabled()
            _no_application_tooltip(device)
            if self._confirm_program_open:
                title, _confirm = self._program_dialog(device)
                imgui.open_popup(f"{title}{_PROGRAM_DIALOG_ID}")
                self._confirm_program_open = False
                self._program_without_plugin = False
                self._overwrite_current_address = ""
                self._program_use_serial = bool(
                    self._use_serial_number_default
                    and self._use_serial_number_default()
                )

        if self._open_memory_preview is not None:
            _same_line_if_fits(S.BTN_PREVIEW_MEMORY)
            imgui.begin_disabled(not device.has_application)
            if imgui.button(S.BTN_PREVIEW_MEMORY):
                self._open_memory_preview(device)
            imgui.end_disabled()
            _no_application_tooltip(device)

        if self._on_program_device is not None:
            self._render_program_confirm(device)

    def _render_plugin_notices(self, device: Device) -> None:
        """Notes on the manufacturer plugins the product data asks for, which are not available."""
        plugins = device_plugins(device)
        # What the one unavailable plugin does: one sentence for a single point, else one note
        # listing them all.
        plugin_points = [
            (sentence, point)
            for shown, sentence, point in (
                (
                    plugins.download_required,
                    S.PLUGIN_DOWNLOAD_REQUIRED,
                    S.PLUGIN_ITEM_DOWNLOAD_REQUIRED,
                ),
                (
                    plugins.user_interface,
                    S.PLUGIN_USER_INTERFACE,
                    S.PLUGIN_ITEM_USER_INTERFACE,
                ),
                (
                    plugins.download and not plugins.download_required,
                    S.PLUGIN_DOWNLOAD,
                    S.PLUGIN_ITEM_DOWNLOAD,
                ),
                (
                    plugins.data_handler,
                    S.PLUGIN_DATA_HANDLER,
                    S.PLUGIN_ITEM_DATA_HANDLER,
                ),
            )
            if shown
        ]
        notices: list[str] = []
        if len(plugin_points) == 1:
            notices.append(plugin_points[0][0])
        if plugins.external_software:
            notices.append(S.PLUGIN_EXTERNAL_SOFTWARE)
        if plugins.configuration_app is not None:
            # When the app's functionality is ported (device in the DALI whitelist) say so;
            # otherwise state that only the standard parameters are programmable.
            ported = device.app is not None and is_dali_dca_app(device.app.id)
            template = (
                S.PLUGIN_CONFIGURATION_APP_PORTED
                if ported
                else S.PLUGIN_CONFIGURATION_APP
            )
            notices.append(template.format(name=plugins.configuration_app))
        if not notices and len(plugin_points) < 2:
            return
        imgui.push_style_color(imgui.Col_.text, _PLUGIN_NOTICE_COLOR)
        if len(plugin_points) > 1:
            imgui.text_wrapped(S.PLUGIN_NOTICES_HEADING)
            for _sentence, point in plugin_points:
                imgui.bullet()
                imgui.same_line()
                imgui.text_wrapped(point)
        for text in notices:
            imgui.text_wrapped(text)
        imgui.pop_style_color()

    def _render_header_fields(self, device: Device) -> None:
        """Name, physical address and description, side by side when the panel is wide enough, else
        stacked. Uses real table columns (``same_line`` is window-relative and would break inside a
        cell)."""
        fields: list[tuple[str, Callable[[], None]]] = [
            (S.CONFIGURE_NAME, lambda: self._name_input(device)),
            (S.CONFIGURE_INDIVIDUAL_ADDRESS, lambda: self._address_input(device)),
            (S.CONFIGURE_DESCRIPTION, lambda: self._description_input(device)),
        ]
        if self._on_serial_number_change is not None:
            fields.append(
                (S.CONFIGURE_SERIAL_NUMBER, lambda: self._serial_input(device))
            )
        # The secure-commissioning and routing-blocking fields need the parsed application. Only
        # read them once it is loaded: for a snapshot device _ready() starts the background load
        # and returns False, so these fields appear when it is ready instead of parsing the whole
        # application synchronously on the render thread (which would freeze the frame).
        if self._ready(device):
            secure = (
                self._get_secure_commissioning(device)
                if self._get_secure_commissioning is not None
                else None
            )
            if secure is not None:
                fields.append(
                    (
                        S.CONFIGURE_SECURE_COMMISSIONING,
                        lambda: self._secure_commissioning_input(device, secure),
                    )
                )
            # "-": not a coupler with the security extensions commissioned securely.
            blocking = (
                self._get_routing_blocking(device)
                if self._get_routing_blocking is not None
                else "-"
            )
            if blocking != "-":
                fields.append(
                    (
                        S.CONFIGURE_ROUTING_BLOCKING,
                        lambda: self._routing_blocking_input(device, blocking),
                    )
                )
        avail = imgui.get_content_region_avail().x
        group_w = self._label_w + px(120)
        ncols = max(1, min(len(fields), int(avail // group_w) if group_w > 0 else 1))
        # Rebalance so the last row isn't a single lonely field (4 fields in a 3-wide panel become
        # 2x2, not 3+1): keep the minimal row count, then spread fields over the fewest columns.
        rows = -(-len(fields) // ncols)
        ncols = -(-len(fields) // rows)
        # Size each label column to just the labels that land in it (not the global label width,
        # which is sized for the much longer detail labels) so the inputs get the rest of the width.
        pad = px(12)
        col_label_w = [0.0] * ncols
        for idx, (label, _render) in enumerate(fields):
            col = idx % ncols
            col_label_w[col] = max(
                col_label_w[col], imgui.calc_text_size(label).x + pad
            )
        if ncols <= 1:
            for label, render in fields:
                imgui.align_text_to_frame_padding()
                imgui.text_disabled(label)
                imgui.same_line(col_label_w[0])
                render()
            return
        if not imgui.begin_table("##device_header", ncols * 2):
            return
        for i in range(ncols):
            imgui.table_setup_column(
                f"l{i}", imgui.TableColumnFlags_.width_fixed, col_label_w[i]
            )
            imgui.table_setup_column(f"v{i}", imgui.TableColumnFlags_.width_stretch)
        for idx, (label, render) in enumerate(fields):
            if idx % ncols == 0:
                imgui.table_next_row()
            col = (idx % ncols) * 2
            imgui.table_set_column_index(col)
            imgui.align_text_to_frame_padding()
            imgui.text_disabled(label)
            imgui.table_set_column_index(col + 1)
            render()
        imgui.end_table()

    def _name_input(self, device: Device) -> None:
        """The device name. Without a custom name the default one is shown in grey italics and
        filled in when the field is entered; leaving it with the default name keeps no custom
        name."""
        default = device.display_name
        placeholder = not device.name and not self._name_prefilled
        imgui.set_next_item_width(-1)
        with italic() if placeholder else nullcontext():
            _, self._name_buffer = imgui.input_text_with_hint(
                "##name", default if placeholder else "", self._name_buffer
            )
        if imgui.is_item_activated() and not device.name:
            self._name_buffer = default
            self._name_prefilled = True
            state = imgui.internal.get_input_text_state(imgui.get_item_id())
            # The bindings type the state as non-optional, but it is None without an active edit.
            if state is not None:  # pyright: ignore[reportUnnecessaryComparison]
                state.reload_user_buf_and_move_to_end()
        if imgui.is_item_deactivated():
            # The default name itself is not stored as a custom name.
            name = "" if self._name_buffer.strip() == default else self._name_buffer
            if name != device.name:
                self._on_name_change(device, name)
            self._name_prefilled = False
        if not imgui.is_item_active() and self._name_buffer != device.name:
            self._name_buffer = device.name

    def _description_input(self, device: Device) -> None:
        """The device description, two lines high."""
        height = (
            imgui.get_text_line_height() * 2 + imgui.get_style().frame_padding.y * 2
        )
        _, self._description_buffer = imgui.input_text_multiline(
            "##description", self._description_buffer, imgui.ImVec2(-1, height)
        )
        if (
            imgui.is_item_deactivated_after_edit()
            and self._description_buffer != device.description
        ):
            self._on_description_change(device, self._description_buffer)
        if (
            not imgui.is_item_active()
            and self._description_buffer != device.description
        ):
            self._description_buffer = device.description

    def _address_input(self, device: Device) -> None:
        imgui.set_next_item_width(-1)
        _, self._address_buffer = imgui.input_text(
            "##individual_address", self._address_buffer
        )
        if (
            imgui.is_item_deactivated_after_edit()
            and self._address_buffer != device.individual_address
        ):
            self._on_individual_address_change(device, self._address_buffer)
        if (
            not imgui.is_item_active()
            and self._address_buffer != device.individual_address
        ):
            self._address_buffer = device.individual_address

    def _project_serial(self, device: Device) -> bytes | None:
        info = self._get_device_info(device.node_id)
        return serial_from_project(info.serial_number) if info is not None else None

    def _serial_input(self, device: Device) -> None:
        stored = serial_text(self._project_serial(device)) or ""
        imgui.set_next_item_width(-1)
        _, self._serial_buffer = imgui.input_text_with_hint(
            "##serial_number", "0000:00000000", self._serial_buffer
        )
        if imgui.is_item_deactivated_after_edit() and self._serial_buffer != stored:
            text = self._serial_buffer.strip()
            serial = parse_serial_text(text) if text else None
            if text and serial is None:
                if self._notify is not None:
                    self._notify(S.SERIAL_NUMBER_INVALID)
            elif self._on_serial_number_change is not None:
                self._on_serial_number_change(device, serial)
        if not imgui.is_item_active() and self._serial_buffer != stored:
            self._serial_buffer = stored

    def _secure_commissioning_input(self, device: Device, activated: bool) -> None:
        """The device setting "Secure Commissioning" (Activated/Deactivated) and the button that
        adds the device certificate it is commissioned with, or shows the one stored."""
        style = imgui.get_style()
        # The combo is as wide as its longest choice; the certificate button follows on the same
        # line when it fits, else below.
        combo_w = (
            max(
                imgui.calc_text_size(S.SECURE_COMMISSIONING_ON).x,
                imgui.calc_text_size(S.SECURE_COMMISSIONING_OFF).x,
            )
            + style.frame_padding.x * 2
            + imgui.get_frame_height()
            + style.item_inner_spacing.x
        )
        avail = imgui.get_content_region_avail().x
        stored = self._has_certificate is not None and self._has_certificate(device)
        button = (
            S.CONFIGURE_DEVICE_CERTIFICATE if stored else S.CONFIGURE_ADD_CERTIFICATE
        )
        button_w = imgui.calc_text_size(button).x + style.frame_padding.x * 2
        start_x = imgui.get_cursor_pos_x()
        imgui.set_next_item_width(min(combo_w, avail))
        current = S.SECURE_COMMISSIONING_ON if activated else S.SECURE_COMMISSIONING_OFF
        if begin_combo("##secure_commissioning", current):
            for value, text in (
                (True, S.SECURE_COMMISSIONING_ON),
                (False, S.SECURE_COMMISSIONING_OFF),
            ):
                if (
                    imgui.selectable(text, value == activated)[0]
                    and value != activated
                    and self._on_secure_commissioning_change is not None
                ):
                    self._on_secure_commissioning_change(device, value)
            imgui.end_combo()
        if self._on_add_certificate is not None:
            if combo_w + style.item_spacing.x + button_w <= avail:
                imgui.same_line()
            else:
                imgui.set_cursor_pos_x(start_x)
            if imgui.button(button):
                self._on_add_certificate(device)

    def _routing_blocking_input(self, device: Device, current: str | None) -> None:
        """A coupler's blocking of point-to-point and broadcast messages (Automatic/On/Off)."""
        choices: list[tuple[str | None, str]] = [
            (None, S.ROUTING_BLOCKING_AUTO),
            ("On", S.ROUTING_BLOCKING_ON),
            ("Off", S.ROUTING_BLOCKING_OFF),
        ]
        label = next((text for mode, text in choices if mode == current), choices[0][1])
        imgui.set_next_item_width(-1)
        if begin_combo("##routing_blocking", label):
            for mode, text in choices:
                if (
                    imgui.selectable(text, mode == current)[0]
                    and mode != current
                    and self._on_routing_blocking is not None
                ):
                    self._on_routing_blocking(device, mode)
            imgui.end_combo()

    def _render_device_actions(self, device: Device) -> None:
        """The device's occasional actions (read-out, restart, reset) in one row that wraps when
        the panel is narrow."""
        enabled = bool(device.individual_address)
        placed = False

        imgui.separator()
        if self._on_read_device_info is not None:
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_READ_DEVICE_INFO):
                self._on_read_device_info(device)
            imgui.end_disabled()
            if not enabled and imgui.is_item_hovered(
                imgui.HoveredFlags_.allow_when_disabled
            ):
                imgui.set_tooltip(S.READOUT_NEEDS_ADDRESS)
            placed = True
        if self._on_restart_device is not None:
            if placed:
                _same_line_if_fits(S.BTN_RESTART_DEVICE)
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_RESTART_DEVICE):
                self._on_restart_device(device)
            imgui.end_disabled()
            placed = True
        if self._on_master_reset_device is not None:
            if placed:
                _same_line_if_fits(S.BTN_RESET_DEVICE)
            self._render_master_reset(device, enabled)

    def _render_application_program(self, device: Device) -> None:
        """Change the application program, or update it to the version that replaces it."""
        if device.app is None:
            imgui.text_disabled(S.CONFIGURE_NO_APPLICATION)
            return
        # The change targets and update-version lookups parse the application; defer them until it
        # is loaded so selecting a snapshot device does not parse synchronously on the render thread.
        if not self._ready(device):
            return
        if (
            self._get_change_targets is not None
            and self._on_change_application is not None
        ):
            imgui.text_disabled(S.CHANGE_APPLICATION)
            targets = self._get_change_targets(device)
            current = self._current_app_version(device)
            label = (
                application_program_label(device.app.name, current)
                if current is not None
                else device.app.name
            )
            imgui.set_next_item_width(-1)
            imgui.begin_disabled(not targets)
            if begin_combo("##change_application", label):
                imgui.selectable(f"{label}##current_application", True)
                for program in targets:
                    assert program.application_version is not None
                    text = application_program_label(
                        program.application_name or program.id,
                        program.application_version,
                    )
                    if imgui.selectable(f"{text}##{program.id}", False)[0]:
                        self._on_change_application(device, program)
                imgui.end_combo()
            imgui.end_disabled()
        if self._on_update_application is not None:
            imgui.text_disabled(S.UPDATE_APPLICATION_VERSION)
            version = (
                self._get_update_version(device)
                if self._get_update_version is not None
                else None
            )
            imgui.begin_disabled(version is None)
            if imgui.button(S.BTN_UPDATE):
                self._on_update_application(device)
            imgui.end_disabled()
            if version is not None and imgui.is_item_hovered():
                imgui.set_tooltip(
                    S.CONFIGURE_UPDATE_BUTTON.format(
                        version=application_version_text(version)
                    )
                )

    def _render_manufacturer_section(self, device: Device) -> None:
        """Manufacturer, product and application details of the selected device."""
        info = self._get_device_info(device.node_id)
        manufacturer = (
            info.manufacturer_name if info and info.manufacturer_name else None
        )
        app_version = self._current_app_version(device)
        product = (
            self._find_product(info.product_ref_id)
            if info is not None and self._find_product is not None
            else None
        )
        # Show every field we have, always (empty -> "-"), so device details are complete and every
        # value can be copied. The flat label/value pairs flow into side-by-side columns when the
        # panel is wide; the manual, online versions and read-out follow full width below.
        # device.app.version is the KNX XML schema version (e.g. "20"), NOT the program version above.
        self._render_detail_grid(
            [
                (
                    S.CONFIGURE_MANUFACTURER,
                    manufacturer or (device.app.manufacturer_id if device.app else ""),
                ),
                (S.CONFIGURE_APPLICATION, device.app.id if device.app else ""),
                (
                    S.CONFIGURE_APP_VERSION,
                    application_version_text(app_version)
                    if app_version is not None
                    else "-",
                ),
                (S.CONFIGURE_SCHEMA_VERSION, device.app.version if device.app else ""),
                (S.CONFIGURE_ORDER_NUMBER, info.order_number if info else ""),
                (S.CONFIGURE_HARDWARE, info.hardware_name if info else ""),
                (S.CONFIGURE_RAIL, rail_text(product, S.BTN_YES, S.BTN_NO)),
                (S.CONFIGURE_BUS_CURRENT, bus_current_text(product)),
                (S.CONFIGURE_PRODUCT, info.product_name if info else ""),
                (S.CONFIGURE_PRODUCT_REF, info.product_ref_id if info else ""),
                (
                    S.CONFIGURE_PROGRAM_REF,
                    (info.hardware2program_ref_id or "") if info else "",
                ),
            ]
        )
        self._render_load_procedures(device)

    def render(self) -> None:
        device = self._current_device()
        if device is None:
            imgui.text_disabled(S.CONFIGURE_NO_DEVICES)
            return
        self._begin_details()
        self._render_title_row(device)
        imgui.separator()
        if not self._ready(device):
            _render_loading_application()
            return

        if imgui.begin_tab_bar("##editor_tabs"):
            ui_nodes = device.get_ui()
            param_count, module_tables = self._ui_derived_tables(ui_nodes)
            # Stable "###" ids keep the selected tab (and each panel's expansion state) put when a
            # parameter edit changes a tab's count-bearing label; a bare label folds the count into
            # the ImGui id, so the tab would lose identity and the bar would jump on every change.
            if imgui.begin_tab_item(
                f"{S.EDITOR_TAB_PARAMETERS.format(count=param_count)}###editor_tab_params"
            )[0]:
                if parameters_in_plugin(device):
                    # The standard pages would let the plugin's settings be edited without it.
                    with heading():
                        imgui.text_wrapped(S.PLUGIN_PARAMETERS_TITLE)
                    imgui.text_wrapped(S.PLUGIN_PARAMETERS_TEXT)
                elif ui_nodes:
                    self._param_filter = filter_box(
                        "##param_filter",
                        S.CONFIGURE_PARAM_FILTER_HINT,
                        self._param_filter,
                    )
                    joint = self._joint_devices(device)
                    differing: frozenset[str] = frozenset()
                    if len(joint) > 1:
                        self._multi_targets = [d.node_id for d in joint]
                        differing = self._differing_refs(joint)
                        imgui.text_disabled(
                            S.CONFIGURE_MULTI_EDIT.format(count=len(joint))
                        )
                    else:
                        self._multi_targets = []
                        if self._render_multi_fill_toggle(device):
                            _same_line_if_fits_checkbox(S.CONFIGURE_APPLY_ALL_CHANNELS)
                        _, self._apply_all_channels = checkbox(
                            S.CONFIGURE_APPLY_ALL_CHANNELS, self._apply_all_channels
                        )
                    help_text = self._help_text(device) if self.show_help else None
                    imgui.begin_disabled(device.script_running)
                    render_ui_tree(
                        device,
                        ui_nodes,
                        self._dispatch_param,
                        filter_text=self._param_filter,
                        differing_refs=differing,
                        buttons=self._button_actions(multi=len(joint) > 1),
                        get_icon=self._icon_getter(device),
                        get_picture=self._picture_getter(device),
                        footer=(lambda h: self._render_help(help_text, h))
                        if help_text
                        else None,
                        footer_height=self._help_height() if help_text else 0.0,
                    )
                    imgui.end_disabled()
                elif not device.has_application:
                    imgui.text_disabled(S.CONFIGURE_NO_APPLICATION)
                else:
                    imgui.text_disabled(S.CONFIGURE_NO_DEVICES)
                imgui.end_tab_item()

            visible_cos = device.get_visible_com_objects()
            if imgui.begin_tab_item(
                f"{S.EDITOR_TAB_GROUP_OBJECTS.format(count=len(visible_cos))}###editor_tab_gos"
            )[0]:
                self._group_objects_table.render(
                    device, visible_cos, self._get_links, self._get_all_gas
                )
                imgui.end_tab_item()

            # "Channels" tab: repeating module instances (e.g. a DALI gateway's ECGs/groups) pivoted
            # into an editable table. Manufacturer-agnostic; only shown when the device has any.
            if module_tables:
                total = sum(len(t.rows) for t in module_tables)
                if imgui.begin_tab_item(
                    f"{S.EDITOR_TAB_MODULES.format(count=total)}###editor_tab_modules"
                )[0]:
                    self._module_filter = filter_box(
                        "##module_filter",
                        S.CONFIGURE_PARAM_FILTER_HINT,
                        self._module_filter,
                    )
                    render_module_tables(
                        device,
                        module_tables,
                        self._dispatch_param,
                        filter_text=self._module_filter,
                    )
                    imgui.end_tab_item()

            # "DALI (Bus)" tab: live commissioning of an MDT DALI gateway (scan/identify/install).
            # Only for MDT DALI apps and only when a renderer is wired (needs a bus connection).
            if (
                self._render_dali is not None
                and device.app is not None
                and is_dali_dca_app(device.app.id)
                and imgui.begin_tab_item(f"{S.EDITOR_TAB_DALI}###editor_tab_dali")[0]
            ):
                self._render_dali(device)
                imgui.end_tab_item()

            imgui.end_tab_bar()

    def _render_manual_button(self, info: "DeviceInfo | None") -> bool:
        """Best-effort: find the device's manual and open it in the browser. Resolves via the KNX
        device database (parsed) and, failing that, a DuckDuckGo ``site:<manufacturer>`` search — see
        editor_gui.doc_links. The lookup does network I/O, so it runs on a daemon thread and the
        browser is opened from there; the button shows a busy state meanwhile. Returns whether a
        button was shown (so the action row can place the next one beside it)."""
        if info is None or not (info.order_number or info.manufacturer_name):
            return False
        # OpenKNX devices (manufacturer KNX Association) have no manufacturer manual to find.
        if info.product_ref_id.startswith(_OPENKNX_PRODUCT_PREFIX):
            return False
        if self._manual_busy:
            imgui.begin_disabled(True)
            imgui.button(S.CONFIGURE_OPEN_MANUAL_BUSY)
            imgui.end_disabled()
            return True
        if imgui.button(S.CONFIGURE_OPEN_MANUAL):
            self._manual_busy = True
            threading.Thread(
                target=self._open_manual,
                args=(info.manufacturer_name, info.order_number, info.product_name),
                daemon=True,
            ).start()
        if imgui.is_item_hovered():
            imgui.set_tooltip(S.CONFIGURE_OPEN_MANUAL_HINT)
        return True

    def _open_manual(
        self, manufacturer: str, order_number: str, product_name: str
    ) -> None:
        """Resolve and open the manual URL off the UI thread; always opens something (KNX search as
        last resort). Only touches ``webbrowser`` and a plain bool flag, never imgui."""
        _log.info("open manual", manufacturer=manufacturer, order=order_number)
        try:
            url = doc_links.resolve_manual_url(
                manufacturer, order_number, product_name
            ) or doc_links.knx_search_url(order_number, manufacturer)
            webbrowser.open(url)
            _log.info("manual opened", url=url)
        except Exception as exc:
            _log.warning("manual lookup failed", order=order_number, error=str(exc))
        finally:
            self._manual_busy = False

    def _current_app_version(self, device: Device) -> int | None:
        """The application program version parsed from the app id (``...-26-...`` -> 38), else the
        application's own version. ``device.app.version`` is the KNX XML schema version instead."""
        if device.app is None:
            return None
        parsed = parse_app_id(device.app.id)
        return (
            parsed.version
            if parsed is not None
            else device.app.program.application_version
        )

    def _ui_derived_tables(
        self, ui_nodes: list[UiNode]
    ) -> tuple[int, list[ModuleTable]]:
        """Parameter count and module ("Channels") tables for the device UI tree, cached by
        id(ui_nodes). Both walk the whole tree (count_parameters, build_module_tables with regex) and
        ran every frame of the Configure panel — build_module_tables even when its tab is inactive,
        only to decide tab visibility. device.get_ui() returns a fresh list after a parameter edit, so
        the id key recomputes exactly then."""
        key = id(ui_nodes)
        if self._ui_derived_key != key or self._ui_derived is None:
            self._ui_derived = (
                count_parameters(ui_nodes),
                build_module_tables(ui_nodes),
            )
            self._ui_derived_key = key
        return self._ui_derived

    def _render_master_reset(self, device: Device, enabled: bool) -> None:
        """Destructive master-reset action: a button opening a confirm popup with an erase-type
        picker; the reset only fires after an explicit acknowledge checkbox + button."""
        if self._on_master_reset_device is None:
            return
        imgui.begin_disabled(not enabled)
        if imgui.button(S.BTN_RESET_DEVICE):
            self._reset_armed = False
            self._reset_erase_idx = 0
            imgui.open_popup(S.RESET_POPUP_TITLE)
        imgui.end_disabled()
        if not begin_dialog(S.RESET_POPUP_TITLE):
            return
        imgui.push_text_wrap_pos(px(360.0))
        imgui.text_colored(imgui.ImVec4(0.95, 0.55, 0.35, 1.0), S.RESET_WARNING)
        imgui.pop_text_wrap_pos()
        presets = _reset_presets()
        # The label left of the drop-down, which ends where the warning wraps.
        imgui.align_text_to_frame_padding()
        imgui.text(S.RESET_TYPE)
        value_x = label_column_width(S.RESET_TYPE, minimum=80.0)
        imgui.same_line(value_x)
        imgui.set_next_item_width(px(360.0) - value_x)
        _, self._reset_erase_idx = combo(
            "##reset_type", self._reset_erase_idx, [label for label, _ in presets]
        )
        _, self._reset_armed = checkbox(S.RESET_CONFIRM, self._reset_armed)
        clicked = dialog_buttons(
            S.RESET_EXECUTE, S.BTN_CANCEL, enabled=[self._reset_armed, True]
        )
        if clicked == 0:
            _, code = presets[self._reset_erase_idx]
            self._on_master_reset_device(device, code)
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _matching_count(self, device: Device) -> int:
        """Number of devices (incl. this one) running the same application program."""
        app_id = getattr(device.app, "id", None)
        if app_id is None:
            return 1
        return sum(
            1 for d in self._get_devices() if getattr(d.app, "id", None) == app_id
        )

    def _render_multi_fill_toggle(self, device: Device) -> bool:
        """Multi-fill toggle: apply parameter edits to all identical devices at once. Returns
        whether it is shown."""
        if self._on_param_change_all is None:
            return False
        count = self._matching_count(device)
        if count <= 1:
            self._apply_all = False
            return False  # nothing to fill into
        _, self._apply_all = checkbox(
            S.CONFIGURE_APPLY_ALL.format(count=count), self._apply_all
        )
        return True

    def _joint_devices(self, primary: Device) -> list[Device]:
        """Selected devices (incl. the primary) that share the primary's application, in the order
        returned by ``get_devices``. Only these can be jointly edited (ref_ids are app-scoped).
        Returns just ``[primary]`` when multi-select is not wired or the selection is a single row,
        and while the device shown before the selection changed stays shown."""
        if (
            self._holding
            or self._get_selected_node_ids is None
            or self._on_param_change_selected is None
        ):
            return [primary]
        ids = self._get_selected_node_ids()
        if len(ids) <= 1:
            return [primary]
        app_id = getattr(primary.app, "id", None)
        joint = [
            d
            for d in self._get_devices()
            if d.node_id in ids and getattr(d.app, "id", None) == app_id
        ]
        if all(d.node_id != primary.node_id for d in joint):
            joint.insert(0, primary)
        return joint

    def _differing_refs(self, joint: list[Device]) -> frozenset[str]:
        """Cached set of ref_ids whose value diverges across ``joint``. Recomputed only when the
        selection changes (computing it builds every device's DynamicUI, far too costly per frame).
        The key includes each device's object identity, so a rebuild (undo/redo, import, any _bump
        that recreates Device objects) invalidates it — an in-place param edit keeps it (we drop the
        just-edited ref explicitly in _dispatch_param)."""
        key = frozenset((d.node_id, id(d)) for d in joint)
        if key != self._diff_selection:
            self._diff_selection = key
            self._diff_refs = differing_param_refs(joint)
        return self._diff_refs

    def _button_actions(self, *, multi: bool) -> ButtonActions | None:
        """Buttons act on one device only, so they are disabled in a multi-device edit."""
        buttons = self._buttons
        if buttons is None or not multi:
            return buttons
        return ButtonActions(
            on_click=buttons.on_click,
            state=lambda _d, _b: (False, None),
            error=buttons.error,
        )

    def _icon_getter(self, device: Device) -> Callable[[str], Icon | None] | None:
        get_icon = self._get_icon
        if get_icon is None:
            return None
        return lambda name: get_icon(device, name)

    def _picture_getter(self, device: Device) -> Callable[[str], Picture | None] | None:
        get_picture = self._get_picture
        if get_picture is None:
            return None
        return lambda ref_id: get_picture(device, ref_id)

    def _help_text(self, device: Device) -> str | None:
        selected = take_selected_help()
        if selected is not None:
            self._help = (device.node_id, selected)
        if self._get_help is None or self._help is None:
            return None
        node_id, context = self._help
        if node_id != device.node_id:
            return None
        return self._get_help(device, context) or None

    def _help_height(self) -> float:
        """Height of the splitter and pane of the help below the parameters."""
        return px(_HELP_SPLITTER) + self._help_pane_height()

    def _help_pane_height(self) -> float:
        if self._help_pane_h <= 0:
            stored = load_settings("app").get(_HELP_HEIGHT_SETTING)
            if not isinstance(stored, int | float) or stored <= 0:
                stored = _HELP_HEIGHT
            self._help_pane_h = px(float(stored))
        return self._help_pane_h

    def _save_help_height(self) -> None:
        data = load_settings("app")
        data[_HELP_HEIGHT_SETTING] = round(self._help_pane_h / px(1.0), 1)
        save_settings("app", data)

    def _render_help_splitter(self) -> None:
        """The line above the help, which resizes the help when dragged."""
        top_left = imgui.get_cursor_screen_pos()
        width = imgui.get_content_region_avail().x
        imgui.invisible_button(
            "##help_splitter", imgui.ImVec2(width, px(_HELP_SPLITTER))
        )
        hovered = imgui.is_item_hovered()
        active = imgui.is_item_active()
        if hovered or active:
            imgui.set_mouse_cursor(imgui.MouseCursor_.resize_ns)
        if active:
            line = imgui.get_text_line_height_with_spacing()
            height = self._help_pane_height() - imgui.get_io().mouse_delta.y
            self._help_pane_h = min(max(height, line * 2), line * 40)
        if imgui.is_item_deactivated():
            self._save_help_height()
        color = (
            imgui.Col_.separator_active
            if active
            else imgui.Col_.separator_hovered
            if hovered
            else imgui.Col_.separator
        )
        # The line right below the parameters, the rest on the help's background.
        line_bottom = top_left.y + px(_HELP_LINE)
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(
            top_left,
            imgui.ImVec2(top_left.x + width, line_bottom),
            imgui.get_color_u32(color),
        )
        draw_list.add_rect_filled(
            imgui.ImVec2(top_left.x, line_bottom),
            imgui.ImVec2(top_left.x + width, top_left.y + px(_HELP_SPLITTER)),
            imgui.get_color_u32(side_background()),
        )

    def _render_help(self, text: str, height: float) -> None:
        # The splitter right below the parameters and the help right below it, without the spacing
        # between items.
        spacing = imgui.get_style().item_spacing.y
        _move_cursor_up(spacing)
        height += spacing
        top = imgui.get_cursor_screen_pos().y
        self._render_help_splitter()
        _move_cursor_up(spacing)
        pane = height - (imgui.get_cursor_screen_pos().y - top)
        # No frame of its own: the help text on the page tree's background, with a scrollbar at the
        # right edge when it is longer than the pane.
        imgui.push_style_var(imgui.StyleVar_.window_padding, px_vec2(*_HELP_PADDING))
        with child_background(side_background()):
            help_open = imgui.begin_child(
                "##param_help",
                imgui.ImVec2(0, max(pane, 1.0)),
                imgui.ChildFlags_.always_use_window_padding,
            )
        imgui.pop_style_var()
        if help_open:
            render_markdown(text)
        imgui.end_child()

    def _dispatch_param(self, device: Device, ref_id: str, value: str) -> None:
        """Route a parameter edit: to the whole selected same-app subset (multi mode), to all
        identical devices (multi-fill toggle), or to just this device."""
        if self._multi_targets and self._on_param_change_selected is not None:
            self._on_param_change_selected(self._multi_targets, ref_id, value)
            self._diff_refs = self._diff_refs - {
                ref_id
            }  # now converged across the subset
        elif self._apply_all and self._on_param_change_all is not None:
            self._on_param_change_all(device, ref_id, value)
        elif self._apply_all_channels:
            # Mirror the edit onto the same parameter in every other active channel of this device.
            # Targets are resolved from the pre-edit tree, so applying them stays consistent.
            targets = channel_apply_targets(device.get_ui(), ref_id)
            self._on_param_change(device, ref_id, value)
            for target in targets:
                self._on_param_change(device, target, value)
        else:
            self._on_param_change(device, ref_id, value)

    def _render_load_procedures(self, device: Device) -> None:
        if device.app is None:
            return
        lp = device.app.load_procedures
        procedures = getattr(lp, "procedures", None)
        if not procedures:
            return
        total_steps = sum(len(p.steps) for p in procedures)
        if not imgui.collapsing_header(
            S.CONFIGURE_LOAD_PROCEDURES.format(count=total_steps)
        ):
            return
        imgui.text_disabled(getattr(lp, "style", ""))
        for i, proc in enumerate(procedures):
            procedure = S.LOAD_PROCEDURE.format(number=i + 1, count=len(proc.steps))
            label = f"{procedure}##lp{i}"
            if imgui.tree_node(label):
                _table_flags = (
                    imgui.TableFlags_.borders_outer
                    | imgui.TableFlags_.borders_inner_v
                    | imgui.TableFlags_.sizing_stretch_prop
                    | imgui.TableFlags_.resizable
                    | imgui.TableFlags_.reorderable
                )
                if begin_data_table(f"##lpt{i}", 3, _table_flags):
                    stretch = imgui.TableColumnFlags_.width_stretch
                    imgui.table_setup_column(S.LOAD_COL_KIND, stretch, 0.3)
                    imgui.table_setup_column(S.LOAD_COL_APPLIES_TO, stretch, 0.15)
                    imgui.table_setup_column(S.LOAD_COL_DETAILS, stretch, 0.55)
                    data_table_headers()
                    for step in proc.steps:
                        imgui.table_next_row()
                        imgui.table_set_column_index(0)
                        imgui.text(step.kind)
                        imgui.table_set_column_index(1)
                        imgui.text_disabled(step.applies_to)
                        imgui.table_set_column_index(2)
                        imgui.text_disabled(step.details)
                    imgui.end_table()
                imgui.tree_pop()

    def _render_ip_config(self, device: Device) -> None:
        """Read-only IP configuration of an IP router/interface, carried verbatim from import. Only
        shown when the device has static values (IP/subnet/gateway/MAC); a plain ``Assign="Auto"``
        with no addresses carries nothing worth showing, so the header stays hidden then."""
        info = self._get_device_info(device.node_id)
        cfg = info.ip_config if info else None
        # Nothing, or only the Assign method with no static address -> not worth a section.
        if not cfg or not (cfg.keys() - {"Assign"}):
            return
        imgui.spacing()
        if not imgui.collapsing_header(S.CONFIGURE_IP_CONFIG):
            return
        self._render_detail_grid(
            [
                (S.CONFIGURE_IP_ASSIGN, cfg.get("Assign", "")),
                (S.CONFIGURE_IP_ADDRESS, cfg.get("IPAddress", "")),
                (S.CONFIGURE_IP_SUBNET, cfg.get("SubnetMask", "")),
                (S.CONFIGURE_IP_GATEWAY, cfg.get("DefaultGateway", "")),
                (S.CONFIGURE_IP_MAC, cfg.get("MACAddress", "")),
            ]
        )

    def _render_additional_addresses(self, device: Device) -> None:
        """The device's additional individual addresses (the tunnelling addresses of an IP
        interface): per entry its address, name and description, all editable, and the button that
        assigns the missing addresses. Shown when the device has entries or its application has
        some; a rejected edit shows its message above the table."""
        info = self._get_device_info(device.node_id)
        entries = list(info.additional_addresses) if info is not None else []
        # The application's number needs the parsed application: only read it once it is loaded.
        count = (
            self._get_additional_address_count(device)
            if self._get_additional_address_count is not None and self._ready(device)
            else 0
        )
        rows = max(len(entries), count)
        if rows == 0:
            return
        if self._additional_device != device.node_id:
            self._additional_buffers = {}
            self._additional_error = ""
            self._additional_device = device.node_id
        imgui.spacing()
        if not imgui.collapsing_header(S.CONFIGURE_ADDITIONAL_ADDRESSES):
            return
        if self._on_assign_additional_addresses is not None:
            missing = rows > len(entries) or any(e.address is None for e in entries)
            imgui.begin_disabled(not missing)
            if imgui.button(S.BTN_ASSIGN_ADDITIONAL_ADDRESSES):
                self._additional_error = (
                    self._on_assign_additional_addresses(device) or ""
                )
            imgui.end_disabled()
        if self._additional_error:
            imgui.push_style_color(imgui.Col_.text, _ERROR_COLOR)
            imgui.text_wrapped(self._additional_error)
            imgui.pop_style_color()
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.resizable
        )
        if not begin_data_table("##additional_addresses", 4, flags):
            return
        imgui.table_setup_column(
            S.ADDITIONAL_ADDRESS_INDEX, imgui.TableColumnFlags_.width_fixed
        )
        imgui.table_setup_column(S.CONFIGURE_ADDITIONAL_ADDRESS)
        imgui.table_setup_column(S.CONFIGURE_NAME)
        imgui.table_setup_column(S.CONFIGURE_DESCRIPTION)
        data_table_headers()
        by_index = {entry.index: entry for entry in entries}
        for index in range(1, rows + 1):
            entry = by_index.get(index)
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            imgui.text(str(index))
            for field, value in (
                ("address", (entry.address or "") if entry is not None else ""),
                ("name", entry.name if entry is not None else ""),
                ("description", entry.description if entry is not None else ""),
            ):
                imgui.table_next_column()
                self._additional_input(device, index, field, value)
        imgui.end_table()

    def _additional_input(
        self, device: Device, index: int, field: str, value: str
    ) -> None:
        """One field of an additional address; an edit is stored when the field is left."""
        key = f"{field}:{index}"
        buffer = self._additional_buffers.get(key, value)
        imgui.set_next_item_width(-1)
        _, buffer = imgui.input_text(f"##additional_{key}", buffer)
        self._additional_buffers[key] = buffer
        if (
            imgui.is_item_deactivated_after_edit()
            and buffer != value
            and self._on_additional_address_change is not None
        ):
            self._additional_error = (
                self._on_additional_address_change(device, index, field, buffer) or ""
            )
        if not imgui.is_item_active() and buffer != value:
            self._additional_buffers[key] = value

    def _render_ip_secure(self, device: Device) -> None:
        """The KNX IP Secure settings of a secure IP device: its commissioning password, its
        authentication code, whether its tunnels are secured and the password of each bus
        interface (with its user id), all editable. The passwords are hidden unless shown."""
        settings = (
            self._get_ip_secure(device) if self._get_ip_secure is not None else None
        )
        if settings is None:
            return
        if self._ip_secure_device != device.node_id:
            self._ip_secure_buffers = {}
            self._ip_secure_device = device.node_id
            self._ip_secure_show = False
        imgui.spacing()
        if not imgui.collapsing_header(S.CONFIGURE_IP_SECURE):
            return
        _, self._ip_secure_show = checkbox(
            S.CONFIGURE_IP_SECURE_SHOW, self._ip_secure_show
        )
        changed, secure = checkbox(
            S.CONFIGURE_IP_SECURE_TUNNELLING, settings.secure_tunnelling
        )
        if changed:
            self._ip_secure_edit(device, "secure_tunnelling", secure)
        for key, label, value in (
            (
                "commissioning_password",
                S.CONFIGURE_IP_SECURE_PASSWORD,
                settings.commissioning_password,
            ),
            (
                "authentication_code",
                S.CONFIGURE_IP_SECURE_AUTHENTICATION,
                settings.authentication_code,
            ),
        ):
            imgui.align_text_to_frame_padding()
            imgui.text_disabled(label)
            imgui.same_line(self._label_w)
            self._ip_secure_input(device, key, value, key)
        if not settings.interfaces:
            return
        imgui.spacing()
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.reorderable
        )
        if not begin_data_table("##ip_secure_interfaces", 3, flags):
            return
        imgui.table_setup_column(S.CONFIGURE_IP_SECURE_INTERFACE)
        imgui.table_setup_column(
            S.CONFIGURE_IP_SECURE_USER, imgui.TableColumnFlags_.width_fixed
        )
        imgui.table_setup_column(S.CONFIGURE_IP_SECURE_INTERFACE_PASSWORD)
        data_table_headers()
        for interface in settings.interfaces:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            if interface.access == "Routing":
                imgui.text(S.CONFIGURE_IP_SECURE_ROUTING)
            else:
                imgui.text(
                    S.CONFIGURE_IP_SECURE_TUNNEL.format(
                        address=interface.address or "-"
                    )
                )
            imgui.table_next_column()
            imgui.text(str(interface.user_id))
            imgui.table_next_column()
            self._ip_secure_input(
                device,
                f"interface:{interface.ref_id}",
                interface.password,
                "interface_password",
                interface.ref_id,
            )
        imgui.end_table()

    def _ip_secure_input(
        self, device: Device, key: str, value: str, field: str, ref: str = ""
    ) -> None:
        """A KNX IP Secure password field (hidden unless shown) with a copy button; an edit is
        stored when the field is left."""
        button = S.CONFIGURE_IP_SECURE_COPY
        button_w = (
            imgui.calc_text_size(button).x + imgui.get_style().frame_padding.x * 2
        )
        buffer = self._ip_secure_buffers.get(key, value)
        imgui.set_next_item_width(
            max(px(80), imgui.get_content_region_avail().x - button_w - px(8))
        )
        flags = (
            imgui.InputTextFlags_.none
            if self._ip_secure_show
            else imgui.InputTextFlags_.password
        )
        _, buffer = imgui.input_text(f"##ip_secure_{key}", buffer, flags)
        self._ip_secure_buffers[key] = buffer
        if imgui.is_item_deactivated_after_edit() and buffer != value:
            self._ip_secure_edit(device, field, (ref, buffer) if ref else buffer)
        if not imgui.is_item_active() and buffer != value:
            self._ip_secure_buffers[key] = value
        imgui.same_line()
        if imgui.button(f"{button}##copy_{key}") and value:
            imgui.set_clipboard_text(value)
            if self._notify is not None:
                self._notify(S.CONFIGURE_COPIED)

    def _ip_secure_edit(self, device: Device, field: str, value: object) -> None:
        if self._on_ip_secure_change is None:
            return
        error = self._on_ip_secure_change(device, field, value)
        if error and self._notify is not None:
            self._notify(error)

    def _render_label_value(self, label: str, value: str) -> None:
        imgui.text_disabled(label)
        imgui.same_line(self._label_w)
        self._value_copy(value)

    def _value_copy(self, value: str) -> None:
        """Render a field value as a full-width click-to-copy target (or ``-`` when empty)."""
        if not value:
            imgui.text_disabled("-")
            return
        # The value is a click target: click anywhere on it to copy, with a short "Copied" toast. A
        # per-frame sequence makes the id unique even when the same label+value appears twice (e.g.
        # "Applikationsversion V38" in the catalog and live sections), which would otherwise trigger
        # imgui's conflicting-ID warning.
        self._lv_seq += 1
        if imgui.selectable(f"{value}##copy_{self._lv_seq}", False)[0]:
            imgui.set_clipboard_text(value)
            if self._notify is not None:
                self._notify(S.CONFIGURE_COPIED)
        if imgui.is_item_hovered():
            imgui.set_tooltip(S.CONFIGURE_COPY_HINT)

    def _render_detail_grid(self, pairs: list[tuple[str, str]]) -> None:
        """Lay out label/value detail pairs in as many side-by-side columns as the panel width
        allows (1-3), so a long list of fields stays compact instead of one tall stack. Falls back
        to a single column on a narrow panel."""
        if not pairs:
            return
        avail = imgui.get_content_region_avail().x
        # Each column group needs the shared label width plus a readable value column.
        group_w = self._label_w + px(160)
        ncols = max(1, min(3, len(pairs), int(avail // group_w) if group_w > 0 else 1))
        if ncols <= 1:
            for label, value in pairs:
                self._render_label_value(label, value)
            return
        if not imgui.begin_table("##detail_grid", ncols * 2):
            return
        for i in range(ncols):
            imgui.table_setup_column(
                f"l{i}", imgui.TableColumnFlags_.width_fixed, self._label_w
            )
            imgui.table_setup_column(f"v{i}", imgui.TableColumnFlags_.width_stretch)
        for idx, (label, value) in enumerate(pairs):
            if idx % ncols == 0:
                imgui.table_next_row()
            col = (idx % ncols) * 2
            imgui.table_set_column_index(col)
            imgui.align_text_to_frame_padding()
            imgui.text_disabled(label)
            imgui.table_set_column_index(col + 1)
            self._value_copy(value)
        imgui.end_table()

    def _direct_program_request(self, device: Device) -> ProgramRequest | None:
        scope = self._download_scope
        serial = (
            self._project_serial(device)
            if scope is DownloadScope.INDIVIDUAL_ADDRESS
            else None
        )
        use_serial = bool(
            serial is not None
            and self._use_serial_number_default
            and self._use_serial_number_default()
        )
        owner = (
            self._find_serial_owner(device, serial)
            if use_serial and serial is not None and self._find_serial_owner is not None
            else None
        )
        return direct_program_request(
            scope,
            plugin_required=device.download_plugin_required,
            use_serial_number=use_serial,
            serial_owner=owner,
        )

    def _program_dialog(self, device: Device) -> tuple[str, str]:
        """Title and confirm button of the dialog a Program press opens when it has to ask
        something first (see direct_program_request)."""
        scope = self._download_scope
        if scope is DownloadScope.UNLOAD:
            return S.PROGRAM_UNLOAD_TITLE, S.SCOPE_UNLOAD
        if scope is DownloadScope.UNLOAD_ALL:
            return S.SCOPE_UNLOAD_ALL, S.SCOPE_UNLOAD
        if scope is DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS:
            return S.SCOPE_OVERWRITE_INDIVIDUAL_ADDRESS, S.BTN_OVERWRITE_ADDRESS
        if device.download_plugin_required and not scope.individual_address_only:
            return S.PROGRAM_PLUGIN_TITLE, S.BTN_PROGRAM_DEVICE
        return S.PROGRAM_SERIAL_USED_TITLE, S.BTN_PROGRAM_DEVICE

    def _render_program_confirm(self, device: Device) -> None:
        title, confirm = self._program_dialog(device)
        if not begin_dialog(f"{title}{_PROGRAM_DIALOG_ID}"):
            return
        scope = self._download_scope
        address = device.individual_address or "?"
        serial = (
            self._project_serial(device)
            if scope is DownloadScope.INDIVIDUAL_ADDRESS
            else None
        )
        use_serial = serial is not None and self._program_use_serial
        plugin_required = (
            device.download_plugin_required and not scope.individual_address_only
        )
        imgui.push_text_wrap_pos(px(420.0))
        if scope is DownloadScope.UNLOAD:
            imgui.text_wrapped(S.PROGRAM_UNLOAD_TEXT.format(address=address))
        elif scope is DownloadScope.UNLOAD_ALL:
            imgui.text_wrapped(S.PROGRAM_UNLOAD_ALL_TEXT.format(address=address))
        elif scope is DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS:
            imgui.text_wrapped(S.PROGRAM_OVERWRITE_TEXT.format(address=address))
        elif use_serial and serial is not None:
            imgui.text_wrapped(
                S.PROGRAM_ADDRESS_SERIAL_TEXT.format(
                    address=address, serial=serial_text(serial)
                )
            )
        elif scope is DownloadScope.INDIVIDUAL_ADDRESS:
            imgui.text_wrapped(S.PROGRAM_ADDRESS_TEXT.format(address=address))
        imgui.pop_text_wrap_pos()
        imgui.spacing()
        if serial is not None:
            _, self._program_use_serial = checkbox(
                S.PROGRAM_USE_SERIAL_NUMBER, self._program_use_serial
            )
            owner = (
                self._find_serial_owner(device, serial)
                if use_serial and self._find_serial_owner is not None
                else None
            )
            if owner is not None:
                imgui.text_colored(
                    _PLUGIN_NOTICE_COLOR,
                    S.SERIAL_NUMBER_ALREADY_USED.format(device=owner),
                )
            imgui.spacing()
        address_error: str | None = None
        current_address: str | None = None
        if scope is DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS:
            imgui.align_text_to_frame_padding()
            imgui.text(S.OVERWRITE_CURRENT_ADDRESS)
            imgui.same_line()
            imgui.set_next_item_width(px(120.0))
            if imgui.is_window_appearing():
                imgui.set_keyboard_focus_here()
            _, self._overwrite_current_address = imgui.input_text(
                "##overwrite_current_address", self._overwrite_current_address
            )
            current_address, address_error = overwrite_current_address(
                self._overwrite_current_address, device.individual_address
            )
            if address_error is not None and self._overwrite_current_address.strip():
                imgui.text_colored(_PLUGIN_NOTICE_COLOR, address_error)
            imgui.spacing()
        if plugin_required:
            imgui.push_text_wrap_pos(px(420.0))
            imgui.text_colored(_PLUGIN_NOTICE_COLOR, S.PLUGIN_DOWNLOAD_WARNING)
            imgui.pop_text_wrap_pos()
            _, self._program_without_plugin = checkbox(
                S.PLUGIN_DOWNLOAD_CONFIRM, self._program_without_plugin
            )
            imgui.spacing()
        can_program = (
            not plugin_required or self._program_without_plugin
        ) and address_error is None
        clicked = dialog_buttons(confirm, S.BTN_CANCEL, enabled=[can_program, True])
        if clicked == 0:
            if self._on_program_device is not None:
                self._on_program_device(
                    device,
                    ProgramRequest(
                        scope=scope,
                        without_plugin=plugin_required,
                        current_address=current_address,
                        use_serial_number=use_serial,
                    ),
                )
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _scope_combo_width(self) -> float:
        style = imgui.get_style()
        return (
            max(imgui.calc_text_size(label).x for label in _scope_labels())
            + style.frame_padding.x * 2
            + imgui.get_frame_height()
        )

    def _render_scope_selector(self) -> None:
        """Select what a download/eval covers: full, parameters, or group comm."""
        current = _SCOPE_ORDER.index(self._download_scope)
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.CONFIGURE_DOWNLOAD_SCOPE)
        imgui.same_line()
        imgui.set_next_item_width(self._scope_combo_width())
        changed, new_idx = combo("##download_scope", current, _scope_labels())
        if changed:
            self._download_scope = _SCOPE_ORDER[new_idx]

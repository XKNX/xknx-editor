import threading
import webbrowser
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from imgui_bundle import imgui
from xknx.exceptions import CouldNotParseAddress
from xknx.telegram import IndividualAddress

from editor_gui import doc_links
from editor_gui.device import ComObject, Device, device_label
from editor_gui.device_info import (
    application_program_label,
    application_version_text,
    firmware_text,
    parse_serial_text,
    serial_from_project,
    serial_text,
)
from editor_gui.device_plugins import device_plugins
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import label_column_width, px
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.fonts import italic
from xknxeditor.download.scope import DownloadScope
from xknxeditor.prod.app_id import parse_app_id
from xknxeditor.recover.dossier import printable_text

if TYPE_CHECKING:
    from editor_gui.device_info import DeviceInfo as BusDeviceInfo
    from xknxeditor.catalog import ProgramSummary
    from xknxeditor.proj.core.service import DeviceInfo
from editor_gui.programming import error_class_text
from editor_gui.programming_dali import is_dali_dca_app
from editor_gui.widgets import (
    ButtonActions,
    GroupObjectsTable,
    channel_apply_targets,
    count_parameters,
    differing_param_refs,
    render_ui_tree,
    take_selected_help,
)
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.group_objects_widgets import (
    GroupAddressCatalog,
    GroupLinkResolver,
)
from editor_gui.widgets.icons import Icon, Picture
from editor_gui.widgets.markdown import render_markdown
from editor_gui.widgets.module_table import build_module_tables, render_module_tables

_log = structlog.get_logger("configure")
_PLUGIN_NOTICE_COLOR = imgui.ImVec4(0.95, 0.75, 0.35, 1.0)


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


@dataclass(frozen=True, slots=True)
class ProgramRequest:
    """A confirmed Program press: the scope and the choices made in its confirmation."""

    scope: DownloadScope
    without_plugin: bool = False
    current_address: str | None = None
    use_serial_number: bool = False


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
        on_update_application: Callable[[Device], None] | None = None,
        get_update_version: Callable[[Device], int | None] | None = None,
        get_change_targets: "Callable[[Device], list[ProgramSummary]] | None" = None,
        on_change_application: "Callable[[Device, ProgramSummary], None] | None" = None,
        on_read_device_info: Callable[[Device], None] | None = None,
        get_device_readout: "Callable[[int], BusDeviceInfo | None] | None" = None,
        get_expected_group_addresses: "Callable[[Device], dict[int, set[int]]] | None" = None,
        get_group_address_names: "Callable[[], dict[int, str]] | None" = None,
        format_group_address: Callable[[int], str] | None = None,
        on_restart_device: Callable[[Device], None] | None = None,
        on_master_reset_device: Callable[[Device, int], None] | None = None,
        suggest_ga: Callable[[Device, ComObject], tuple[str, str]] | None = None,
        on_create_and_link: Callable[[Device, ComObject, str, str], None] | None = None,
        group_style: Callable[[], object] | None = None,
        next_free_sub: Callable[[int, int], int] | None = None,
        get_ga_range_tree: Callable[[], list[object]] | None = None,
        get_selected_node_ids: Callable[[], set[int]] | None = None,
        on_param_change_selected: Callable[[list[int], str, str], None] | None = None,
        render_dali: Callable[[Device], None] | None = None,
        on_navigate_ga: Callable[[int], None] | None = None,
        on_paste_links: Callable[[int, list[tuple[int, bool]], bool], None]
        | None = None,
        buttons: ButtonActions | None = None,
        get_help: Callable[[Device, str], str | None] | None = None,
        get_icon: Callable[[Device, str], Icon | None] | None = None,
        get_picture: Callable[[Device, str], Picture | None] | None = None,
        on_serial_number_change: Callable[[Device, bytes | None], None] | None = None,
        use_serial_number_default: Callable[[], bool] | None = None,
        find_serial_owner: Callable[[Device, bytes], str | None] | None = None,
    ) -> None:
        self._get_devices = get_devices
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
        self._on_individual_address_change = on_individual_address_change
        self._on_name_change = on_name_change
        self._on_description_change = on_description_change
        self._on_program_device = on_program_device
        self._on_eval_device = on_eval_device
        self._open_memory_preview = open_memory_preview
        self._get_links = get_links_for_com_object
        self._get_all_gas = get_all_group_addresses
        self._get_device_info = get_device_info
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
        self._get_device_readout = get_device_readout
        self._get_expected_group_addresses = get_expected_group_addresses
        self._get_group_address_names = get_group_address_names
        self._format_group_address = format_group_address
        # Cache the (DB-backed) expected group-address map and GA names of the read-out so scrolling
        # its table doesn't recompute every frame.
        self._expected_cache_key: tuple[int, int] | None = None
        self._expected_cache: dict[int, set[int]] = {}
        self._ga_names_cache_key: int | None = None
        self._ga_names_cache: dict[int, str] = {}
        self._manual_busy: bool = False  # a manual lookup thread is running
        self._group_objects_table = GroupObjectsTable(
            set_flag,
            on_link_com_object,
            on_unlink_com_object,
            on_auto_create_gas,
            suggest_ga=suggest_ga,
            on_create_and_link=on_create_and_link,
            group_style=group_style,
            next_free_sub=next_free_sub,
            get_ga_range_tree=get_ga_range_tree,
            on_navigate_ga=on_navigate_ga,
            on_paste_links=on_paste_links,
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
        """The selected device, defaulting to the first one."""
        devices = self._get_devices()
        if not devices:
            return None
        device = self._get_selected_device()
        if device is None:
            device = devices[0]
            self._set_selected_device(device)
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
            self._buffer_device_id = device.node_id

        self._render_header_fields(device)
        self._render_plugin_notices(device)
        self._render_device_actions(device)
        self._render_ip_config(device)

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
                and not self._download_scope.individual_address_only
            )
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_EVAL_DEVICE):
                self._on_eval_device(device, self._download_scope)
            imgui.end_disabled()

        if self._on_program_device is not None:
            _same_line_if_fits(S.BTN_PROGRAM_DEVICE)
            enabled = bool(device.individual_address)
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_PROGRAM_DEVICE):
                # Programming writes to the device; confirm first.
                self._confirm_program_open = True
            imgui.end_disabled()
            if self._confirm_program_open:
                imgui.open_popup(S.PROGRAM_CONFIRM_TITLE)
                self._confirm_program_open = False
                self._program_without_plugin = False
                self._overwrite_current_address = ""
                self._program_use_serial = bool(
                    self._use_serial_number_default
                    and self._use_serial_number_default()
                )

        if self._open_memory_preview is not None:
            _same_line_if_fits(S.BTN_PREVIEW_MEMORY)
            if imgui.button(S.BTN_PREVIEW_MEMORY):
                self._open_memory_preview(device)

        if self._on_program_device is not None:
            self._render_program_confirm(device)

    def _render_plugin_notices(self, device: Device) -> None:
        """Notes on the manufacturer plugins the product data asks for, which are not available."""
        plugins = device_plugins(device)
        notices = [
            text
            for shown, text in (
                (plugins.download_required, S.PLUGIN_DOWNLOAD_REQUIRED),
                (plugins.user_interface, S.PLUGIN_USER_INTERFACE),
                (plugins.download and not plugins.download_required, S.PLUGIN_DOWNLOAD),
                (plugins.data_handler, S.PLUGIN_DATA_HANDLER),
                (plugins.external_software, S.PLUGIN_EXTERNAL_SOFTWARE),
            )
            if shown
        ]
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
        if not notices:
            return
        imgui.push_style_color(imgui.Col_.text, _PLUGIN_NOTICE_COLOR)
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
        default = device.product_name or device.app.name
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
            if state is not None:
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
        if imgui.is_item_deactivated_after_edit():
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
        if imgui.is_item_deactivated_after_edit():
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
        if imgui.is_item_deactivated_after_edit():
            text = self._serial_buffer.strip()
            serial = parse_serial_text(text) if text else None
            if text and serial is None:
                if self._notify is not None:
                    self._notify(S.SERIAL_NUMBER_INVALID)
            elif self._on_serial_number_change is not None:
                self._on_serial_number_change(device, serial)
        if not imgui.is_item_active() and self._serial_buffer != stored:
            self._serial_buffer = stored

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
            if imgui.begin_combo("##change_application", label):
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
        # Show every field we have, always (empty -> "-"), so device details are complete and every
        # value can be copied. The flat label/value pairs flow into side-by-side columns when the
        # panel is wide; the manual, online versions and read-out follow full width below.
        # device.app.version is the KNX XML schema version (e.g. "20"), NOT the program version above.
        self._render_detail_grid(
            [
                (S.CONFIGURE_MANUFACTURER, manufacturer or device.app.manufacturer_id),
                (S.CONFIGURE_APPLICATION, device.app.id),
                (
                    S.CONFIGURE_APP_VERSION,
                    application_version_text(app_version)
                    if app_version is not None
                    else "-",
                ),
                (S.CONFIGURE_SCHEMA_VERSION, device.app.version),
                (S.CONFIGURE_ORDER_NUMBER, info.order_number if info else ""),
                (S.CONFIGURE_HARDWARE, info.hardware_name if info else ""),
                (S.CONFIGURE_PRODUCT, info.product_name if info else ""),
                (S.CONFIGURE_PRODUCT_REF, info.product_ref_id if info else ""),
                (
                    S.CONFIGURE_PROGRAM_REF,
                    (info.hardware2program_ref_id or "") if info else "",
                ),
            ]
        )
        imgui.spacing()
        self._render_application_program(device)
        imgui.spacing()
        self._render_manual_button(info)
        self._render_readout_result(device)
        self._render_load_procedures(device)

    def render(self) -> None:
        device = self._current_device()
        if device is None:
            imgui.text_disabled(S.CONFIGURE_NO_DEVICES)
            return
        self._begin_details()
        self._render_title_row(device)
        imgui.separator()

        if imgui.begin_tab_bar("##editor_tabs"):
            ui_nodes = device.get_ui()
            param_count = count_parameters(ui_nodes)
            # Stable "###" ids keep the selected tab (and each panel's expansion state) put when a
            # parameter edit changes a tab's count-bearing label; a bare label folds the count into
            # the ImGui id, so the tab would lose identity and the bar would jump on every change.
            if imgui.begin_tab_item(
                f"{S.EDITOR_TAB_PARAMETERS.format(count=param_count)}###editor_tab_params"
            )[0]:
                if ui_nodes:
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
            module_tables = build_module_tables(ui_nodes)
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
        parsed = parse_app_id(device.app.id)
        return (
            parsed.version
            if parsed is not None
            else device.app.program.application_version
        )

    def _render_readout_result(self, device: Device) -> None:
        """Show the state read off the bus (mask, application version, serial/order/hardware) next to
        the project's planned state. The read-out itself is triggered from the action row; this only
        renders the result once it exists."""
        readout = (
            self._get_device_readout(device.node_id)
            if self._get_device_readout is not None
            else None
        )
        if readout is None:
            return
        imgui.text_disabled(S.READOUT_TITLE)
        mask = (
            f"{readout.mask_version:#06x}" if readout.mask_version is not None else ""
        )
        # Compare the version read off the device to the project's version. Only flag a mismatch
        # (device behind/ahead of the project -> programming needed); a match needs no marker.
        app = readout.application
        app_v = app.application_version if app is not None else None
        project_v = self._current_app_version(device)
        if app_v is None:
            version_value = "-"
        elif project_v is None or app_v == project_v:
            version_value = application_version_text(app_v)
        else:
            mismatch = S.READOUT_VERSION_MISMATCH.format(
                version=application_version_text(project_v)
            )
            version_value = f"{application_version_text(app_v)}  {mismatch}"
        if app is not None:
            manufacturer = app.manufacturer_id
        elif readout.manufacturer_id is not None:
            manufacturer = f"M-{readout.manufacturer_id:04X}"
        else:
            manufacturer = ""
        serial = readout.serial_number
        order = printable_text(readout.order_number) if readout.order_number else None
        hardware = readout.hardware_type
        self._render_detail_grid(
            [
                (S.CONFIGURE_MANUFACTURER, manufacturer),
                (S.CONFIGURE_APP_VERSION, version_value),
                (S.READOUT_MASK, mask),
                (S.READOUT_SERIAL, serial.hex().upper() if serial else ""),
                (S.READOUT_ORDER, order or ""),
                (S.READOUT_HARDWARE, hardware.hex().upper() if hardware else ""),
                (S.DEVICE_INFO_FIRMWARE, firmware_text(readout.firmware_version) or ""),
            ]
        )
        self._render_diagnosis(readout)
        self._render_readout_group_objects(device, readout)

    def _render_diagnosis(self, readout: "BusDeviceInfo") -> None:
        """Light error diagnosis from the standard Device-Object properties: the error class
        (green when no fault, orange otherwise) and a programming-mode note when active."""
        if readout.error_code is not None:
            imgui.text_disabled(S.READOUT_STATUS)
            imgui.same_line()
            if readout.error_code == 0:
                imgui.text_colored(
                    imgui.ImVec4(0.45, 0.8, 0.45, 1.0), S.READOUT_STATUS_OK
                )
            else:
                imgui.text_colored(
                    imgui.ImVec4(0.95, 0.55, 0.35, 1.0),
                    error_class_text(readout.error_code) or "",
                )
        if readout.programming_mode:
            imgui.text_colored(imgui.ImVec4(0.95, 0.75, 0.35, 1.0), S.READOUT_PROG_MODE)

    def _render_readout_group_objects(
        self, device: Device, readout: "BusDeviceInfo"
    ) -> None:
        """Group objects read off the device: one row per object with its number and the group
        addresses found on it. Addresses are green when they match the project's planned links for
        that object number, orange when they differ."""
        if not readout.group_objects:
            return
        expected = self._expected_group_addresses(device, readout)
        names = self._ga_names(readout)
        imgui.spacing()
        imgui.text_disabled(S.READOUT_GROUP_OBJECTS)
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.scroll_y
        )
        if not imgui.begin_table(
            "##readout_gos", 2, flags, imgui.ImVec2(0.0, px(200.0))
        ):
            return
        imgui.table_setup_column("#", imgui.TableColumnFlags_.width_fixed, px(44.0))
        imgui.table_setup_column(S.READOUT_GROUP_ADDRESSES)
        imgui.table_headers_row()
        ok = imgui.ImVec4(0.45, 0.8, 0.45, 1.0)
        differ = imgui.ImVec4(0.95, 0.55, 0.35, 1.0)
        for go in sorted(readout.group_objects, key=lambda g: g.number):
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(str(go.number))
            imgui.table_next_column()
            match = set(go.group_addresses) == expected.get(go.number, set())
            text = (
                ", ".join(self._format_ga(a, names) for a in go.group_addresses)
                if go.group_addresses
                else "-"
            )
            imgui.text_colored(ok if match else differ, text)
        imgui.end_table()

    def _format_ga(self, address: int, names: dict[int, str]) -> str:
        formatted = (
            self._format_group_address(address)
            if self._format_group_address is not None
            else str(address)
        )
        name = names.get(address)
        return f"{formatted}  {name}" if name else formatted

    def _ga_names(self, readout: "BusDeviceInfo") -> dict[int, str]:
        """Group-address value -> name, cached per read-out so the table render stays cheap."""
        key = id(readout)
        if self._ga_names_cache_key != key:
            self._ga_names_cache = (
                self._get_group_address_names()
                if self._get_group_address_names is not None
                else {}
            )
            self._ga_names_cache_key = key
        return self._ga_names_cache

    def _expected_group_addresses(
        self, device: Device, readout: "BusDeviceInfo"
    ) -> dict[int, set[int]]:
        """Project's planned group addresses per object, computed once per read-out and cached (the
        lookup hits the DB, so it must not run every frame while the table scrolls)."""
        key = (device.node_id, id(readout))
        if self._expected_cache_key != key:
            self._expected_cache = (
                self._get_expected_group_addresses(device)
                if self._get_expected_group_addresses is not None
                else {}
            )
            self._expected_cache_key = key
        return self._expected_cache

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
        imgui.set_next_item_width(px(320.0))
        _, self._reset_erase_idx = imgui.combo(
            S.RESET_TYPE, self._reset_erase_idx, [label for label, _ in presets]
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
        Returns just ``[primary]`` when multi-select is not wired or the selection is a single row."""
        if (
            self._get_selected_node_ids is None
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
        """Height of the heading and pane of the help below the parameters."""
        return imgui.get_frame_height_with_spacing() + self._help_pane_height()

    def _help_pane_height(self) -> float:
        if self._help_pane_h <= 0:
            self._help_pane_h = imgui.get_text_line_height_with_spacing() * 5
        return self._help_pane_h

    def _render_help_heading(self) -> None:
        """The help heading, which resizes the help when dragged."""
        imgui.separator_text(S.CONFIGURE_HELP)
        top_left = imgui.get_item_rect_min()
        size = imgui.get_item_rect_size()
        imgui.set_cursor_screen_pos(top_left)
        imgui.invisible_button("##help_splitter", size)
        if imgui.is_item_hovered() or imgui.is_item_active():
            imgui.set_mouse_cursor(imgui.MouseCursor_.resize_ns)
        if imgui.is_item_active():
            line = imgui.get_text_line_height_with_spacing()
            height = self._help_pane_height() - imgui.get_io().mouse_delta.y
            self._help_pane_h = min(max(height, line * 2), line * 40)

    def _render_help(self, text: str, height: float) -> None:
        top = imgui.get_cursor_screen_pos().y
        self._render_help_heading()
        pane = height - (imgui.get_cursor_screen_pos().y - top)
        if imgui.begin_child("##param_help", imgui.ImVec2(0, max(pane, 1.0)), True):
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
            label = f"Procedure {i + 1}  ({len(proc.steps)} steps)##lp{i}"
            if imgui.tree_node(label):
                _table_flags = (
                    imgui.TableFlags_.borders_outer
                    | imgui.TableFlags_.borders_inner_v
                    | imgui.TableFlags_.sizing_stretch_prop
                )
                if imgui.begin_table(f"##lpt{i}", 3, _table_flags):
                    imgui.table_setup_column(
                        "Kind", imgui.TableColumnFlags_.width_stretch, 0.3
                    )
                    imgui.table_setup_column(
                        "Applies To", imgui.TableColumnFlags_.width_stretch, 0.15
                    )
                    imgui.table_setup_column(
                        "Details", imgui.TableColumnFlags_.width_stretch, 0.55
                    )
                    imgui.table_headers_row()
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

    def _render_program_confirm(self, device: Device) -> None:
        if not begin_dialog(S.PROGRAM_CONFIRM_TITLE):
            return
        scope = self._download_scope
        address = device.individual_address or "?"
        serial = (
            self._project_serial(device)
            if scope is DownloadScope.INDIVIDUAL_ADDRESS
            else None
        )
        use_serial = serial is not None and self._program_use_serial
        if use_serial:
            imgui.text_wrapped(
                S.PROGRAM_ADDRESS_SERIAL_TEXT.format(
                    address=address, serial=serial_text(serial)
                )
            )
        elif scope is DownloadScope.INDIVIDUAL_ADDRESS:
            imgui.text_wrapped(S.PROGRAM_ADDRESS_TEXT.format(address=address))
        elif scope is DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS:
            imgui.text_wrapped(S.PROGRAM_OVERWRITE_TEXT.format(address=address))
        else:
            imgui.text_wrapped(
                S.PROGRAM_CONFIRM_TEXT.format(address=address, scope=scope.name)
            )
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
        plugin_required = (
            device.download_plugin_required and not scope.individual_address_only
        )
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
        clicked = dialog_buttons(
            S.BTN_PROGRAM_DEVICE, S.BTN_CANCEL, enabled=[can_program, True]
        )
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
        changed, new_idx = imgui.combo("##download_scope", current, _scope_labels())
        if changed:
            self._download_scope = _SCOPE_ORDER[new_idx]

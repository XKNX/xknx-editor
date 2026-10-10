import threading
import webbrowser
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from imgui_bundle import imgui

from editor_gui import doc_links
from editor_gui.device import ComObject, Device
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import label_column_width, px, px_vec2
from editor_gui.widgets.filter_box import filter_box
from xknxeditor.download.scope import DownloadScope
from xknxeditor.prod.app_id import parse_app_id

if TYPE_CHECKING:
    from editor_gui.programming import DeviceOverview
    from xknxeditor.proj.core.service import DeviceInfo
from editor_gui.programming_dali import is_mdt_dali_app
from editor_gui.widgets import (
    ButtonActions,
    GroupObjectsTable,
    channel_apply_targets,
    count_parameters,
    differing_param_refs,
    render_ui_tree,
    take_selected_help,
)
from editor_gui.widgets.group_objects_widgets import (
    GroupAddressCatalog,
    GroupLinkResolver,
)
from editor_gui.widgets.icons import Icon, Picture
from editor_gui.widgets.markdown import render_markdown
from editor_gui.widgets.module_table import build_module_tables, render_module_tables

_log = structlog.get_logger("configure")


def _same_line_if_fits(label: str) -> None:
    """Put the next button beside the previous item if it fits, else below it."""
    style = imgui.get_style()
    width = imgui.calc_text_size(label).x + style.frame_padding.x * 2
    right = imgui.get_item_rect_max().x + style.item_spacing.x + width
    limit = imgui.get_window_pos().x + imgui.get_window_width() - style.window_padding.x
    if right <= limit:
        imgui.same_line()


def _device_title(device: Device) -> str:
    return " ".join(p for p in (device.individual_address, device.name) if p)


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
        on_program_device: Callable[[Device, DownloadScope], None] | None = None,
        on_eval_device: Callable[[Device, DownloadScope], None] | None = None,
        open_memory_preview: Callable[[Device], None] | None = None,
        on_param_change_all: Callable[[Device, str, str], None] | None = None,
        notify: Callable[[str], None] | None = None,
        get_online_versions: Callable[[str], list[int]] | None = None,
        on_update_application: Callable[[Device], bool] | None = None,
        on_read_device_info: Callable[[Device], None] | None = None,
        get_device_readout: "Callable[[int], DeviceOverview | None] | None" = None,
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
        self._get_online_versions = get_online_versions
        self._on_update_application = on_update_application
        self._on_read_device_info = on_read_device_info
        self._on_restart_device = on_restart_device
        self._on_master_reset_device = on_master_reset_device
        self._reset_armed = False  # confirm checkbox in the master-reset popup
        self._reset_erase_idx = 0
        self._get_device_readout = get_device_readout
        self._confirm_update_open: bool = False
        self._alert_update_open: bool = False
        self._alert_version: int = 0
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
        self._address_buffer: str = ""
        self._description_buffer: str = ""
        self._buffer_device_id: int | None = None
        self._download_scope: DownloadScope = DownloadScope.PARAMETERS
        self._confirm_program_open: bool = False
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
            S.CONFIGURE_DOWNLOAD_SCOPE,
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
        """Device selection, address and programming."""
        self._begin_details()
        device = self._current_device()
        if device is None:
            imgui.text_disabled(S.CONFIGURE_NO_DEVICES)
            return
        devices = self._get_devices()

        current_idx = 0
        labels: list[str] = []
        for i, d in enumerate(devices):
            label = (
                f"{d.name} ({d.individual_address})" if d.individual_address else d.name
            )
            labels.append(label)
            if d.node_id == device.node_id:
                current_idx = i

        imgui.set_next_item_width(-1)
        changed, new_idx = imgui.combo("##device_select", current_idx, labels)
        if changed:
            self._set_selected_device(devices[new_idx])
            device = devices[new_idx]

        imgui.separator()

        if self._buffer_device_id != device.node_id:
            self._name_buffer = device.name
            self._address_buffer = device.individual_address
            self._description_buffer = device.description
            self._buffer_device_id = device.node_id

        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.CONFIGURE_NAME)
        imgui.same_line(self._label_w)
        imgui.set_next_item_width(-1)
        _, self._name_buffer = imgui.input_text("##name", self._name_buffer)
        if imgui.is_item_deactivated_after_edit():
            self._on_name_change(device, self._name_buffer)
        if not imgui.is_item_active() and self._name_buffer != device.name:
            self._name_buffer = device.name

        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.CONFIGURE_INDIVIDUAL_ADDRESS)
        imgui.same_line(self._label_w)
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

        if self._on_program_device is not None or self._on_eval_device is not None:
            self._render_scope_selector()

        # Workflow order: dry run (preview changes) → program → preview memory image.
        if self._on_eval_device is not None:
            enabled = bool(device.individual_address)
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_EVAL_DEVICE):
                self._on_eval_device(device, self._download_scope)
            imgui.end_disabled()
            _same_line_if_fits(S.BTN_PROGRAM_DEVICE)

        if self._on_program_device is not None:
            enabled = bool(device.individual_address)
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_PROGRAM_DEVICE):
                # Programming writes to the device; confirm first.
                self._confirm_program_open = True
            imgui.end_disabled()
            if self._confirm_program_open:
                imgui.open_popup(S.PROGRAM_CONFIRM_TITLE)
                self._confirm_program_open = False
            self._render_program_confirm(device)
            if self._open_memory_preview is not None:
                _same_line_if_fits(S.BTN_PREVIEW_MEMORY)

        if self._open_memory_preview is not None and imgui.button(S.BTN_PREVIEW_MEMORY):
            self._open_memory_preview(device)

        self._render_ip_config(device)

    def render_manufacturer(self) -> None:
        """Manufacturer, product and application details of the selected device."""
        self._begin_details()
        device = self._current_device()
        if device is None:
            imgui.text_disabled(S.CONFIGURE_NO_DEVICES)
            return
        info = self._get_device_info(device.node_id)
        manufacturer = (
            info.manufacturer_name if info and info.manufacturer_name else None
        )
        # Show every field we have, always (empty -> "-"), so device details are complete
        # and every value can be copied.
        self._render_label_value(
            S.CONFIGURE_MANUFACTURER,
            manufacturer or device.app.manufacturer_id,
        )
        self._render_label_value(S.CONFIGURE_APPLICATION, device.app.id)
        app_version = self._current_app_version(device)
        self._render_label_value(
            S.CONFIGURE_APP_VERSION,
            f"V{app_version}" if app_version is not None else "-",
        )
        # device.app.version is the KNX XML schema version (e.g. "20" for the /20 schema),
        # NOT the application program version above.
        self._render_label_value(S.CONFIGURE_SCHEMA_VERSION, device.app.version)
        self._render_label_value(
            S.CONFIGURE_ORDER_NUMBER, info.order_number if info else ""
        )
        self._render_label_value(
            S.CONFIGURE_HARDWARE, info.hardware_name if info else ""
        )
        self._render_label_value(S.CONFIGURE_PRODUCT, info.product_name if info else "")
        self._render_description_field(device)
        self._render_label_value(
            S.CONFIGURE_PRODUCT_REF, info.product_ref_id if info else ""
        )
        self._render_label_value(
            S.CONFIGURE_PROGRAM_REF,
            (info.hardware2program_ref_id or "") if info else "",
        )
        self._render_manual_button(info)
        self._render_online_versions(device, info)
        self._render_device_readout(device)

        self._render_load_procedures(device)

        # Success alert after an application update, opened via a flag set in the update confirm.
        if self._alert_update_open:
            imgui.open_popup(S.UPDATE_ALERT_TITLE)
            self._alert_update_open = False
        self._render_update_alert()

    def render(self) -> None:
        device = self._current_device()
        if device is None:
            imgui.text_disabled(S.CONFIGURE_NO_DEVICES)
            return
        imgui.text(_device_title(device))
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
                        self._render_multi_fill_toggle(device)
                        _, self._apply_all_channels = imgui.checkbox(
                            S.CONFIGURE_APPLY_ALL_CHANNELS, self._apply_all_channels
                        )
                    help_text = self._help_text(device)
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
                and is_mdt_dali_app(device.app.id)
                and imgui.begin_tab_item(f"{S.EDITOR_TAB_DALI}###editor_tab_dali")[0]
            ):
                self._render_dali(device)
                imgui.end_tab_item()

            imgui.end_tab_bar()

    def _render_manual_button(self, info: "DeviceInfo | None") -> None:
        """Best-effort: find the device's manual and open it in the browser. Resolves via the KNX
        device database (parsed) and, failing that, a DuckDuckGo ``site:<manufacturer>`` search — see
        editor_gui.doc_links. The lookup does network I/O, so it runs on a daemon thread and the
        browser is opened from there; the button shows a busy state meanwhile."""
        if info is None or not (info.order_number or info.manufacturer_name):
            return
        imgui.spacing()
        if self._manual_busy:
            imgui.text_disabled(S.CONFIGURE_OPEN_MANUAL_BUSY)
            return
        if imgui.button(S.CONFIGURE_OPEN_MANUAL):
            self._manual_busy = True
            threading.Thread(
                target=self._open_manual,
                args=(info.manufacturer_name, info.order_number, info.product_name),
                daemon=True,
            ).start()
        if imgui.is_item_hovered():
            imgui.set_tooltip(S.CONFIGURE_OPEN_MANUAL_HINT)

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

    def _render_online_versions(
        self, device: Device, info: "DeviceInfo | None"
    ) -> None:
        """Show which application versions exist online for this device's order number, so the user
        can see whether a newer one is available. Read-only from the cached index; empty until the
        online catalog has been fetched. One wrapped line of version numbers (newest first), with
        the device's current version marked when it can be determined."""
        if self._get_online_versions is None:
            return
        order = info.order_number if info else ""
        if not order:
            return
        versions = self._get_online_versions(order)
        if not versions:
            return
        current = self._current_app_version(device)
        parts = [
            f"V{v} {S.CONFIGURE_ONLINE_CURRENT}" if v == current else f"V{v}"
            for v in versions
        ]
        imgui.spacing()
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(
            S.CONFIGURE_ONLINE_AVAILABLE.format(count=len(versions))
            + ": "
            + ", ".join(parts)
        )
        imgui.pop_text_wrap_pos()
        self._render_update_action(device, versions, current)

    def _current_app_version(self, device: Device) -> int | None:
        """The application program version parsed from the app id (``...-26-...`` -> 38). This is the
        real application version; ``device.app.version`` is the KNX XML schema version instead."""
        parsed = parse_app_id(device.app.id)
        return parsed.version if parsed is not None else None

    def _render_device_readout(self, device: Device) -> None:
        """Read-only "read from device" action: pull the actual programmed state off the bus (mask,
        application version, serial/order/hardware) and show it next to the project's planned state."""
        if self._on_read_device_info is None:
            return
        imgui.spacing()
        enabled = bool(device.individual_address)
        imgui.begin_disabled(not enabled)
        if imgui.button(S.BTN_READ_DEVICE_INFO):
            self._on_read_device_info(device)
        imgui.end_disabled()
        if not enabled and imgui.is_item_hovered(
            imgui.HoveredFlags_.allow_when_disabled
        ):
            imgui.set_tooltip(S.READOUT_NEEDS_ADDRESS)
        if self._on_restart_device is not None:
            imgui.same_line()
            imgui.begin_disabled(not enabled)
            if imgui.button(S.BTN_RESTART_DEVICE):
                self._on_restart_device(device)
            imgui.end_disabled()
        if self._on_master_reset_device is not None:
            imgui.same_line()
            self._render_master_reset(device, enabled)

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
        self._render_label_value(S.CONFIGURE_MANUFACTURER, readout.manufacturer or "")
        # Compare the version read off the device to the project's version. Only flag a mismatch
        # (device behind/ahead of the project -> programming needed); a match needs no marker.
        app_v = readout.application_version
        project_v = self._current_app_version(device)
        if app_v is None:
            value = "-"
        elif project_v is None or app_v == project_v:
            value = f"V{app_v}"
        else:
            value = f"V{app_v}  {S.READOUT_VERSION_MISMATCH.format(version=project_v)}"
        self._render_label_value(S.CONFIGURE_APP_VERSION, value)
        self._render_label_value(S.READOUT_MASK, mask)
        self._render_label_value(S.READOUT_SERIAL, readout.serial_number or "")
        self._render_label_value(S.READOUT_ORDER, readout.order_info or "")
        self._render_label_value(S.READOUT_HARDWARE, readout.hardware_type or "")
        self._render_diagnosis(readout)

    def _render_diagnosis(self, readout: "DeviceOverview") -> None:
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
                    imgui.ImVec4(0.95, 0.55, 0.35, 1.0), readout.error_text or ""
                )
        if readout.programming_mode:
            imgui.text_colored(imgui.ImVec4(0.95, 0.75, 0.35, 1.0), S.READOUT_PROG_MODE)

    def _render_master_reset(self, device: Device, enabled: bool) -> None:
        """Destructive master-reset action: a button opening a confirm popup with an erase-type
        picker; the reset only fires after an explicit acknowledge checkbox + button."""
        if self._on_master_reset_device is None:
            return
        imgui.begin_disabled(not enabled)
        if imgui.button(S.BTN_RESET_DEVICE):
            self._reset_armed = False
            self._reset_erase_idx = 0
            imgui.open_popup("##masterreset")
        imgui.end_disabled()
        if not imgui.begin_popup("##masterreset"):
            return
        imgui.text_disabled(S.RESET_POPUP_TITLE)
        imgui.push_text_wrap_pos(px(360.0))
        imgui.text_colored(imgui.ImVec4(0.95, 0.55, 0.35, 1.0), S.RESET_WARNING)
        imgui.pop_text_wrap_pos()
        presets = _reset_presets()
        imgui.set_next_item_width(px(320.0))
        _, self._reset_erase_idx = imgui.combo(
            S.RESET_TYPE, self._reset_erase_idx, [label for label, _ in presets]
        )
        _, self._reset_armed = imgui.checkbox(S.RESET_CONFIRM, self._reset_armed)
        imgui.begin_disabled(not self._reset_armed)
        if imgui.button(S.RESET_EXECUTE):
            _, code = presets[self._reset_erase_idx]
            self._on_master_reset_device(device, code)
            imgui.close_current_popup()
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button(f"{S.ML_CANCEL}##reset_cancel"):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_update_action(
        self, device: Device, versions: list[int], current: int | None
    ) -> None:
        """When a newer application version is available online, offer an update that keeps
        parameters and links. Confirmed first, because it repoints the device's application."""
        if self._on_update_application is None or current is None or not versions:
            return
        newest = versions[0]  # get_online_versions is sorted descending
        if newest <= current:
            return
        if imgui.button(S.CONFIGURE_UPDATE_BUTTON.format(version=newest)):
            self._confirm_update_open = True
        if self._confirm_update_open:
            imgui.open_popup(S.UPDATE_CONFIRM_TITLE)
            self._confirm_update_open = False
        self._render_update_confirm(device, newest)

    def _render_update_confirm(self, device: Device, version: int) -> None:
        if not imgui.begin_popup_modal(
            S.UPDATE_CONFIRM_TITLE, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text_wrapped(S.UPDATE_CONFIRM_TEXT.format(version=version))
        imgui.spacing()
        btn_w = px_vec2(180, 0)
        if imgui.button(S.CONFIGURE_UPDATE_BUTTON.format(version=version), btn_w):
            ok = (
                bool(self._on_update_application(device))
                if self._on_update_application is not None
                else False
            )
            if ok:
                self._alert_version = version
                self._alert_update_open = True
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_update_alert(self) -> None:
        if not imgui.begin_popup_modal(
            S.UPDATE_ALERT_TITLE, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text_wrapped(S.UPDATE_ALERT_TEXT.format(version=self._alert_version))
        imgui.spacing()
        if imgui.button(S.BTN_OK, px_vec2(120, 0)):
            imgui.close_current_popup()
        imgui.end_popup()

    def _matching_count(self, device: Device) -> int:
        """Number of devices (incl. this one) running the same application program."""
        app_id = getattr(device.app, "id", None)
        if app_id is None:
            return 1
        return sum(
            1 for d in self._get_devices() if getattr(d.app, "id", None) == app_id
        )

    def _render_multi_fill_toggle(self, device: Device) -> None:
        """Multi-fill toggle: apply parameter edits to all identical devices at once."""
        if self._on_param_change_all is None:
            return
        count = self._matching_count(device)
        if count <= 1:
            self._apply_all = False
            return  # nothing to fill into
        _, self._apply_all = imgui.checkbox(
            S.CONFIGURE_APPLY_ALL.format(count=count), self._apply_all
        )

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

    def _render_description_field(self, device: Device) -> None:
        """Editable device description (ETS "Description"/comment). Follows the same commit-on-blur
        pattern as the Name field: push through the callback when editing ends, and keep the buffer
        in sync with the live device while the field is not focused."""
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.CONFIGURE_DESCRIPTION)
        imgui.same_line(self._label_w)
        imgui.set_next_item_width(-1)
        _, self._description_buffer = imgui.input_text(
            "##description", self._description_buffer
        )
        if imgui.is_item_deactivated_after_edit():
            self._on_description_change(device, self._description_buffer)
        if (
            not imgui.is_item_active()
            and self._description_buffer != device.description
        ):
            self._description_buffer = device.description

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
        self._render_label_value(S.CONFIGURE_IP_ASSIGN, cfg.get("Assign", ""))
        self._render_label_value(S.CONFIGURE_IP_ADDRESS, cfg.get("IPAddress", ""))
        self._render_label_value(S.CONFIGURE_IP_SUBNET, cfg.get("SubnetMask", ""))
        self._render_label_value(S.CONFIGURE_IP_GATEWAY, cfg.get("DefaultGateway", ""))
        self._render_label_value(S.CONFIGURE_IP_MAC, cfg.get("MACAddress", ""))

    def _render_label_value(self, label: str, value: str) -> None:
        imgui.text_disabled(label)
        imgui.same_line(self._label_w)
        if not value:
            imgui.text_disabled("-")
            return
        # The value is a full-width click target: click anywhere on it to copy, with a short
        # "Copied" toast. A per-frame sequence makes the id unique even when the same label+value
        # appears in two blocks (e.g. "Applikationsversion V38" in the catalog and live sections),
        # which would otherwise trigger imgui's conflicting-ID warning.
        self._lv_seq += 1
        if imgui.selectable(f"{value}##copy_{self._lv_seq}", False)[0]:
            imgui.set_clipboard_text(value)
            if self._notify is not None:
                self._notify(S.CONFIGURE_COPIED)
        if imgui.is_item_hovered():
            imgui.set_tooltip(S.CONFIGURE_COPY_HINT)

    def _render_program_confirm(self, device: Device) -> None:
        if not imgui.begin_popup_modal(
            S.PROGRAM_CONFIRM_TITLE, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text_wrapped(
            S.PROGRAM_CONFIRM_TEXT.format(
                address=device.individual_address or "?",
                scope=self._download_scope.name,
            )
        )
        imgui.spacing()
        btn_w = px_vec2(140, 0)
        if imgui.button(S.BTN_PROGRAM_DEVICE, btn_w):
            if self._on_program_device is not None:
                self._on_program_device(device, self._download_scope)
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_scope_selector(self) -> None:
        """Select what a download/eval covers: full, parameters, or group comm."""
        order = [
            DownloadScope.FULL,
            DownloadScope.PARAMETERS,
            DownloadScope.GROUP_COMMUNICATION,
            DownloadScope.APPLICATION,
            DownloadScope.UNLOAD,
            DownloadScope.UNLOAD_ALL,
        ]
        labels = [
            S.SCOPE_FULL,
            S.SCOPE_PARAMETERS,
            S.SCOPE_GROUP_COMMUNICATION,
            S.SCOPE_APPLICATION,
            S.SCOPE_UNLOAD,
            S.SCOPE_UNLOAD_ALL,
        ]
        current = order.index(self._download_scope)
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.CONFIGURE_DOWNLOAD_SCOPE)
        imgui.same_line(self._label_w)
        imgui.set_next_item_width(px(220.0))
        changed, new_idx = imgui.combo("##download_scope", current, labels)
        if changed:
            self._download_scope = order[new_idx]

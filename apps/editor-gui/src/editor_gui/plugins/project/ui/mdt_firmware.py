"""Firmware readout shared by desktop and browser clients."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.local_time import stamp_text
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tree import collapsing_header
from xknxeditor.vendor.mdt.firmware import MAX_ARCHIVE_SIZE

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.program_queue import QueueItem
    from editor_gui.plugins.project.service import ProjectService


class MdtFirmwarePanel:
    def __init__(self, queue: Callable[..., Any]) -> None:
        self._queue = queue
        self._generation = -1
        self._operations: dict[int, QueueItem] = {}
        self._open: file_dialogs.OpenFileDialog | None = None
        self._import_target: tuple[int, int] | None = None
        self._error = ""

    def _poll(self, project: ProjectService) -> None:
        if self._generation != project.generation:
            self._operations.clear()
            self._error = ""
            self._generation = project.generation
        if self._open is not None and self._open.ready():
            paths = self._open.result()
            self._open = None
            if paths:
                try:
                    if self._import_target != (project.generation, project.revision):
                        raise ValueError(S.MDT_IMPORT_STALE)
                    path = Path(paths[0])
                    with path.open("rb") as stream:
                        data = stream.read(MAX_ARCHIVE_SIZE + 1)
                    project.import_mdt_firmware_catalog(data, path.name)
                    self._error = ""
                except (OSError, ValueError) as exc:
                    self._error = str(exc)

    def render(
        self, device: Device, project: ProjectService, *, default_open: bool
    ) -> None:
        self._poll(project)
        if not project.has_mdt_firmware(device):
            return
        imgui.push_id(f"mdt_firmware_{project.generation}_{device.node_id}")
        try:
            if collapsing_header(
                S.CONFIGURE_FIRMWARE + "###mdt_firmware",
                imgui.TreeNodeFlags_.default_open if default_open else 0,
            ):
                self._render(device, project)
        finally:
            imgui.pop_id()

    def _render(self, device: Device, project: ProjectService) -> None:
        operation = self._operations.get(device.node_id)
        pending = (
            operation is not None
            and operation.ended is not None
            and not operation.ended.done()
        )
        imgui.begin_disabled(pending or not device.individual_address)
        if imgui.button(S.MDT_FIRMWARE_READ):
            item = self._queue(device.node_id, "mdt_firmware")
            if item is not None:
                self._operations[device.node_id] = item
        imgui.end_disabled()
        same_line_if_fits(button_width(S.MDT_FIRMWARE_IMPORT))
        imgui.begin_disabled(self._open is not None)
        if imgui.button(S.MDT_FIRMWARE_IMPORT):
            self._import_target = (project.generation, project.revision)
            self._open = file_dialogs.open_file(
                title=S.MDT_FIRMWARE_IMPORT,
                filters=[S.CONFIGURE_FIRMWARE, "*.etsapp *.zip"],
            )
        imgui.end_disabled()
        if operation is not None and not pending and operation.message:
            imgui.text_wrapped(operation.message)
        try:
            view = project.mdt_firmware(device.node_id)
            for label, value in (
                (S.CONFIGURE_ORDER_NUMBER, view["product"]),
                (S.MDT_FIRMWARE_INSTALLED, view["installed_version"]),
                (
                    S.MDT_FIRMWARE_COMPATIBLE,
                    view["latest_compatible_version"] or S.MDT_FIRMWARE_UNDETERMINED,
                ),
                (S.MDT_FIRMWARE_READ_AT, stamp_text(view["read_at"])),
            ):
                imgui.text_wrapped(f"{label}: {value or '-'}")
            imgui.text_wrapped(S.MDT_FIRMWARE_STATUS[view["compatibility_status"]])
            self._render_info(view)
            imgui.text_wrapped(
                S.MDT_FIRMWARE_CATALOG.format(
                    source=view["catalog_source"],
                    date=stamp_text(view["catalog_latest_modified"]),
                )
            )
            if view["identity_error"]:
                imgui.text_wrapped(view["identity_error"])
        except ValueError as exc:
            imgui.text_wrapped(str(exc))
        imgui.text_wrapped(S.MDT_FIRMWARE_HELP)
        if self._error:
            imgui.text_wrapped(self._error)

    @staticmethod
    def _render_info(view: dict[str, Any]) -> None:
        labels = S.MDT_FIRMWARE_FIELDS
        for label, value in (
            (labels["hardware_key"], view["hardware_key"]),
            (S.DEVICE_INFO_HARDWARE, view["hardware_type_hex"]),
        ):
            imgui.text_wrapped(f"{label}: {value or S.MDT_FIRMWARE_UNDETERMINED}")
        bootloader = view["bootloader"]
        if bootloader is not None:
            imgui.text_wrapped(
                f"{labels['version_code']}: 0x{bootloader['version_code']:04X}"
            )
            imgui.text_wrapped(
                f"{labels['max_telegram_length']}: {bootloader['max_telegram_length']}"
            )
        for release in view["compatible_releases"]:
            if release["version"] == view["latest_compatible_version"]:
                imgui.text_wrapped(f"{labels['filename']}: {release['filename']}")
                imgui.text_wrapped(
                    f"{labels['modified']}: {stamp_text(release['modified'])}"
                )
        if not view["read_at"] or not collapsing_header(
            S.PLUGIN_PARAMS_COL_DETAILS + "###firmware_details"
        ):
            return
        if bootloader is not None:
            for field in ("crc_type_code", "encryption_type_code"):
                imgui.text_wrapped(f"{labels[field]}: 0x{bootloader[field]:02X}")
            imgui.text_wrapped(S.MDT_FIRMWARE_CODES_HELP)
        for label, value in (
            (S.READOUT_ORDER, view["order_info_hex"]),
            (labels["version_property_hex"], view["version_property_hex"]),
            (labels["bsl_info_hex"], view["bsl_info_hex"]),
        ):
            if value is not None:
                imgui.text_wrapped(f"{label}: {value}")

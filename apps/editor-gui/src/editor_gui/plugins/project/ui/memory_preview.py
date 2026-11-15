from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from editor_gui.memory_read import PreviewSegment
    from xknxeditor.download.prepared import PreparedDownload

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.device import Device, device_label
from editor_gui.memory_read import MemoryReadResult, preview_segments
from editor_gui.plugins.project.program_queue import OperationState, QueueItem
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets import HexView
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_tool_window
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tabs import begin_tab_bar
from xknxeditor.download.errors import ImageError
from xknxeditor.prod.errors import EncodingError


class MemoryPreviewWindow:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        prepared_image: Callable[[Device], PreparedDownload | None] | None = None,
        read_memory: Callable[[int, str], QueueItem] | None = None,
        generation: Callable[[], int] = lambda: 0,
    ) -> None:
        self._prepared_image = prepared_image
        self._get_devices = get_devices
        self._read_memory = read_memory
        self._generation = generation
        self._owner: tuple[int, int, str, str | None] | None = None
        self._read_item: QueueItem | None = None
        self._read_data: dict[str, MemoryReadResult] = {}
        self._show_device_memory = True
        self._device: Device | None = None
        self._show: bool = False
        self._segments: dict[str, bytes] = {}
        self._base_addrs: dict[str, int] = {}
        self._param_maps: dict[str, dict[int, tuple[str, str]]] = {}
        self._hex_views: dict[str, HexView] = {}
        self._ref_data: dict[str, bytes] = {}
        self._encode_error: str | None = None
        # File dialogs and the segment each one is for.
        self._save_dialog: file_dialogs.SaveFileDialog | None = None
        self._save_seg_id: str | None = None
        self._ref_dialog: file_dialogs.OpenFileDialog | None = None
        self._ref_seg_id: str | None = None
        self._ref_owner: tuple[int, int, str, str | None] | None = None
        self._file_error: str | None = None
        self._save_data: bytes = b""

    def open(self, device: Device) -> None:
        self._device = device
        self._sync_device(device)
        self._show = True

    def _sync_device(self, device: Device) -> None:
        owner = (
            self._generation(),
            device.node_id,
            device.individual_address,
            device.app.id if device.app else None,
        )
        if owner != self._owner:
            self._owner = owner
            self._read_item = None
            self._read_data.clear()
            self._ref_data.clear()
            self._hex_views.clear()
            self._file_error = None

    def render(self) -> None:
        devices = self._get_devices()
        if not devices:
            self._owner = None
            self._poll_dialogs()
            return
        selected_id = self._device.node_id if self._device is not None else None
        self._device = next(
            (device for device in devices if device.node_id == selected_id), devices[0]
        )

        device = self._device
        self._sync_device(device)
        self._poll_dialogs()
        if not self._show:
            return
        # A parameter that cannot be encoded raises rather than emitting a partial image
        # (see EncodingError). This preview runs every frame, so degrade to an inline
        # message instead of letting the exception tear down the render loop.
        try:
            prepared = self._prepared_image(device) if self._prepared_image else None
            segments = preview_segments(device, prepared)
            self._segments = {key: segment.data for key, segment in segments.items()}
            self._base_addrs = {
                key: segment.request.address for key, segment in segments.items()
            }
            for key, result in tuple(self._read_data.items()):
                if key not in segments or segments[key].request != result.request:
                    self._read_data.pop(key)
                    self._ref_data.pop(key, None)
            self._poll_read(segments)
            self._encode_error = None
        except (EncodingError, ImageError, ValueError) as exc:
            self._segments = {}
            self._encode_error = str(exc)
        self._param_maps = device.get_memory_param_map()

        opened, p_open = begin_tool_window(
            S.CONFIGURE_MEMORY_PREVIEW, self._show, width=760, height=540
        )
        if p_open is not None:
            self._show = p_open
        if opened:
            self._render_device_selector(devices)
            if self._device is not device:
                imgui.end()
                return
            imgui.separator()
            if self._file_error is not None:
                imgui.text_colored(status_color("error"), self._file_error)
            segments = list(self._segments.items())
            if self._encode_error is not None:
                imgui.text_disabled(
                    S.MEMORY_ENCODE_FAILED.format(error=self._encode_error)
                )
            elif not segments:
                imgui.text_disabled(S.MEMORY_NO_SEGMENTS)
            elif begin_tab_bar("##segs"):
                for i, (seg_id, data) in enumerate(segments):
                    label = f"{seg_id} ({len(data)}B)##seg{i}"
                    if imgui.begin_tab_item(label)[0]:
                        self._render_seg_toolbar(seg_id, data, i)
                        imgui.separator()
                        view = self._hex_views.setdefault(seg_id, HexView())
                        read = self._read_data.get(seg_id)
                        actual = read is not None and self._show_device_memory
                        view.draw(
                            read.data if actual and read is not None else data,
                            read.address
                            if read is not None
                            else self._base_addrs.get(seg_id, 0),
                            self._param_maps.get(seg_id),
                            data if actual else self._ref_data.get(seg_id),
                        )
                        imgui.end_tab_item()
                imgui.end_tab_bar()
        imgui.end()

    def _render_device_selector(self, devices: list[Device]) -> None:
        current_idx = 0
        labels: list[str] = []
        for i, d in enumerate(devices):
            labels.append(device_label(d))
            if self._device is not None and d.node_id == self._device.node_id:
                current_idx = i
        imgui.set_next_item_width(-1)
        changed, new_idx = combo("##mp_device", current_idx, labels)
        if changed:
            self._device = devices[new_idx]
            self._segments = {}
            self._hex_views = {}
            self._ref_data = {}
            self._sync_device(self._device)

    def _render_seg_toolbar(self, seg_id: str, data: bytes, idx: int) -> None:
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.MEMORY_BYTES(len(data)).format(count=len(data)))
        if self._read_memory is not None:
            same_line_if_fits(button_width(S.MEMORY_READ))
            enabled = (
                self._device is not None
                and bool(self._device.individual_address)
                and self._read_item is None
            )
            imgui.begin_disabled(not enabled)
            if imgui.button(f"{S.MEMORY_READ}##rd{idx}") and enabled:
                assert self._device is not None
                try:
                    self._read_item = self._read_memory(self._device.node_id, seg_id)
                    self._file_error = None
                except (ValueError, RuntimeError) as exc:
                    self._file_error = str(exc)
            imgui.end_disabled()
            imgui.set_item_tooltip(S.MEMORY_READ_HINT)
        same_line_if_fits(button_width(S.MEMORY_SAVE))
        idle = self._save_dialog is None and self._ref_dialog is None
        imgui.begin_disabled(not idle)
        if imgui.button(f"{S.MEMORY_SAVE}##sv{idx}"):
            self._save_seg_id = seg_id
            self._save_data = (
                self._read_data[seg_id].data
                if self._show_device_memory and seg_id in self._read_data
                else data
            )
            self._file_error = None
            self._save_dialog = file_dialogs.save_file(
                S.MEMORY_SAVE_DUMP_TITLE,
                self._dump_name(seg_id),
                [S.MEMORY_FILE_FILTER, "*.bin", S.MEMORY_ALL_FILES, "*"],
            )
        same_line_if_fits(
            button_width(
                S.MEMORY_CLEAR_DIFF if seg_id in self._ref_data else S.MEMORY_DIFF
            )
        )
        if seg_id in self._ref_data:
            if imgui.button(f"{S.MEMORY_CLEAR_DIFF}##df{idx}"):
                del self._ref_data[seg_id]
                self._read_data.pop(seg_id, None)
        elif imgui.button(f"{S.MEMORY_DIFF}##df{idx}"):
            self._ref_seg_id = seg_id
            self._ref_owner = self._owner
            self._file_error = None
            self._ref_dialog = file_dialogs.open_file(
                S.MEMORY_LOAD_REF_TITLE,
                "",
                [S.MEMORY_FILE_FILTER, "*.bin", S.MEMORY_ALL_FILES, "*"],
            )
        imgui.end_disabled()
        if seg_id in self._read_data:
            _, self._show_device_memory = checkbox(
                S.MEMORY_SHOW_DEVICE, self._show_device_memory
            )
        if self._read_item is not None:
            from editor_gui.plugins.project.ui.program_queue import state_text

            imgui.text_disabled(state_text(self._read_item))

    def _poll_read(self, segments: dict[str, PreviewSegment]) -> None:
        item = self._read_item
        if item is None or item.ended is None or not item.ended.done():
            return
        self._read_item = None
        if item.state is not OperationState.FINISHED:
            self._file_error = item.message or S.OPERATION_CANCELED
            return
        result = item.result
        if isinstance(result, MemoryReadResult):
            key = result.request.segment
            if key in segments and segments[key].request == result.request:
                self._read_data[key] = result
                self._ref_data[key] = result.data
                self._show_device_memory = True
            else:
                self._file_error = S.MEMORY_LAYOUT_CHANGED

    def _dump_name(self, seg_id: str) -> str:
        """Default file name from device address and segment, e.g. ``1.1.5-RS-04-00000.bin``."""
        address = self._device.individual_address if self._device is not None else ""
        name = "-".join(part for part in (address, seg_id) if part) or "memory"
        return re.sub(r'[<>:"/\\|?*\s]+', "_", name) + ".bin"

    def _poll_dialogs(self) -> None:
        if self._save_dialog is not None and self._save_dialog.ready():
            result = self._save_dialog.result()
            self._save_dialog = None
            seg_id = self._save_seg_id
            if result and seg_id is not None:
                path = Path(result)
                if not path.suffix:
                    path = path.with_suffix(".bin")
                try:
                    path.write_bytes(self._save_data)
                except OSError as exc:
                    self._file_error = S.MEMORY_FILE_FAILED.format(error=exc)
        if self._ref_dialog is not None and self._ref_dialog.ready():
            result = self._ref_dialog.result()
            self._ref_dialog = None
            seg_id = self._ref_seg_id
            if result and seg_id is not None and self._ref_owner == self._owner:
                try:
                    self._ref_data[seg_id] = Path(result[0]).read_bytes()
                except OSError as exc:
                    self._file_error = S.MEMORY_FILE_FAILED.format(error=exc)

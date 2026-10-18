import re
from collections.abc import Callable
from pathlib import Path

from imgui_bundle import imgui
from imgui_bundle import portable_file_dialogs as pfd

from editor_gui.device import Device, device_label
from editor_gui.plugins.project.strings import S
from editor_gui.widgets import HexView
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_tool_window
from xknxeditor.prod.errors import EncodingError

_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)


class MemoryPreviewWindow:
    def __init__(self, get_devices: Callable[[], list[Device]]) -> None:
        self._get_devices = get_devices
        self._device: Device | None = None
        self._show: bool = False
        self._segments: dict[str, bytes] = {}
        self._base_addrs: dict[str, int] = {}
        self._param_maps: dict[str, dict[int, tuple[str, str]]] = {}
        self._hex_views: dict[str, HexView] = {}
        self._ref_data: dict[str, bytes] = {}
        self._encode_error: str | None = None
        # Native file dialogs (non-blocking) and the segment each one is for.
        self._save_dialog: pfd.save_file | None = None
        self._save_seg_id: str | None = None
        self._ref_dialog: pfd.open_file | None = None
        self._ref_seg_id: str | None = None
        self._file_error: str | None = None

    def open(self, device: Device) -> None:
        self._device = device
        self._show = True

    def render(self) -> None:
        self._poll_dialogs()
        if not self._show:
            return
        devices = self._get_devices()
        if not devices:
            return
        if self._device is None or self._device not in devices:
            self._device = devices[0]

        device = self._device
        # A parameter that cannot be encoded raises rather than emitting a partial image
        # (see EncodingError). This preview runs every frame, so degrade to an inline
        # message instead of letting the exception tear down the render loop.
        try:
            self._segments = device.encode_to_memory()
            self._encode_error = None
        except EncodingError as exc:
            self._segments = {}
            self._encode_error = str(exc)
        self._base_addrs = device.get_segment_base_addrs()
        self._param_maps = device.get_memory_param_map()

        opened, p_open = begin_tool_window(
            S.CONFIGURE_MEMORY_PREVIEW, self._show, width=760, height=540
        )
        if p_open is not None:
            self._show = p_open
        if opened:
            self._render_device_selector(devices)
            imgui.separator()
            if self._file_error is not None:
                imgui.text_colored(_ERROR_COLOR, self._file_error)
            segments = list(self._segments.items())
            if self._encode_error is not None:
                imgui.text_disabled(
                    S.MEMORY_ENCODE_FAILED.format(error=self._encode_error)
                )
            elif not segments:
                imgui.text_disabled(S.MEMORY_NO_SEGMENTS)
            elif imgui.begin_tab_bar("##segs"):
                for i, (seg_id, data) in enumerate(segments):
                    label = f"{seg_id} ({len(data)}B)##seg{i}"
                    if imgui.begin_tab_item(label)[0]:
                        self._render_seg_toolbar(seg_id, data, i)
                        imgui.separator()
                        view = self._hex_views.setdefault(seg_id, HexView())
                        view.draw(
                            data,
                            self._base_addrs.get(seg_id, 0),
                            self._param_maps.get(seg_id),
                            self._ref_data.get(seg_id),
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

    def _render_seg_toolbar(self, seg_id: str, data: bytes, idx: int) -> None:
        imgui.text_disabled(S.MEMORY_BYTES.format(count=len(data)))
        imgui.same_line()
        idle = self._save_dialog is None and self._ref_dialog is None
        imgui.begin_disabled(not idle)
        if imgui.small_button(f"{S.MEMORY_SAVE}##sv{idx}"):
            self._save_seg_id = seg_id
            self._file_error = None
            self._save_dialog = pfd.save_file(
                S.MEMORY_SAVE_DUMP_TITLE,
                self._dump_name(seg_id),
                [S.MEMORY_FILE_FILTER, "*.bin", S.MEMORY_ALL_FILES, "*"],
            )
        imgui.same_line()
        if seg_id in self._ref_data:
            if imgui.small_button(f"{S.MEMORY_CLEAR_DIFF}##df{idx}"):
                del self._ref_data[seg_id]
        elif imgui.small_button(f"{S.MEMORY_DIFF}##df{idx}"):
            self._ref_seg_id = seg_id
            self._file_error = None
            self._ref_dialog = pfd.open_file(
                S.MEMORY_LOAD_REF_TITLE,
                "",
                [S.MEMORY_FILE_FILTER, "*.bin", S.MEMORY_ALL_FILES, "*"],
            )
        imgui.end_disabled()

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
            if result and seg_id is not None and seg_id in self._segments:
                path = Path(result)
                if not path.suffix:
                    path = path.with_suffix(".bin")
                try:
                    path.write_bytes(self._segments[seg_id])
                except OSError as exc:
                    self._file_error = S.MEMORY_FILE_FAILED.format(error=exc)
        if self._ref_dialog is not None and self._ref_dialog.ready():
            result = self._ref_dialog.result()
            self._ref_dialog = None
            seg_id = self._ref_seg_id
            if result and seg_id is not None:
                try:
                    self._ref_data[seg_id] = Path(result[0]).read_bytes()
                except OSError as exc:
                    self._file_error = S.MEMORY_FILE_FAILED.format(error=exc)

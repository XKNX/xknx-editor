import contextlib
from collections.abc import Callable
from pathlib import Path

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugins.project.strings import S
from editor_gui.widgets import HexView
from editor_gui.widgets.dpi import px, px_vec2
from xknxeditor.prod.errors import EncodingError


class MemoryPreviewWindow:
    def __init__(self, get_devices: Callable[[], list[Device]]) -> None:
        self._get_devices = get_devices
        self._device: Device | None = None
        self._show: bool = False
        self._segments: dict[str, bytes] = {}
        self._base_addrs: dict[str, int] = {}
        self._param_maps: dict[str, dict[int, tuple[str, str]]] = {}
        self._hex_views: dict[str, HexView] = {}
        self._save_path_buf: str = "dump.bin"
        self._save_seg_id: str | None = None
        self._ref_data: dict[str, bytes] = {}
        self._ref_path_buf: str = ""
        self._ref_seg_id: str | None = None
        self._encode_error: str | None = None

    def open(self, device: Device) -> None:
        self._device = device
        self._show = True

    def render(self) -> None:
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

        # Dock the preview to the right edge of the work area on first open (anchor its top-right).
        vp = imgui.get_main_viewport()
        pos = getattr(vp, "work_pos", None) or getattr(vp, "pos", None)
        size = getattr(vp, "work_size", None) or getattr(vp, "size", None)
        if pos is not None and size is not None:
            imgui.set_next_window_pos(
                imgui.ImVec2(pos.x + size.x - px(16.0), pos.y + px(48.0)),
                imgui.Cond_.appearing,
                imgui.ImVec2(1.0, 0.0),
            )
        imgui.set_next_window_size(px_vec2(760, 540), imgui.Cond_.first_use_ever)
        opened, p_open = imgui.begin(S.CONFIGURE_MEMORY_PREVIEW, self._show)
        if p_open is not None:
            self._show = p_open
        if opened:
            self._render_device_selector(devices)
            imgui.separator()
            self._render_save_modal()
            self._render_load_ref_modal()
            segments = list(self._segments.items())
            if self._encode_error is not None:
                imgui.text_disabled(f"Cannot encode memory image: {self._encode_error}")
            elif not segments:
                imgui.text_disabled("No memory segments.")
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
            label = (
                f"{d.name} ({d.individual_address})" if d.individual_address else d.name
            )
            labels.append(label)
            if self._device is not None and d.node_id == self._device.node_id:
                current_idx = i
        imgui.set_next_item_width(-1)
        changed, new_idx = imgui.combo("##mp_device", current_idx, labels)
        if changed:
            self._device = devices[new_idx]
            self._segments = {}
            self._hex_views = {}
            self._ref_data = {}

    def _render_seg_toolbar(self, seg_id: str, data: bytes, idx: int) -> None:
        imgui.text_disabled(f"{len(data)} bytes")
        imgui.same_line()
        if imgui.small_button(f"Save...##sv{idx}"):
            self._save_seg_id = seg_id
            imgui.open_popup("##savedump")
        imgui.same_line()
        has_ref = seg_id in self._ref_data
        diff_label = f"Diff (clear)##df{idx}" if has_ref else f"Diff...##df{idx}"
        if imgui.small_button(diff_label):
            if has_ref:
                del self._ref_data[seg_id]
            else:
                self._ref_seg_id = seg_id
                self._ref_path_buf = ""
                imgui.open_popup("##loadref")

    def _render_load_ref_modal(self) -> None:
        imgui.set_next_window_size(px_vec2(500, 0), imgui.Cond_.always)
        if imgui.begin_popup_modal(
            "##loadref",
            None,
            imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.always_auto_resize,
        )[0]:
            imgui.text("Reference file path:")
            imgui.set_next_item_width(-1)
            _, self._ref_path_buf = imgui.input_text("##rp", self._ref_path_buf)
            imgui.spacing()
            btn_w = px_vec2(120, 0)
            if imgui.button("Load", btn_w):
                seg_id = self._ref_seg_id
                if seg_id is not None:
                    with contextlib.suppress(OSError):
                        self._ref_data[seg_id] = Path(self._ref_path_buf).read_bytes()
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Cancel", btn_w):
                imgui.close_current_popup()
            imgui.end_popup()

    def _render_save_modal(self) -> None:
        imgui.set_next_window_size(px_vec2(500, 0), imgui.Cond_.always)
        if imgui.begin_popup_modal(
            "##savedump",
            None,
            imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.always_auto_resize,
        )[0]:
            imgui.text("Save path:")
            imgui.set_next_item_width(-1)
            _, self._save_path_buf = imgui.input_text("##sp", self._save_path_buf)
            imgui.spacing()
            btn_w = px_vec2(120, 0)
            if imgui.button("Save", btn_w):
                seg_id = self._save_seg_id
                if seg_id is not None and seg_id in self._segments:
                    with contextlib.suppress(OSError):
                        Path(self._save_path_buf).write_bytes(self._segments[seg_id])
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Cancel", btn_w):
                imgui.close_current_popup()
            imgui.end_popup()

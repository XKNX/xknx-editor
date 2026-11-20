"""Read the cached DALI inventory of classic Siemens gateways."""

from __future__ import annotations

import functools
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugin_blob_readers.siemens_dali.layout import line_count
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dpi import px
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from xknxeditor.dali.connection import Programmer
from xknxeditor.dali.siemens import (
    DaliDeviceType,
    SiemensN141BusState,
    object_index_for_line,
    scan_n141_03,
)

# (device, operation, its name in the Operations panel, called once it has ended); the runner
# queues the operation. It invokes ``op`` with a raw Programmer because Siemens N141/03 has no
# vendor-specific commissioner wrapper.
SiemensDaliRunner = Callable[
    [Device, Callable[[Programmer], Awaitable[object]], str, Callable[[], None]], None
]


@dataclass
class _DeviceUi:
    """Per-device panel state (keyed by individual address)."""

    scan: SiemensN141BusState | None = None
    line: int = 0
    busy: str = ""
    # Counts the operations started, so the end of an older one does not free a newer one.
    run: int = 0
    error: str = ""


_DEVICE_TYPE_LABEL: dict[int, str] = {
    DaliDeviceType.ECG: "ECG",
    DaliDeviceType.INVERTER: "Inverter",
    DaliDeviceType.DISCHARGE: "Discharge",
    DaliDeviceType.LV_HALO: "LV Halo",
    DaliDeviceType.DIMMABLE_LAMP: "Dimmable Lamp",
    DaliDeviceType.CONVERTER_10V: "10 V Converter",
    DaliDeviceType.LED_MODULE: "LED",
    DaliDeviceType.SWITCHING_FUNCTION: "Switch",
    DaliDeviceType.COLOUR_CONTROL: "Colour Control",
    DaliDeviceType.SEQUENCER: "Sequencer",
}


def _device_type_label(code: int) -> str:
    try:
        return _DEVICE_TYPE_LABEL.get(DaliDeviceType(code), f"0x{code:02X}")
    except ValueError:
        return "-" if code in (-1, 0xFF) else f"0x{code:02X}"


@dataclass
class SiemensDaliPanel:
    """Stateful "DALI (Siemens)" tab body. One instance owned by the project plugin."""

    _run: SiemensDaliRunner
    _generation: Callable[[], int] = lambda: 0
    _project_generation: int | None = None
    _ui: dict[tuple[int, str], _DeviceUi] = field(
        default_factory=dict[tuple[int, str], _DeviceUi]
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _key(self, device: Device) -> tuple[int, str]:
        generation = self._generation()
        if generation != self._project_generation:
            with self._lock:
                if self._project_generation is not None:
                    self._ui.clear()
                self._project_generation = generation
        return device.node_id, device.app.id if device.app else ""

    def _state(self, key: tuple[int, str]) -> _DeviceUi:
        return self._ui.setdefault(key, _DeviceUi())

    def _start(
        self,
        device: Device,
        label: str,
        text: str,
        op: Callable[[Programmer], Awaitable[object]],
    ) -> None:
        """Queue ``op`` as the operation ``label``, named ``text`` in the Operations panel."""
        key = self._key(device)
        with self._lock:
            state = self._state(key)
            if state.busy:
                return
            state.busy = label
            state.error = ""
            state.run += 1
            run = state.run
        wrapped = self._wrap(state, op)
        self._run(device, wrapped, text, functools.partial(self._ended, state, run))

    def _ended(self, state: _DeviceUi, run: int) -> None:
        """The operation ``run`` has ended: free the device also when it never ran or was
        canceled."""
        with self._lock:
            if state.run == run:
                state.busy = ""

    def _wrap(
        self,
        state: _DeviceUi,
        op: Callable[[Programmer], Awaitable[object]],
    ) -> Callable[[Programmer], Awaitable[object]]:
        async def runner(programmer: Programmer) -> object:
            try:
                result = await op(programmer)
            except Exception as exc:
                with self._lock:
                    st = state
                    st.error = f"{type(exc).__name__}: {exc}"
                    st.busy = ""
                raise
            with self._lock:
                st = state
                if isinstance(result, SiemensN141BusState):
                    st.scan = result
                st.busy = ""
            return result

        return runner

    def render(self, device: Device, connected: bool) -> None:
        key = self._key(device)
        imgui.push_id(f"siemens_dali_{self._project_generation}_{key}")
        try:
            self._render(device, connected, key)
        finally:
            imgui.pop_id()

    def _render(self, device: Device, connected: bool, key: tuple[int, str]) -> None:
        with self._lock:
            state = self._state(key)
            busy, error, scan = state.busy, state.error, state.scan

        if not connected:
            imgui.text_disabled(S.SIEMENS_DALI_CONNECT_FIRST)
            return

        imgui.begin_disabled(bool(busy))
        count = line_count(device.app.id) if device.app else 0
        if count > 1:
            changed, line = combo(
                S.DALI_CHANNEL,
                state.line,
                [S.DALI_CHANNEL_N.format(n=n + 1) for n in range(count)],
            )
            if changed:
                state.line = line
                state.scan = scan = None
        line = state.line
        if imgui.button(S.SIEMENS_DALI_SCAN_BUS):
            self._start(
                device,
                "scan",
                S.SIEMENS_DALI_SCAN_BUS,
                lambda p: scan_n141_03(p, line=line),
            )
        imgui.end_disabled()

        if busy:
            imgui.text_disabled(
                S.SIEMENS_DALI_SCANNING.format(object_index=object_index_for_line(line))
            )
        if error:
            imgui.text_colored(status_color("error"), error)

        if scan is None:
            imgui.text_disabled(S.SIEMENS_DALI_NO_SCAN)
            return
        self._render_ecg_table(scan)

    def _render_ecg_table(self, scan: SiemensN141BusState) -> None:
        present = scan.present_ecgs
        imgui.text_disabled(
            S.SIEMENS_DALI_PRESENT_COUNT(len(present)).format(
                count=len(present), line=scan.line + 1
            )
        )
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        if not begin_data_table("##siemens_dali_ecgs", 6, flags):
            return
        imgui.table_setup_column(
            S.SIEMENS_DALI_COL_SLOT, imgui.TableColumnFlags_.width_fixed, px(45.0)
        )
        imgui.table_setup_column(
            S.SIEMENS_DALI_COL_SHORT_ADDRESS,
            imgui.TableColumnFlags_.width_fixed,
            px(90.0),
        )
        imgui.table_setup_column(S.SIEMENS_DALI_COL_TYPE)
        imgui.table_setup_column(
            S.SIEMENS_DALI_COL_RANDOM, imgui.TableColumnFlags_.width_fixed, px(110.0)
        )
        imgui.table_setup_column(
            S.SIEMENS_DALI_COL_MIN_DIM, imgui.TableColumnFlags_.width_fixed, px(70.0)
        )
        imgui.table_setup_column(
            S.SIEMENS_DALI_COL_COLOUR, imgui.TableColumnFlags_.width_fixed, px(120.0)
        )
        data_table_headers()
        for ecg in present:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(str(ecg.slot))
            imgui.table_next_column()
            imgui.text(str(ecg.short_address))
            imgui.table_next_column()
            imgui.text(_device_type_label(ecg.type))
            imgui.table_next_column()
            imgui.text(f"{ecg.random_address:06X}")
            imgui.table_next_column()
            imgui.text(str(ecg.min_phys_dim_value))
            imgui.table_next_column()
            if ecg.colour_temp_phys_coolest or ecg.colour_temp_phys_warmest:
                imgui.text(
                    f"{ecg.colour_temp_phys_coolest}-{ecg.colour_temp_phys_warmest}"
                )
            else:
                imgui.text("-")
        imgui.end_table()

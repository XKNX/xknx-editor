"""DALI read-only panel for the Siemens KNX/DALI Gateway plus N 141/03.

Siemens N141/03 is a classic ETS plug-in device (``EtsUiPlugin + EtsDownloadPlugin +
EtsDataHandler``). This panel offers only the non-destructive side: a bus read that lists the
ECGs the gateway holds, by reading its DALI-line interface object with standard
``A_PropertyValue_Read``. Writing group assignments, scenes or firmware stays in the vendor
plug-in for now.

Renders on the GUI thread; the async read runs through ``ConnectionService.run_siemens_dali``
and stashes its result under a lock that ``render`` polls (the asyncio thread's done-callback
sets the state).
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px
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
    _ui: dict[str, _DeviceUi] = field(default_factory=dict[str, "_DeviceUi"])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _state(self, ia: str) -> _DeviceUi:
        return self._ui.setdefault(ia, _DeviceUi())

    def _start(
        self,
        device: Device,
        label: str,
        text: str,
        op: Callable[[Programmer], Awaitable[object]],
    ) -> None:
        """Queue ``op`` as the operation ``label``, named ``text`` in the Operations panel."""
        ia = device.individual_address
        with self._lock:
            state = self._state(ia)
            if state.busy:
                return
            state.busy = label
            state.error = ""
            state.run += 1
            run = state.run
        wrapped = self._wrap(ia, op)
        self._run(device, wrapped, text, functools.partial(self._ended, ia, run))

    def _ended(self, ia: str, run: int) -> None:
        """The operation ``run`` has ended: free the device also when it never ran or was
        canceled."""
        with self._lock:
            state = self._state(ia)
            if state.run == run:
                state.busy = ""

    def _wrap(
        self,
        ia: str,
        op: Callable[[Programmer], Awaitable[object]],
    ) -> Callable[[Programmer], Awaitable[object]]:
        async def runner(programmer: Programmer) -> object:
            try:
                result = await op(programmer)
            except Exception as exc:
                with self._lock:
                    st = self._state(ia)
                    st.error = f"{type(exc).__name__}: {exc}"
                    st.busy = ""
                raise
            with self._lock:
                st = self._state(ia)
                if isinstance(result, SiemensN141BusState):
                    st.scan = result
                st.busy = ""
            return result

        return runner

    def render(self, device: Device, connected: bool) -> None:
        ia = device.individual_address
        with self._lock:
            state = self._state(ia)
            busy, error, scan = state.busy, state.error, state.scan

        if not connected:
            imgui.text_disabled(S.SIEMENS_DALI_CONNECT_FIRST)
            return

        imgui.begin_disabled(bool(busy))
        if imgui.button(S.SIEMENS_DALI_SCAN_BUS):
            self._start(
                device,
                "scan",
                S.SIEMENS_DALI_SCAN_BUS,
                lambda p: scan_n141_03(p, line=0),
            )
        imgui.end_disabled()

        if busy:
            imgui.text_disabled(
                S.SIEMENS_DALI_SCANNING.format(object_index=object_index_for_line(0))
            )
        if error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.4, 0.4, 1.0), error)

        if scan is None:
            imgui.text_disabled(S.SIEMENS_DALI_NO_SCAN)
            return
        self._render_ecg_table(scan)
        imgui.separator()
        imgui.text_wrapped(S.SIEMENS_DALI_READ_ONLY_NOTE)

    def _render_ecg_table(self, scan: SiemensN141BusState) -> None:
        present = scan.present_ecgs
        imgui.text_disabled(
            S.SIEMENS_DALI_PRESENT_COUNT.format(count=len(present), line=scan.line + 1)
        )
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        if not imgui.begin_table("##siemens_dali_ecgs", 6, flags):
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
        imgui.table_headers_row()
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

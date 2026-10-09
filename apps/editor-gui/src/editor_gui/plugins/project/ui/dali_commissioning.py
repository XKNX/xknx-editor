"""DALI commissioning tab for MDT gateways (bus scan / identify / new+post installation).

Renders on the GUI thread; runs the async protocol through ``ConnectionService.run_dali`` and stashes
results under a lock that ``render`` polls (done-callbacks fire on the asyncio thread). Only shown for
MDT DALI devices (gated in configure.py). Write/installation actions renumber the bus, so they sit
behind a confirm and every session should start with a read-only scan.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.widgets.dpi import px
from xknxeditor.dali import DaliBusState, MdtDaliCommissioner
from xknxeditor.dali.model import GROUP_SINGLE, GROUP_UNASSIGNED

# Type of the injected runner: (device, op) -> started?  (op gets a commissioner, returns a coro.)
DaliRunner = Callable[
    [Device, Callable[[MdtDaliCommissioner], Awaitable[object]]], bool
]


def _group_label(group_index: int) -> str:
    if group_index == GROUP_SINGLE:
        return "single"
    if group_index == GROUP_UNASSIGNED:
        return "unassigned"
    return str(group_index)


@dataclass
class _DeviceUi:
    """Per-device commissioning state (keyed by individual address)."""

    scan: DaliBusState | None = None
    busy: str = ""  # non-empty = an operation label is running
    error: str = ""
    progress: str = ""


@dataclass
class DaliCommissioningPanel:
    """Stateful "DALI (Bus)" tab body. One instance owned by the project plugin."""

    _run: DaliRunner
    _ui: dict[str, _DeviceUi] = field(default_factory=dict[str, "_DeviceUi"])
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pending_install: str = ""  # "" | "new" | "post" — install awaiting confirmation

    def _state(self, ia: str) -> _DeviceUi:
        return self._ui.setdefault(ia, _DeviceUi())

    # --- op runner + thread-safe result stash ------------------------------

    def _start(
        self,
        device: Device,
        label: str,
        op: Callable[[MdtDaliCommissioner], Awaitable[object]],
        *,
        store_scan: bool = False,
    ) -> None:
        ia = device.individual_address
        with self._lock:
            state = self._state(ia)
            if state.busy:
                return
            state.busy = label
            state.error = ""
            state.progress = ""
        wrapped = self._wrap(ia, op, store_scan)
        if not self._run(device, wrapped):
            with self._lock:
                self._state(ia).busy = ""

    def _wrap(
        self,
        ia: str,
        op: Callable[[MdtDaliCommissioner], Awaitable[object]],
        store_scan: bool,
    ) -> Callable[[MdtDaliCommissioner], Awaitable[object]]:
        async def runner(commissioner: MdtDaliCommissioner) -> object:
            try:
                result = await op(commissioner)
            except Exception as exc:  # surface any bus/protocol error in the UI
                with self._lock:  # set error AND release busy in one acquisition
                    st = self._state(ia)
                    st.error = f"{type(exc).__name__}: {exc}"
                    st.busy = ""
                raise
            # Store the result and clear busy atomically, so a follow-up op that starts the instant
            # busy clears cannot have its state overwritten by this (older) result.
            with self._lock:
                st = self._state(ia)
                if store_scan and isinstance(result, DaliBusState):
                    st.scan = result
                st.busy = ""
            return result

        return runner

    def _note_progress(self, ia: str, text: str) -> None:
        with self._lock:
            self._state(ia).progress = text

    # --- rendering ---------------------------------------------------------

    def render(self, device: Device, connected: bool) -> None:
        ia = device.individual_address
        with self._lock:
            state = self._state(ia)
            busy, error, progress = state.busy, state.error, state.progress
            scan = state.scan

        if not connected:
            imgui.text_disabled(
                "Connect to a KNX interface to commission the DALI bus."
            )
            return

        imgui.begin_disabled(bool(busy))
        if imgui.button("Scan bus"):
            self._start(device, "scan", lambda c: c.scan(), store_scan=True)
        imgui.same_line()
        if imgui.button("All ECGs on"):
            self._start(device, "broadcast", lambda c: c.broadcast(True))
        imgui.same_line()
        if imgui.button("All off"):
            self._start(device, "broadcast", lambda c: c.broadcast(False))
        imgui.same_line()
        if imgui.button("New installation..."):
            self._pending_install = "new"
        imgui.same_line()
        if imgui.button("Post installation..."):
            self._pending_install = "post"
        imgui.end_disabled()

        if busy:
            imgui.text_disabled(
                f"Running: {busy}{f'  ({progress})' if progress else ''}"
            )
        if error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.4, 0.4, 1.0), error)

        self._render_confirm(device, ia)

        if scan is None:
            imgui.text_disabled("Run 'Scan bus' to read the DALI bus.")
            return
        if scan.firmware is not None:
            imgui.text_disabled(f"Firmware {'.'.join(str(x) for x in scan.firmware)}")
        self._render_ecg_table(device, scan, busy=bool(busy))

    def _install_progress(self, ia: str) -> Callable[[object], None]:
        def cb(readback: object) -> None:
            found = getattr(readback, "param1", None)
            if found is not None:
                self._note_progress(ia, f"found {found}")

        return cb

    def _render_confirm(self, device: Device, ia: str) -> None:
        title = "Confirm DALI installation"
        if self._pending_install:
            imgui.open_popup(title)
        if imgui.begin_popup_modal(title)[0]:
            if self._pending_install == "new":
                imgui.text(
                    "New installation re-scans the DALI bus and REASSIGNS every ballast's\n"
                    "short address. Destructive and cannot be undone. Continue?"
                )
            else:
                imgui.text(
                    "Post installation scans the bus for new ballasts and updates addressing.\n"
                    "This changes the device. Continue?"
                )
            if imgui.button("Continue"):
                kind = self._pending_install
                self._pending_install = ""
                self._start_install(device, ia, kind)
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Cancel"):
                self._pending_install = ""
                imgui.close_current_popup()
            imgui.end_popup()

    def _start_install(self, device: Device, ia: str, kind: str) -> None:
        progress = self._install_progress(ia)
        if kind == "new":
            self._start(
                device,
                "new-install",
                lambda c: c.new_installation(on_progress=progress),
                store_scan=True,
            )
        else:
            self._start(
                device,
                "post-install",
                lambda c: c.post_installation(on_progress=progress),
                store_scan=True,
            )

    def _render_ecg_table(
        self, device: Device, scan: DaliBusState, *, busy: bool
    ) -> None:
        present = scan.present_ecgs
        imgui.text_disabled(f"{len(present)} ECG(s) present")
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        if not imgui.begin_table("##dali_ecgs", 6, flags):
            return
        imgui.table_setup_column("#", imgui.TableColumnFlags_.width_fixed, px(40.0))
        for col in ("Group", "Type", "Long address", "Alarm", ""):
            imgui.table_setup_column(col)
        imgui.table_headers_row()
        for ecg in present:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(str(ecg.slot))
            imgui.table_next_column()
            imgui.text(_group_label(ecg.group_index))
            imgui.table_next_column()
            imgui.text(str(ecg.ecg_type))
            imgui.table_next_column()
            imgui.text(f"{ecg.long_address:06X}")
            imgui.table_next_column()
            imgui.text("!" if ecg.alarm else "")
            imgui.table_next_column()
            imgui.begin_disabled(busy)
            if imgui.button(f"Blink##{ecg.slot}"):
                self._start(device, "blink", lambda c, s=ecg.slot: c.blink_ecg(s, True))
            imgui.same_line()
            if imgui.button(f"Stop##{ecg.slot}"):
                self._start(
                    device, "blink-stop", lambda c, s=ecg.slot: c.blink_ecg(s, False)
                )
            imgui.end_disabled()
        imgui.end_table()

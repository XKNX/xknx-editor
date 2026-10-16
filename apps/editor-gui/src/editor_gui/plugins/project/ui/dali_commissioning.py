"""DALI commissioning tab for MDT gateways (bus scan / identify / group + scene programming).

Renders on the GUI thread; runs the async protocol through ``ConnectionService.run_dali`` and stashes
results under a lock that ``render`` polls (done-callbacks fire on the asyncio thread). Only shown for
MDT DALI devices (gated in configure.py). Write/installation actions change the bus, so they sit
behind a confirm and every session should start with a read-only scan. The programmed payload (group
assignment, scenes) is built from the scan plus the user's edits held in :class:`_DeviceUi`.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from xknxeditor.dali import DaliBusState, MdtDaliCommissioner
from xknxeditor.dali.model import (
    GROUP_SINGLE,
    GROUP_UNASSIGNED,
    MAX_GROUP,
    MAX_SCENES,
    EcgAssignment,
    Scene,
    SceneValue,
)

# Type of the injected runner: (device, op) -> started?  (op gets a commissioner, returns a coro.)
DaliRunner = Callable[
    [Device, Callable[[MdtDaliCommissioner], Awaitable[object]]], bool
]

# Group combo entries: real groups 0-15, then "single" and "unassigned".
_GROUP_ITEMS = [str(i) for i in range(MAX_GROUP)] + ["single", "unassigned"]
_MAX_LEVEL = 255


def _group_to_combo(group_index: int) -> int:
    if 0 <= group_index < MAX_GROUP:
        return group_index
    if group_index == GROUP_SINGLE:
        return MAX_GROUP
    return MAX_GROUP + 1  # unassigned / missing


def _combo_to_group(combo_index: int) -> int:
    if combo_index < MAX_GROUP:
        return combo_index
    if combo_index == MAX_GROUP:
        return GROUP_SINGLE
    return GROUP_UNASSIGNED


def _assignments_from(
    scan: DaliBusState, group_edits: dict[int, int]
) -> list[EcgAssignment]:
    """Build the commission plan: every present ECG keeps its ETS number, group from the edits."""
    return [
        EcgAssignment(
            slot=e.slot,
            ets_index=e.ets_ecg_index,
            group_index=group_edits.get(e.slot, e.group_index),
        )
        for e in scan.present_ecgs
    ]


def _scenes_from(scene_edits: dict[int, dict[int, int]]) -> list[Scene]:
    """Build the scene list from the editor state (one SceneValue per group level set)."""
    out: list[Scene] = []
    for index in sorted(scene_edits):
        levels = scene_edits[index]
        if not levels:
            continue
        values = [
            SceneValue(scene=index, target_index=group, level=level)
            for group, level in sorted(levels.items())
        ]
        out.append(Scene(index=index, knx_scene=index + 1, values=values))
    return out


@dataclass
class _DeviceUi:
    """Per-device commissioning state (keyed by individual address)."""

    scan: DaliBusState | None = None
    busy: str = ""  # non-empty = an operation label is running
    error: str = ""
    progress: str = ""
    group_edits: dict[int, int] = field(default_factory=dict[int, int])  # slot -> group
    scene_sel: int = 0  # scene currently being edited
    scene_edits: dict[int, dict[int, int]] = field(
        default_factory=dict[int, "dict[int, int]"]
    )  # scene index -> {group -> level}


@dataclass
class DaliCommissioningPanel:
    """Stateful "DALI (Bus)" tab body. One instance owned by the project plugin."""

    _run: DaliRunner
    _ui: dict[str, _DeviceUi] = field(default_factory=dict[str, "_DeviceUi"])
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pending: str = (
        ""  # "" | "new" | "post" | "groups" | "scenes" — awaiting confirmation
    )

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
                    st.group_edits.clear()  # fresh bus state -> drop stale group edits
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
        if imgui.button("Scan Bus"):
            self._start(device, "scan", lambda c: c.scan(), store_scan=True)
        imgui.same_line()
        if imgui.button("All ECGs On"):
            self._start(device, "broadcast", lambda c: c.broadcast(True))
        imgui.same_line()
        if imgui.button("All Off"):
            self._start(device, "broadcast", lambda c: c.broadcast(False))
        imgui.same_line()
        if imgui.button("New Installation…"):
            self._pending = "new"
        imgui.same_line()
        if imgui.button("Post Installation…"):
            self._pending = "post"
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
        self._render_ecg_table(device, scan, state, busy=bool(busy))
        self._render_scenes(scan, state, busy=bool(busy))

    def _install_progress(self, ia: str) -> Callable[[object], None]:
        def cb(readback: object) -> None:
            found = getattr(readback, "param1", None)
            if found is not None:
                self._note_progress(ia, f"found {found}")

        return cb

    def _render_confirm(self, device: Device, ia: str) -> None:
        title = "Confirm DALI Programming"
        if self._pending:
            imgui.open_popup(title)
        if begin_dialog(title):
            imgui.text(_CONFIRM_TEXT.get(self._pending, ""))
            clicked = dialog_buttons("Continue", "Cancel")
            if clicked == 0:
                kind = self._pending
                self._pending = ""
                self._dispatch(device, ia, kind)
                imgui.close_current_popup()
            elif clicked == 1:
                self._pending = ""
                imgui.close_current_popup()
            end_dialog()

    def _dispatch(self, device: Device, ia: str, kind: str) -> None:
        if kind in ("new", "post"):
            self._start_install(device, ia, kind)
        elif kind == "groups":
            self._start_program_groups(device, ia)
        elif kind == "scenes":
            self._start_program_scenes(device, ia)

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

    def _start_program_groups(self, device: Device, ia: str) -> None:
        st = self._state(ia)
        if st.scan is None:
            return
        assignments = _assignments_from(st.scan, st.group_edits)
        if not assignments:
            return
        self._start(device, "program-groups", lambda c: c.commission(assignments))

    def _start_program_scenes(self, device: Device, ia: str) -> None:
        scenes = _scenes_from(self._state(ia).scene_edits)
        if not scenes:
            return
        self._start(device, "program-scenes", lambda c: c.write_scenes(scenes))

    def _render_ecg_table(
        self, device: Device, scan: DaliBusState, state: _DeviceUi, *, busy: bool
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
        for col in ("Group", "Type", "Long Address", "Alarm", ""):
            imgui.table_setup_column(col)
        imgui.table_headers_row()
        for ecg in present:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(str(ecg.slot))
            imgui.table_next_column()
            effective = state.group_edits.get(ecg.slot, ecg.group_index)
            imgui.set_next_item_width(px(110.0))
            changed, combo = imgui.combo(
                f"##grp{ecg.slot}", _group_to_combo(effective), _GROUP_ITEMS
            )
            if changed:
                state.group_edits[ecg.slot] = _combo_to_group(combo)
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

        imgui.begin_disabled(busy or not present)
        if imgui.button("Program Groups…"):
            self._pending = "groups"
        imgui.end_disabled()

    def _render_scenes(
        self, scan: DaliBusState, state: _DeviceUi, *, busy: bool
    ) -> None:
        groups_in_use = sorted(
            {
                state.group_edits.get(e.slot, e.group_index)
                for e in scan.present_ecgs
                if 0 <= state.group_edits.get(e.slot, e.group_index) < MAX_GROUP
            }
        )
        imgui.separator()
        imgui.text("Scenes")
        if not groups_in_use:
            imgui.text_disabled("Assign ECGs to groups above to define scenes.")
            return
        imgui.set_next_item_width(px(160.0))
        _, state.scene_sel = imgui.combo(
            "Scene##dali_scene_sel",
            state.scene_sel,
            [f"Scene {i + 1}" for i in range(MAX_SCENES)],
        )
        levels = state.scene_edits.setdefault(state.scene_sel, {})
        for group in groups_in_use:
            imgui.set_next_item_width(px(120.0))
            changed, value = imgui.input_int(
                f"Group {group}##scn{group}", levels.get(group, 0)
            )
            if changed:
                levels[group] = max(0, min(_MAX_LEVEL, value))

        has_values = any(lv for lv in state.scene_edits.values())
        imgui.begin_disabled(busy or not has_values)
        if imgui.button("Program Scenes…"):
            self._pending = "scenes"
        imgui.end_disabled()


_CONFIRM_TEXT = {
    "new": (
        "New installation re-scans the DALI bus and REASSIGNS every ballast's\n"
        "short address. Destructive and cannot be undone. Continue?"
    ),
    "post": (
        "Post installation scans the bus for new ballasts and updates addressing.\n"
        "This changes the device. Continue?"
    ),
    "groups": (
        "Write the group assignment to the DALI bus. This applies the groups to the\n"
        "gateway and its ballasts. Continue?"
    ),
    "scenes": ("Write the scene values to the DALI bus. Continue?"),
}

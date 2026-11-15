"""DALI commissioning tab for MDT gateways (bus scan / identify / group + scene programming).

Renders on the GUI thread; runs the async protocol through ``ConnectionService.run_dali`` and stashes
results under a lock that ``render`` polls (done-callbacks fire on the asyncio thread). Only shown for
MDT DALI devices (gated in configure.py). Write/installation actions change the bus, so they sit
behind a confirm and every session should start with a read-only scan. The programmed payload (group
assignment, scenes) is built from the scan plus the user's edits held in :class:`_DeviceUi`.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.tables import begin_data_table, data_table_headers
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
from xknxeditor.dali.profiles import profile_for

# Type of the injected runner, which queues the operation: (device, op, channel, its name in
# the Operations panel, called once it has ended).  (op gets a commissioner.)
DaliRunner = Callable[
    [
        Device,
        Callable[[MdtDaliCommissioner], Awaitable[object]],
        int,
        str,
        Callable[[], None],
        bool,
    ],
    None,
]

# Persist the commissioning result into the project blob for one channel:
# (device, scan, assignments, scenes, channel).
DaliPersist = Callable[
    [Device, DaliBusState, "list[EcgAssignment]", "list[Scene]", int], None
]

# Number of DALI bus channels a device has: (device) -> count (1 for a 1x64 gateway).
DaliChannelCount = Callable[[Device], int]

# Group combo entries: real groups 0-15, then "single" and "unassigned".


def _group_items() -> list[str]:
    """The group choices: the group numbers, then single and unassigned."""
    return [str(i) for i in range(MAX_GROUP)] + [
        S.DALI_GROUP_SINGLE,
        S.DALI_GROUP_UNASSIGNED,
    ]


def _task_text(task: str) -> str:
    """The running task as the user reads it."""
    texts = {
        "scan": S.DALI_TASK_SCAN,
        "broadcast": S.DALI_TASK_BROADCAST,
        "blink": S.DALI_TASK_BLINK,
        "blink-stop": S.DALI_TASK_BLINK_STOP,
        "new-install": S.DALI_TASK_NEW,
        "post-install": S.DALI_TASK_POST,
        "program-groups": S.DALI_TASK_GROUPS,
        "program-scenes": S.DALI_TASK_SCENES,
    }
    return texts.get(task, task)


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
    busy: str = ""  # non-empty = an operation label is queued or running
    # Counts the operations started, so the end of an older one does not free a newer one.
    run: int = 0
    error: str = ""
    progress: str = ""
    group_edits: dict[int, int] = field(default_factory=dict[int, int])  # slot -> group
    scene_sel: int = 0  # scene currently being edited
    scene_edits: dict[int, dict[int, int]] = field(
        default_factory=dict[int, "dict[int, int]"]
    )  # scene index -> {group -> level}
    # Set by a successful groups/scenes write on the asyncio thread; render() picks it up on the
    # GUI thread and persists the backup blob into the project (project edits are not thread-safe).
    persist_pending: bool = False
    pending: str = ""


@dataclass
class DaliCommissioningPanel:
    """Stateful "DALI (Bus)" tab body. One instance owned by the project plugin.

    Each project device and DALI channel keeps its scan and edits across address changes.
    """

    _run: DaliRunner
    _persist: DaliPersist | None = None
    _channel_count: DaliChannelCount | None = None
    _generation: Callable[[], int] = lambda: 0
    _project_generation: int | None = None
    _ui: dict[tuple[tuple[int, str], int], _DeviceUi] = field(
        default_factory=dict[tuple[tuple[int, str], int], _DeviceUi]
    )
    _active_channel: dict[tuple[int, str], int] = field(
        default_factory=dict[tuple[int, str], int]
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _key(self, device: Device) -> tuple[int, str]:
        generation = self._generation()
        if generation != self._project_generation:
            with self._lock:
                if self._project_generation is not None:
                    self._ui.clear()
                    self._active_channel.clear()
                self._project_generation = generation
        return device.node_id, device.app.id if device.app else ""

    def _channels(self, device: Device) -> int:
        if self._channel_count is None:
            return 1
        return max(1, self._channel_count(device))

    def _current_channel(self, key: tuple[int, str]) -> int:
        return self._active_channel.get(key, 0)

    def _state(self, key: tuple[int, str], channel: int) -> _DeviceUi:
        return self._ui.setdefault((key, channel), _DeviceUi())

    # --- op runner + thread-safe result stash ------------------------------

    def _start(
        self,
        device: Device,
        label: str,
        text: str,
        op: Callable[[MdtDaliCommissioner], Awaitable[object]],
        *,
        store_scan: bool = False,
    ) -> None:
        """Queue ``op`` as the operation ``label``, named ``text`` (the button that started it)
        in the Operations panel."""
        key = self._key(device)
        channel = self._current_channel(key)
        with self._lock:
            state = self._state(key, channel)
            if state.busy:
                return
            state.busy = label
            state.error = ""
            state.progress = ""
            state.run += 1
            run = state.run
        wrapped = self._wrap(state, label, op, store_scan)
        self._run(
            device,
            wrapped,
            channel,
            text,
            functools.partial(self._ended, state, run),
            # Everything but a scan changes the gateway or the DALI bus.
            label != "scan",
        )

    def _ended(self, state: _DeviceUi, run: int) -> None:
        """The operation ``run`` has ended: free the channel also when it never ran (canceled
        while it waited, or it could not start) or was canceled while it ran."""
        with self._lock:
            if state.run == run:
                state.busy = ""

    def _wrap(
        self,
        state: _DeviceUi,
        label: str,
        op: Callable[[MdtDaliCommissioner], Awaitable[object]],
        store_scan: bool,
    ) -> Callable[[MdtDaliCommissioner], Awaitable[object]]:
        async def runner(commissioner: MdtDaliCommissioner) -> object:
            try:
                result = await op(commissioner)
            except Exception as exc:  # surface any bus/protocol error in the UI
                with self._lock:  # set error AND release busy in one acquisition
                    st = state
                    st.error = f"{type(exc).__name__}: {exc}"
                    st.busy = ""
                raise
            # Store the result and clear busy atomically, so a follow-up op that starts the instant
            # busy clears cannot have its state overwritten by this (older) result.
            with self._lock:
                st = state
                if store_scan and isinstance(result, DaliBusState):
                    st.scan = result
                    st.group_edits.clear()  # fresh bus state -> drop stale group edits
                if label in ("program-groups", "program-scenes"):
                    # A write landed on the bus; flag the GUI thread to persist the project blob.
                    st.persist_pending = True
                st.busy = ""
            return result

        return runner

    def _persist_backup(self, device: Device, channel: int, state: _DeviceUi) -> None:
        """Hand the current commissioning state to the project persistence callback.

        Runs on the GUI thread (from ``render``) after a groups/scenes write landed on the
        bus. The blob holds the whole channel, so it is rebuilt from the full scan + edits
        every time, not just the slice that was last written.
        """
        if self._persist is None or state.scan is None:
            return
        assignments = _assignments_from(state.scan, state.group_edits)
        scenes = _scenes_from(state.scene_edits)
        self._persist(device, state.scan, assignments, scenes, channel)

    # --- rendering ---------------------------------------------------------

    def render(self, device: Device, connected: bool) -> None:
        key = self._key(device)
        channels = self._channels(device)
        channel = min(self._current_channel(key), channels - 1)
        self._active_channel[key] = channel
        imgui.push_id(f"dali_{self._project_generation}_{key}_{channel}")
        try:
            self._render(device, connected, key, channels, channel)
        finally:
            imgui.pop_id()

    def _render(
        self,
        device: Device,
        connected: bool,
        key: tuple[int, str],
        channels: int,
        channel: int,
    ) -> None:
        with self._lock:
            state = self._state(key, channel)
            busy, error, progress = state.busy, state.error, state.progress
            scan = state.scan
            persist_pending = state.persist_pending
            state.persist_pending = False

        app_id = device.app.id if device.app is not None else None
        profile = profile_for(app_id)
        # Persist the project blob for any recognised Type-A gateway: MDT (gc16) is fully
        # modelled; Theben/IPAS (e64pro) get their group assignments patched into the existing
        # vendor blob with everything else preserved byte-exact.
        can_persist = self._persist is not None and profile is not None
        if persist_pending and can_persist and scan is not None:
            self._persist_backup(device, channel, state)

        if not connected:
            imgui.text_disabled(S.DALI_CONNECT_HINT)
            return

        if profile is not None and profile.unverified:
            imgui.text_colored(status_color("warning"), S.DALI_UNVERIFIED_WARNING)
        if not can_persist:
            imgui.text_disabled(S.DALI_PROJECT_NOT_UPDATED)

        # Channel selector for multi-bus gateways (a 2x64 has two independent DALI buses).
        if channels > 1:
            imgui.begin_disabled(bool(busy))
            changed, new_channel = combo(
                S.DALI_CHANNEL,
                channel,
                [S.DALI_CHANNEL_N.format(n=i + 1) for i in range(channels)],
            )
            imgui.end_disabled()
            if changed and new_channel != channel:
                self._active_channel[key] = new_channel
                return  # re-render next frame with the newly selected channel's state

        imgui.begin_disabled(bool(busy))
        if imgui.button(S.DALI_SCAN):
            self._start(
                device, "scan", S.DALI_SCAN, lambda c: c.scan(), store_scan=True
            )
        imgui.same_line()
        if imgui.button(S.DALI_ALL_ON):
            self._start(device, "broadcast", S.DALI_ALL_ON, lambda c: c.broadcast(True))
        imgui.same_line()
        if imgui.button(S.DALI_ALL_OFF):
            self._start(
                device, "broadcast", S.DALI_ALL_OFF, lambda c: c.broadcast(False)
            )
        imgui.same_line()
        if imgui.button(S.DALI_NEW_INSTALLATION):
            state.pending = "new"
        imgui.same_line()
        if imgui.button(S.DALI_POST_INSTALLATION):
            state.pending = "post"
        imgui.end_disabled()

        if busy:
            running = S.DALI_RUNNING.format(task=_task_text(busy))
            imgui.text_disabled(f"{running}  ({progress})" if progress else running)
        if error:
            imgui.text_colored(status_color("error"), error)

        self._render_confirm(device, key)

        if scan is None:
            imgui.text_disabled(S.DALI_SCAN_HINT)
            return
        if scan.firmware is not None:
            version = ".".join(str(x) for x in scan.firmware)
            imgui.text_disabled(S.DALI_FIRMWARE.format(version=version))
        self._render_ecg_table(device, scan, state, busy=bool(busy))
        self._render_scenes(scan, state, busy=bool(busy))

    def _install_progress(self, key: tuple[int, str]) -> Callable[[object], None]:
        state = self._state(key, self._current_channel(key))

        def cb(readback: object) -> None:
            found = getattr(readback, "param1", None)
            if found is not None:
                with self._lock:
                    state.progress = S.DALI_FOUND.format(count=found)

        return cb

    def _render_confirm(self, device: Device, key: tuple[int, str]) -> None:
        channel = self._current_channel(key)
        state = self._state(key, channel)
        title = f"{S.DALI_CONFIRM_TITLE}###dali_confirm_{self._project_generation}_{key}_{channel}"
        if state.pending:
            imgui.open_popup(title)
        if begin_dialog(title):
            imgui.text(_confirm_text(state.pending))
            clicked = dialog_buttons(S.DALI_CONTINUE, S.DALI_CANCEL)
            if clicked == 0:
                kind = state.pending
                state.pending = ""
                self._dispatch(device, key, kind)
                imgui.close_current_popup()
            elif clicked == 1:
                state.pending = ""
                imgui.close_current_popup()
            end_dialog()

    def _dispatch(self, device: Device, key: tuple[int, str], kind: str) -> None:
        if kind in ("new", "post"):
            self._start_install(device, key, kind)
        elif kind == "groups":
            self._start_program_groups(device, key)
        elif kind == "scenes":
            self._start_program_scenes(device, key)

    def _start_install(self, device: Device, key: tuple[int, str], kind: str) -> None:
        progress = self._install_progress(key)
        if kind == "new":
            self._start(
                device,
                "new-install",
                S.DALI_NEW_INSTALLATION,
                lambda c: c.new_installation(on_progress=progress),
                store_scan=True,
            )
        else:
            self._start(
                device,
                "post-install",
                S.DALI_POST_INSTALLATION,
                lambda c: c.post_installation(on_progress=progress),
                store_scan=True,
            )

    def _start_program_groups(self, device: Device, key: tuple[int, str]) -> None:
        st = self._state(key, self._current_channel(key))
        if st.scan is None:
            return
        assignments = _assignments_from(st.scan, st.group_edits)
        if not assignments:
            return
        self._start(
            device,
            "program-groups",
            S.DALI_PROGRAM_GROUPS,
            lambda c: c.commission(assignments),
        )

    def _start_program_scenes(self, device: Device, key: tuple[int, str]) -> None:
        st = self._state(key, self._current_channel(key))
        scenes = _scenes_from(st.scene_edits)
        if not scenes:
            return
        self._start(
            device,
            "program-scenes",
            S.DALI_PROGRAM_SCENES,
            lambda c: c.write_scenes(scenes),
        )

    def _render_ecg_table(
        self, device: Device, scan: DaliBusState, state: _DeviceUi, *, busy: bool
    ) -> None:
        present = scan.present_ecgs
        imgui.text_disabled(
            S.DALI_ECGS_PRESENT(len(present)).format(count=len(present))
        )
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.reorderable
        )
        if not begin_data_table("##dali_ecgs", 6, flags):
            return
        imgui.table_setup_column("#", imgui.TableColumnFlags_.width_fixed, px(40.0))
        for col in (
            S.DALI_COL_GROUP,
            S.DALI_COL_TYPE,
            S.DALI_COL_LONG_ADDRESS,
            S.DALI_COL_ALARM,
            "",
        ):
            imgui.table_setup_column(col)
        data_table_headers()
        for ecg in present:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(str(ecg.slot))
            imgui.table_next_column()
            effective = state.group_edits.get(ecg.slot, ecg.group_index)
            imgui.set_next_item_width(px(110.0))
            changed, picked = combo(
                f"##grp{ecg.slot}", _group_to_combo(effective), _group_items()
            )
            if changed:
                state.group_edits[ecg.slot] = _combo_to_group(picked)
            imgui.table_next_column()
            imgui.text(str(ecg.ecg_type))
            imgui.table_next_column()
            imgui.text(f"{ecg.long_address:06X}")
            imgui.table_next_column()
            imgui.text("!" if ecg.alarm else "")
            imgui.table_next_column()
            imgui.begin_disabled(busy)
            if imgui.button(f"{S.DALI_BLINK}##blink{ecg.slot}"):
                self._start(
                    device,
                    "blink",
                    S.DALI_BLINK,
                    lambda c, s=ecg.slot: c.blink_ecg(s, True),
                )
            imgui.same_line()
            if imgui.button(f"{S.DALI_STOP}##stop{ecg.slot}"):
                self._start(
                    device,
                    "blink-stop",
                    S.DALI_STOP,
                    lambda c, s=ecg.slot: c.blink_ecg(s, False),
                )
            imgui.end_disabled()
        imgui.end_table()

        imgui.begin_disabled(busy or not present)
        if imgui.button(S.DALI_PROGRAM_GROUPS):
            state.pending = "groups"
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
        imgui.text(S.DALI_SCENES)
        if not groups_in_use:
            imgui.text_disabled(S.DALI_SCENES_HINT)
            return
        imgui.set_next_item_width(px(160.0))
        _, state.scene_sel = combo(
            f"{S.DALI_SCENE}##dali_scene_sel",
            state.scene_sel,
            [S.DALI_SCENE_NUMBER.format(number=i + 1) for i in range(MAX_SCENES)],
        )
        levels = state.scene_edits.setdefault(state.scene_sel, {})
        for group in groups_in_use:
            imgui.set_next_item_width(px(120.0))
            changed, value = imgui.input_int(
                f"{S.DALI_GROUP_NUMBER.format(number=group)}##scn{group}",
                levels.get(group, 0),
            )
            if changed:
                levels[group] = max(0, min(_MAX_LEVEL, value))

        has_values = any(lv for lv in state.scene_edits.values())
        imgui.begin_disabled(busy or not has_values)
        if imgui.button(S.DALI_PROGRAM_SCENES):
            state.pending = "scenes"
        imgui.end_disabled()


def _confirm_text(kind: str) -> str:
    """What confirming the pending ``kind`` of programming does."""
    texts = {
        "new": S.DALI_CONFIRM_NEW,
        "post": S.DALI_CONFIRM_POST,
        "groups": S.DALI_CONFIRM_GROUPS,
        "scenes": S.DALI_CONFIRM_SCENES,
    }
    return texts.get(kind, "")

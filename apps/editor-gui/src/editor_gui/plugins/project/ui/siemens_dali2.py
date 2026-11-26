"""Discovery and physical commissioning of Siemens DALI-2 lines."""

from __future__ import annotations

import dataclasses
import functools
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugins.project.strings import S
from editor_gui.programming_siemens_dali2 import (
    Action,
    GuardedOperation,
    IdentificationControl,
    IdentificationSession,
    Operation,
    Result,
    Target,
    prepare_operation,
)
from editor_gui.theme import status_color
from editor_gui.widgets.combo import combo
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from xknxeditor.dali.siemens_dali2 import (
    ColorTemperatureState,
    GearState,
    Programmer,
    RgbwState,
    Scan,
    line_count,
    supports_color_commands,
    uses_legacy_identification,
)

Runner = Callable[
    [Device, Callable[[Programmer], Awaitable[object]], str, Callable[[], None], bool],
    None,
]

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectService


@dataclass
class _State:
    line: int = 0
    sensors: bool = False
    address: int = 0
    target: int = 0
    busy: bool = False
    error: str = ""
    run: int = 0
    duration: float = 15
    level: int = 0
    kelvin: int = 4000
    rgbw: tuple[int, int, int, int] = (0, 0, 0, 0)
    colors: dict[tuple[int, int, str], ColorTemperatureState | RgbwState] = field(
        default_factory=dict[tuple[int, int, str], ColorTemperatureState | RgbwState]
    )
    gear_states: dict[tuple[int, int], GearState] = field(
        default_factory=dict[tuple[int, int], GearState]
    )
    session: tuple[Target, IdentificationSession] | None = None
    scans: dict[tuple[int, bool], Scan | None] = field(
        default_factory=dict[tuple[int, bool], Scan | None]
    )


@dataclass
class SiemensDali2Panel:
    """One panel per project; each device keeps its own inventory and selection."""

    _run: Runner
    _generation: Callable[[], int] = lambda: 0
    _project_generation: int | None = None
    _identification: IdentificationControl = field(
        default_factory=IdentificationControl
    )
    _project: ProjectService | None = None
    _states: dict[tuple[int, str, str], _State] = field(
        default_factory=dict[tuple[int, str, str], _State]
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _state(self, device: Device) -> _State:
        generation = self._generation()
        if generation != self._project_generation:
            self._states.clear()
            self._project_generation = generation
        return self._states.setdefault(
            (
                device.node_id,
                device.app.id if device.app else "",
                device.individual_address,
            ),
            _State(),
        )

    def _ended(self, state: _State, run: int) -> None:
        with self._lock:
            if state.run == run:
                state.busy = False
                if state.session is not None:
                    self._identification.finish(*state.session)
                    state.session = None

    def _start(
        self, device: Device, action: Action, label: str, *, set_color: bool = False
    ) -> None:
        with self._lock:
            state = self._state(device)
            target = (
                self._generation(),
                device.node_id,
                device.app.id if device.app else "",
                device.individual_address,
                state.line,
                state.sensors,
            )
            if action == "stop" and self._identification.stop(target) is not None:
                return
            if state.busy:
                return
            scan = state.scans.get((state.line, state.sensors))
            selected = (
                next(
                    (d for d in scan.devices if d.short_address == state.address), None
                )
                if scan
                else None
            )
            try:
                operation = prepare_operation(
                    device.app.id if device.app else None,
                    action,
                    line=state.line,
                    sensors=state.sensors,
                    address=state.address,
                    new_address=state.target,
                    random_address=selected.random_address if selected else 0,
                    duration=state.duration,
                    level=state.level,
                    kelvin=state.kelvin
                    if set_color and action == "temperature"
                    else None,
                    rgbw=state.rgbw if set_color and action == "rgbw" else None,
                    timeout=15
                    if action not in ("read", "discover", "address")
                    else 300,
                )
                if operation.legacy and action == "blink":
                    session = self._identification.start(target)
                    state.session = (target, session)
                    operation = dataclasses.replace(operation, session=session)
            except ValueError as exc:
                state.error = str(exc)
                return
            state.busy = True
            state.error = ""
            if not state.sensors and action not in ("read", "discover"):
                state.gear_states.pop((operation.line, operation.address), None)
                state.colors = {
                    key: row
                    for key, row in state.colors.items()
                    if key[:2] != (operation.line, operation.address)
                    or (
                        action in ("temperature", "rgbw")
                        and not set_color
                        and key[2] != action
                    )
                }
            state.run += 1
            run = state.run

        async def execute(programmer: Programmer) -> Result:
            error: BaseException | None = None
            if operation.session is not None:
                operation.session.started.set()
            try:
                result = await operation(programmer)
                with self._lock:
                    self._store(state, operation, result)
                return result
            except BaseException as exc:
                error = exc
                with self._lock:
                    state.error = str(exc)
                raise
            finally:
                if state.session is not None:
                    self._identification.finish(*state.session, error)

        try:
            self._run(
                device,
                GuardedOperation(self._project, target, execute)
                if self._project
                else execute,
                label,
                functools.partial(self._ended, state, run),
                operation.writes,
            )
        except Exception as exc:
            with self._lock:
                state.error = str(exc)
                state.busy = False
                if state.session is not None:
                    self._identification.finish(*state.session, exc)
                    state.session = None

    @staticmethod
    def _store(state: _State, operation: Operation, result: Result) -> None:
        if operation.action in ("read", "discover", "address"):
            if result is not None and not isinstance(result, Scan):
                raise ValueError("Invalid DALI-2 inventory response")
            state.scans[(operation.line, operation.sensors)] = result
            if not operation.sensors:
                state.gear_states = {
                    key: row
                    for key, row in state.gear_states.items()
                    if key[0] != operation.line
                }
                state.colors = {
                    key: row
                    for key, row in state.colors.items()
                    if key[0] != operation.line
                }
            if operation.action == "address":
                state.address = operation.new_address
        elif isinstance(result, GearState):
            state.gear_states[(operation.line, operation.address)] = result
        elif isinstance(result, (ColorTemperatureState, RgbwState)):
            state.colors[(operation.line, operation.address, operation.action)] = result

    def render(self, device: Device, connected: bool) -> None:
        count = line_count(device.app.id if device.app else None)
        if not count:
            return
        with self._lock:
            state = self._state(device)
            busy, error = state.busy, state.error
        imgui.push_id(f"siemens_dali2_{self._project_generation}_{device.node_id}")
        try:
            imgui.begin_disabled(busy)
            if count > 1:
                _, state.line = combo(
                    S.DALI_CHANNEL,
                    state.line,
                    [S.DALI_CHANNEL_N.format(n=n + 1) for n in range(count)],
                )
            _, kind = combo(
                S.E64_DEVICE, int(state.sensors), [S.E64_ECG, S.E64_INPUT_DEVICES]
            )
            state.sensors = bool(kind)
            imgui.begin_disabled(not connected)
            scan_actions: tuple[tuple[Action, str], ...] = (
                ("read", S.E64_BUS_ACTIONS["read"]),
                ("discover", S.DALI_SCAN),
            )
            for index, (action, label) in enumerate(scan_actions):
                if index:
                    same_line_if_fits(button_width(label))
                if imgui.button(label):
                    self._start(device, action, label)
            imgui.end_disabled()
            imgui.end_disabled()
            if not connected:
                imgui.text_disabled(S.SIEMENS_DALI_CONNECT_FIRST)
            if error:
                imgui.text_colored(status_color("error"), error)
            if busy and state.session is not None and imgui.button(S.DALI_STOP):
                self._start(device, "stop", S.DALI_STOP)
            with self._lock:
                scan = state.scans.get((state.line, state.sensors))
            if scan is None:
                imgui.text_disabled(S.SIEMENS_DALI2_NO_SCAN)
                return
            self._table(state, scan)
            imgui.begin_disabled(busy or not connected or not scan.devices)
            _, state.address = imgui.input_int(S.E64_ADDRESS, state.address)
            identify_actions: tuple[tuple[Action, str], ...] = (
                ("blink", S.DALI_BLINK),
                ("stop", S.DALI_STOP),
                ("on", S.DALI_SWITCH_ON),
                ("off", S.DALI_SWITCH_OFF),
            )
            legacy = uses_legacy_identification(device.app.id if device.app else None)
            if legacy:
                _, state.duration = imgui.input_float(
                    f"{S.E64_DURATION} (s)", state.duration
                )
            for index, (action, label) in enumerate(identify_actions):
                if legacy and state.sensors and action in ("on", "off"):
                    continue
                if index:
                    same_line_if_fits(button_width(label))
                if imgui.button(label):
                    self._start(device, action, label)
            _, state.target = imgui.input_int(S.E64_SHORT_ADDRESS, state.target)
            if imgui.button(S.E64_APPLY):
                self._start(device, "address", S.E64_SHORT_ADDRESS)
            if not state.sensors:
                imgui.separator_text(S.E64_TEST)
                if imgui.button(S.E64_STATUS):
                    self._start(device, "status", S.E64_STATUS)
                _, state.level = imgui.input_int(
                    f"{S.E64_BRIGHTNESS} (DALI 0..254)", state.level
                )
                if imgui.button(S.E64_TEST):
                    self._start(device, "level", S.E64_TEST)
                with self._lock:
                    current = state.gear_states.get((state.line, state.address))
                if current is not None:
                    status = (
                        f"0x{current.status:02X}" if current.status is not None else "?"
                    )
                    level = str(current.level) if current.level is not None else "?"
                    imgui.text_unformatted(
                        f"{S.E64_STATUS}: {status}   {S.E64_BRIGHTNESS}: {level}"
                    )
                    if current.status is not None and current.status & 3:
                        failed = [
                            label
                            for bit, label in ((1, S.E64_ECG), (2, S.E64_LAMPS))
                            if current.status & bit
                        ]
                        imgui.text_colored(
                            status_color("error"),
                            f"{S.E64_ERRORS}: {', '.join(failed)}",
                        )
                if supports_color_commands(device.app.id if device.app else None):
                    self._colors(device, state)
            imgui.end_disabled()
        finally:
            imgui.pop_id()

    def _colors(self, device: Device, state: _State) -> None:
        imgui.separator_text(S.E64_COLOR)
        actions: tuple[tuple[Action, str], ...] = (
            ("temperature", S.E64_COLOR_TEMPERATURE),
            ("rgbw", "RGBW"),
        )
        for action, label in actions:
            imgui.push_id(action)
            try:
                if action == "temperature":
                    _, state.kelvin = imgui.input_int(f"{label} (K)", state.kelvin)
                else:
                    _, channels = imgui.input_int4("RGBW (0..255)", list(state.rgbw))
                    state.rgbw = (channels[0], channels[1], channels[2], channels[3])
                if imgui.button(S.E64_BUS_ACTIONS["read"]):
                    self._start(device, action, label)
                same_line_if_fits(button_width(S.E64_TEST))
                if imgui.button(S.E64_TEST):
                    self._start(device, action, label, set_color=True)
                with self._lock:
                    current = state.colors.get((state.line, state.address, action))
                if isinstance(current, ColorTemperatureState):
                    imgui.text_unformatted(
                        f"{S.E64_STATUS}: {current.kelvin if current.kelvin is not None else '?'} K"
                    )
                elif isinstance(current, RgbwState):
                    imgui.text_unformatted(
                        f"{S.E64_STATUS}: {current.red}, {current.green}, {current.blue}, {current.white}"
                    )
            finally:
                imgui.pop_id()

    @staticmethod
    def _table(state: _State, scan: Scan) -> None:
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        height = min(
            imgui.get_text_line_height_with_spacing() * (len(scan.devices) + 3),
            max(
                imgui.get_text_line_height_with_spacing() * 4,
                imgui.get_content_region_avail().y * 0.55,
            ),
        )
        if not begin_data_table("inventory", 7, flags, imgui.ImVec2(0, height)):
            return
        for label in (
            S.E64_ADDRESS,
            S.SIEMENS_DALI_COL_RANDOM,
            S.E64_FLAG,
            S.CONFIGURE_APP_VERSION,
            S.SIEMENS_DALI_COL_TYPE,
            S.SIEMENS_DALI_COL_MIN_DIM,
            S.E64_INSTANCE,
        ):
            imgui.table_setup_column(label)
        data_table_headers()
        for device in scan.devices:
            imgui.table_next_row()
            imgui.table_next_column()
            if imgui.selectable(
                str(device.short_address), state.address == device.short_address
            )[0]:
                state.address = device.short_address
            values = (
                f"{device.random_address:06X}",
                str(device.flags),
                str(device.version),
                ", ".join(map(str, device.types)),
                str(device.min_level),
                ", ".join(
                    f"{i.number}: {i.type} ({i.resolution})" for i in device.instances
                ),
            )
            for value in values:
                imgui.table_next_column()
                imgui.text_unformatted(value)
        imgui.end_table()

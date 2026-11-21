"""Gateway operations for the saved DCA workspace."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.e64pro import cells, pick, table
from editor_gui.programming_dali import (
    GuardedDaliOperation,
    finish_workspace_operation,
    prepare_workspace_operation,
)
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.flow import button_width, same_line_if_fits

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from editor_gui.plugins.project.ui.dali_commissioning import DaliRunner
    from xknxeditor.dali import MdtDaliCommissioner


@dataclass
class BusView:
    busy: bool = False
    error: str = ""
    section: str = "scenes"
    motion_section: str = "names"
    index: int = 0
    row: int = 0
    test_group: int | None = None
    value: int = 10
    secondary: int = 0
    bank: int = 2
    lock: bool = False
    confirmation: str = ""
    result: dict[str, Any] = field(default_factory=dict[str, Any])

    def start(
        self, device: Device, project: ProjectService, run: DaliRunner, action: str
    ) -> None:
        try:
            plan = prepare_workspace_operation(
                device,
                action,
                self.section,
                self.index,
                self.row,
                self.value,
                self.secondary,
                self.bank,
                self.lock,
                test_group=self.test_group
                if action == "test" and self.section == "templates"
                else None,
            )
            generation = project.generation
            pending: list[dict[str, Any]] = []
            errors: list[str] = []
            self.error = ""
            self.busy = True

            async def operation(commissioner: MdtDaliCommissioner) -> object:
                try:
                    result = await plan.execute(commissioner)
                    pending.append(result)
                    return result
                except Exception as exc:
                    errors.append(str(exc))
                    raise

            def ended() -> None:
                self.busy = False
                if errors:
                    self.error = errors[0]
                if pending:
                    try:
                        self.result = finish_workspace_operation(
                            project, device, plan, generation, pending[0]
                        )
                    except Exception as exc:
                        self.error = str(exc)

            run(
                device,
                GuardedDaliOperation(
                    project, device, plan.expected, generation, operation
                ),
                0,
                S.E64_BUS_ACTIONS[action],
                ended,
                plan.writes,
            )
        except Exception as exc:
            self.busy = False
            self.error = str(exc)

    def render(self, device: Device, project: ProjectService, run: DaliRunner) -> None:
        sections = {
            "scenes": S.E64_SCENES,
            "effects": S.E64_EFFECTS,
            "templates": S.E64_TIME_CONTROL,
            "inputs": S.E64_INPUT_DEVICES,
            "ecgs": S.E64_ECG,
            "groups": S.E64_GROUPS,
            "mds": S.E64_MOTION_BRIGHTNESS,
        }
        imgui.begin_disabled(self.busy)
        self.section = pick(S.E64_TARGET, self.section, sections)
        maximum = (
            64
            if self.section in ("inputs", "ecgs")
            else 8
            if self.section == "mds"
            else 16
        )
        self.index = min(self.index, maximum - 1)
        _, number = imgui.input_int(
            S.E64_DEVICE if maximum == 64 else S.E64_TARGET + "##number", self.index + 1
        )
        self.index = min(max(number - 1, 0), maximum - 1)
        if self.section == "templates":
            _, number = imgui.input_int(S.E64_ACTIONS, self.row + 1)
            self.row = max(0, number - 1)
            selected = pick(
                S.E64_TEST + "##target",
                "members" if self.test_group is None else str(self.test_group),
                {"members": S.E64_TEMPLATE_MEMBERS}
                | {str(i): f"{S.E64_GROUP} {i + 1}" for i in range(16)},
            )
            self.test_group = None if selected == "members" else int(selected)
        if self.section == "mds":
            self.motion_section = pick(
                S.E64_SERVICE,
                self.motion_section,
                {
                    "names": S.E64_DESCRIPTIONS,
                    "motion": S.E64_STATUS,
                    "calibration": S.E64_CALIBRATION,
                    "memory": S.E64_MEMORY_BANK,
                },
            )
            if self.motion_section in ("calibration", "memory"):
                _, self.value = imgui.input_int(
                    S.E64_BRIGHTNESS
                    if self.motion_section == "calibration"
                    else S.E64_VALUE,
                    self.value,
                )
                _, self.secondary = imgui.input_int(
                    S.E64_LUX
                    if self.motion_section == "calibration"
                    else S.E64_ADDRESS,
                    self.secondary,
                )
            if self.motion_section == "memory":
                _, self.bank = imgui.input_int(S.E64_MEMORY_BANK, self.bank)
                _, self.lock = checkbox(S.E64_MEMORY_LOCK, self.lock)
        actions = {
            "scenes": ("read", "write", "test"),
            "effects": ("read", "write", "start", "stop"),
            "templates": ("read", "write", "test"),
            "inputs": ("read", "write", "discover", "identify", "stop_identify"),
            "ecgs": (
                "read_names",
                "write_names",
                "function",
                "duration",
                "battery",
                "inhibit",
                "replace",
                "restore",
            ),
            "groups": ("read_names", "write_names"),
            "mds": (
                "read_names",
                "write_names",
                "read_motion",
                "read_calibration",
                "set_calibration",
                "save_calibration",
                "read_memory",
                "write_memory",
            ),
        }[self.section]
        if self.section == "mds":
            actions = {
                "names": ("read_names", "write_names"),
                "motion": ("read_motion",),
                "calibration": (
                    "read_calibration",
                    "set_calibration",
                    "save_calibration",
                ),
                "memory": ("read_memory", "write_memory"),
            }[self.motion_section]
        for i, action in enumerate(actions):
            label = S.E64_BUS_ACTIONS[action]
            if i:
                same_line_if_fits(button_width(label))
            if imgui.button(label):
                if action in ("replace", "restore"):
                    self.confirmation = action
                    imgui.open_popup(S.E64_BUS_ACTIONS[action])
                else:
                    self.start(device, project, run, action)
        imgui.end_disabled()
        if self.confirmation and begin_dialog(S.E64_BUS_ACTIONS[self.confirmation]):
            imgui.text_wrapped(
                S.E64_RESTORE_CONFIRM
                if self.confirmation == "restore"
                else S.E64_REPLACE_CONFIRM
            )
            choice = dialog_buttons(S.DALI_CONTINUE, S.DALI_CANCEL)
            if choice is not None:
                action = self.confirmation
                self.confirmation = ""
                imgui.close_current_popup()
                if choice == 0:
                    self.start(device, project, run, action)
            end_dialog()
        if self.error:
            imgui.text_wrapped(self.error)
        self.show_result()

    def show_result(self) -> None:
        result = self.result
        if "names" in result:
            with table("##names", [S.E64_DEVICE, S.E64_DESCRIPTION]) as opened:
                if opened:
                    for index, name in enumerate(result["names"]):
                        cells([str(index + 1), name])
        if "devices" in result:
            with table(
                "##inputs",
                [S.E64_ADDRESS, S.E64_INSTANCE, S.E64_TYPE, S.E64_STATUS, S.E64_VALUE],
            ) as opened:
                if opened:
                    for device in result["devices"]:
                        for instance in device["instances"]:
                            cells(
                                [
                                    str(device["address"] + 1),
                                    str(instance["number"]),
                                    S.E64_INPUT_TYPES.get(
                                        instance["kind"], S.E64_INPUT_TYPES[255]
                                    ),
                                    str(instance["status"]),
                                    str(instance["value"]),
                                ]
                            )
        if "controllers" in result:
            with table(
                "##motion",
                [
                    S.E64_DEVICE,
                    S.E64_STATUS,
                    S.E64_ERRORS,
                    S.E64_BRIGHTNESS,
                    S.E64_TARGET,
                    S.E64_VALUE,
                ],
            ) as opened:
                if opened:
                    for controller in result["controllers"]:
                        cells(
                            [
                                str(controller["index"] + 1),
                                str(controller["motion"]),
                                str(controller["error"]),
                                str(controller["brightness"]),
                                str(controller["setpoint"]),
                                str(controller["output"]),
                            ]
                        )
        if "minimum_level" in result:
            with table(
                "##calibration",
                [S.E64_CALIBRATION, S.E64_VALUE, S.E64_LUX, S.E64_BRIGHTNESS],
            ) as opened:
                if opened:
                    for prefix, label in [
                        ("minimum", S.E64_MIN_BRIGHTNESS),
                        ("maximum", S.E64_MAX_BRIGHTNESS),
                    ]:
                        cells(
                            [
                                label,
                                str(result[prefix + "_level"]),
                                str(result[prefix + "_lux"]),
                                str(result[prefix + "_sensor"]),
                            ]
                        )
        for key in ("value", "level", "result"):
            if key in result:
                imgui.text(f"{S.E64_RESULT}: {result[key]}")

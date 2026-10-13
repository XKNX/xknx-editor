"""Runs offline Button handlers on the script worker while the GUI waits."""

from __future__ import annotations

import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, end_dialog
from xknxeditor.prod.script import AbortToken, ScriptAborted, ScriptError
from xknxeditor.prod.script.button import run_offline_button
from xknxeditor.prod.script.worker import default_worker

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.prod.parser_v2.calculation import ChangeSet
    from xknxeditor.prod.parser_v2.ui import UiButton

_POPUP_ID = "###button_script_running"


@dataclass
class _Run:
    device: Device
    button: UiButton
    future: Future[ChangeSet]
    abort: AbortToken
    started: float
    old_active: set[str]


class ButtonRunner:
    """One offline handler at a time; the GUI is blocked by a modal until it finishes."""

    WAIT = 0.2
    FORCE_STOP_AFTER = 10.0

    def __init__(self, project: ProjectService, log: Logger) -> None:
        self._project = project
        self._log = log
        self._run: _Run | None = None

    @property
    def busy(self) -> bool:
        return self._run is not None

    def start(self, device: Device, button: UiButton) -> None:
        if self._run is not None or device.script_running:
            return
        old_active = self._project.parameter_driven_com_objects(device)
        ui = device.begin_script()
        if ui is None:
            device.end_script()
            return
        device.param_errors.pop(button.id, None)
        abort = AbortToken()
        future = default_worker().submit(
            lambda: run_offline_button(ui, button, abort=abort, on_log=self._script_log)
        )
        self._run = _Run(device, button, future, abort, time.monotonic(), old_active)
        try:
            future.result(timeout=self.WAIT)
        except FutureTimeout:
            return
        except BaseException:
            pass
        self.poll()

    def _script_log(self, level: str, message: str) -> None:
        self._log.info("script log", level=level, message=message)

    def poll(self) -> None:
        run = self._run
        if run is None or not run.future.done():
            return
        self._run = None
        run.device.end_script()
        try:
            changes = run.future.result()
        except ScriptAborted:
            run.device.param_errors[run.button.id] = S.SCRIPT_ABORTED
            return
        except ScriptError as exc:
            run.device.param_errors[run.button.id] = exc.message
            self._log.warning(
                "button script failed", button=run.button.id, error=exc.message
            )
            return
        except Exception as exc:
            run.device.param_errors[run.button.id] = str(exc)
            self._log.error(
                "button script failed", button=run.button.id, error=str(exc)
            )
            return
        self._project.commit_param_changes(
            run.device,
            changes,
            run.old_active,
            label=S.BUTTON_EXECUTED.format(run.button.text),
        )

    def render(self) -> None:
        run = self._run
        if run is None:
            return
        title = f"{S.SCRIPT_OPERATION}{_POPUP_ID}"
        if not imgui.is_popup_open(title):
            imgui.open_popup(title)
        if begin_dialog(title, flags=imgui.WindowFlags_.no_move):
            imgui.text(S.SCRIPT_RUNNING)
            if time.monotonic() - run.started >= self.FORCE_STOP_AFTER:
                imgui.text_disabled(S.SCRIPT_NOT_RESPONDING)
                if imgui.button(S.SCRIPT_FORCE_STOP):
                    run.abort.request()
            if not run.future.done():
                end_dialog()
                return
            imgui.close_current_popup()
            end_dialog()
        self.poll()

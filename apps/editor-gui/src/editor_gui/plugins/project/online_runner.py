"""Runs online Button handlers as "Parameter Script" operations of the program queue."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from xknx.telegram import IndividualAddress

from editor_gui.plugins.project.strings import S
from xknxeditor.download.errors import DownloadError
from xknxeditor.download.scope import mask_object_types
from xknxeditor.download.script_online import OnlineHost, OnlineSession
from xknxeditor.prod.script import ScriptAborted, ScriptError
from xknxeditor.prod.script.button import ParameterAccess, run_button
from xknxeditor.prod.script.worker import default_worker

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.connection.service import ConnectionService
    from editor_gui.plugins.project.program_queue import QueueItem, ScriptRun
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.prod.parser_v2.calculation import ChangeSet
    from xknxeditor.prod.script.sandbox import HostFunction

# Wait for the bus session to close after the handler returned or failed.
_DISPOSE_TIMEOUT = 10.0


def _mask_version(device: Device) -> int | None:
    text = (device.app.program.mask_version if device.app else None) or ""
    try:
        return int(text.removeprefix("MV-"), 16)
    except ValueError:
        return None


def _object_types(conn: ConnectionService, device: Device) -> dict[int, int]:
    """Interface objects the device's mask declares, by index."""
    if conn.master is None or device.app is None:
        return {}
    try:
        return mask_object_types(conn.master.raw, device.app.program.mask_version)
    except DownloadError:
        return {}


class OnlineButtonRunner:
    """Starts one online handler on the script worker; writes are persisted one by one on the
    GUI thread and the session is closed when the handler returns or fails."""

    def __init__(
        self,
        project: ProjectService,
        connection: ConnectionService,
        submit: Callable[[Callable[[], None]], Any],
        log: Logger,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self._project = project
        self._connection = connection
        self._submit = submit
        self._log = log
        self._notify = notify

    def start(self, item: QueueItem) -> Future[Any] | None:
        conn = self._connection
        connector = conn.operation_connector
        if connector is not None and (
            (conn.xknx is None and conn.can_reach_bus())
            or (conn.xknx is not None and connector.opened_for_operations())
        ):
            # Run through a connection opened for it (and the operations around it).
            return conn.connect_for_operation(
                S.SCRIPT_OPERATION, lambda: self._start(item)
            )
        return self._start(item)

    def _start(self, item: QueueItem) -> Future[Any] | None:
        run = item.script
        device = self._project.find_device_by_node_id(item.node_id)
        if run is None or device is None:
            return None
        conn = self._connection
        if conn.not_connected(S.SCRIPT_OPERATION) or conn.xknx is None:
            item.message = S.SCRIPT_NOT_CONNECTED
            return None
        if not device.individual_address:
            item.message = S.BUTTON_NO_VALID_ADDRESS
            self._fail(device, run, S.BUTTON_NO_VALID_ADDRESS)
            return None
        if device.script_running or not conn.begin_operation(
            "script", device.individual_address
        ):
            return None
        old_active = self._project.parameter_driven_com_objects(device)
        ui = device.begin_script()
        if ui is None:
            device.end_script()
            conn.end_operation()
            return None
        mask = _mask_version(device)
        session = OnlineSession(
            conn.xknx,
            IndividualAddress(device.individual_address),
            security=conn.security_for(device),
            connectionless=run.button.online == "ConnectionLess",
            mask_version=mask,
            interface_max_apdu_length=conn.interface_max_apdu_length,
            locale=ui.script_env.locale if ui.script_env is not None else None,
            object_types=_object_types(conn, device),
        )
        online = OnlineHost(session, self._run_coroutine, run.abort)
        node_id = device.node_id
        # The project this operation belongs to. Its persistence runs later on the GUI thread, by
        # which point the user may have opened a different project; the token lets those writes be
        # dropped instead of landing in the wrong project.
        generation = self._project.generation

        def commit(changes: ChangeSet, description: str | None) -> None:
            self._submit(
                lambda: self._project.persist_script_changes(
                    node_id, changes, description, generation=generation
                )
            )

        access = ParameterAccess(ui, on_commit=commit)
        button = run.button

        def job() -> None:
            try:
                run_button(
                    ui,
                    button,
                    access,
                    online=online.functions(),
                    progress=self._progress(run),
                    abort=run.abort,
                    on_log=self._script_log,
                )
            finally:
                access.rollback()
                with contextlib.suppress(Exception):
                    self._run_coroutine(session.disconnect()).result(_DISPOSE_TIMEOUT)

        device.param_errors.pop(button.id, None)
        future = default_worker().submit(job)
        future.add_done_callback(
            lambda f: self._submit(
                lambda: self._finish(device, run, f, old_active, generation)
            )
        )
        return future

    def _run_coroutine(self, coro: Any) -> Future[Any]:
        future = self._connection.run_async(coro)
        if future is None:
            raise ScriptError(S.SCRIPT_NOT_CONNECTED)
        return future

    def _progress(self, run: ScriptRun) -> dict[str, HostFunction]:
        def set_progress(value: float) -> None:
            run.progress = value

        def set_text(text: str) -> None:
            run.text = text

        def canceled() -> bool:
            return run.canceled

        return {"g.progress": set_progress, "g.text": set_text, "g.canceled": canceled}

    def _script_log(self, level: str, message: str) -> None:
        self._log.info("script log", level=level, message=message)

    def _fail(self, device: Device, run: ScriptRun, message: str) -> None:
        device.param_errors[run.button.id] = message
        self._log.warning(
            "parameter script failed", button=run.button.id, error=message
        )
        if self._notify is not None:
            self._notify(f"{S.SCRIPT_OPERATION}: {message}")

    def _finish(
        self,
        device: Device,
        run: ScriptRun,
        future: Future[None],
        old_active: set[str],
        generation: int,
    ) -> None:
        self._connection.end_operation()
        device.end_script()
        self._project.finish_script_changes(
            device.node_id, old_active, generation=generation
        )
        device = self._project.find_device_by_node_id(device.node_id) or device
        error = future.exception()
        if isinstance(error, ScriptAborted):
            self._fail(device, run, S.SCRIPT_ABORTED)
        elif isinstance(error, ScriptError):
            self._fail(device, run, error.message)
        elif error is not None:
            self._fail(device, run, str(error) or type(error).__name__)

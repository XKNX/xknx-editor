"""Button event handlers ``fn(device, online, progress, context)`` on a live DynamicUI."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from .errors import (
    CLASS_NOT_AUTOMATION,
    COR_E_EXCEPTION,
    COR_E_KEYNOTFOUND,
    COR_E_TARGETINVOCATION,
    HostError,
)
from .json_compat import loads_lenient
from .sandbox import AbortToken, HostFunction, ScriptContext
from .texts import dotnet_text
from .values import ScriptValueError, coerce_script_value, to_js

if TYPE_CHECKING:
    from ..parser_v2.calculation import ChangeSet
    from ..parser_v2.dynamic import DynamicUI
    from ..parser_v2.ui import UiButton

log = logging.getLogger(__name__)


def merge_changes(into: ChangeSet, changes: ChangeSet) -> None:
    for ref_id, (old, new) in changes.items():
        into[ref_id] = (into[ref_id][0] if ref_id in into else old, new)


def _net(changes: ChangeSet) -> ChangeSet:
    return {ref: change for ref, change in changes.items() if change[0] != change[1]}


class ParameterAccess:
    """Parameter reads and writes of one handler run, with ``withUndo`` groups.

    Writes outside a group are passed to ``on_commit`` right away (``None`` description); a
    committed outermost group is passed as one change with its description. Without ``on_commit``
    every change is kept in :attr:`changes` until :meth:`rollback` or the caller persists it."""

    def __init__(
        self,
        ui: DynamicUI,
        *,
        on_commit: Callable[[ChangeSet, str | None], None] | None = None,
    ) -> None:
        self._ui = ui
        self._on_commit = on_commit
        self._groups: list[tuple[str, ChangeSet]] = []
        self.changes: ChangeSet = {}

    def _record(self, changes: ChangeSet, description: str | None) -> None:
        if self._groups:
            merge_changes(self._groups[-1][1], changes)
            return
        merge_changes(self.changes, changes)
        if self._on_commit is not None and _net(changes):
            self._on_commit(_net(changes), description)

    def _revert(self, changes: ChangeSet) -> None:
        self._ui.apply_parameter_values({ref: old for ref, (old, _) in changes.items()})

    def rollback(self) -> None:
        """Revert every change not yet handed to ``on_commit``."""
        while self._groups:
            self._revert(self._groups.pop()[1])
        if self._on_commit is None:
            self._revert(self.changes)
            self.changes = {}

    @property
    def _locale(self) -> str | None:
        env = self._ui.script_env
        return env.locale if env is not None else None

    def _find(self, kind: str, scope: str | None, key: Any) -> str:
        ref = self._ui.find_parameter_ref(kind, key, scope)
        if ref is None:
            raise HostError(
                dotnet_text("key_not_found", self._locale), number=COR_E_KEYNOTFOUND
            )
        return ref

    def _get(self, ref: str) -> Any:
        local = self._ui.local_ref_id(ref)
        return to_js(self._ui.get_value(ref), self._ui.indexer.type_of(local))

    def _set(self, ref: str, value: Any) -> None:
        local = self._ui.local_ref_id(ref)
        try:
            text = coerce_script_value(
                value,
                self._ui.indexer.type_of(local),
                locale=self._locale,
                text_encoding=self._ui.text_encoding,
            )
            changes = self._ui.write_parameter(ref, text)
        except ScriptValueError as exc:
            raise HostError(exc.message, number=exc.number) from exc
        except ValueError as exc:
            raise HostError(str(exc), number=COR_E_EXCEPTION) from exc
        self._record(changes, None)

    def _undo_begin(self, description: str) -> None:
        self._groups.append((description, {}))

    def _undo_rollback(self) -> None:
        if self._groups:
            self._revert(self._groups.pop()[1])
        raise HostError(
            dotnet_text("target_invocation", self._locale),
            number=COR_E_TARGETINVOCATION,
        )

    def _undo_commit(self) -> None:
        if self._groups:
            description, changes = self._groups.pop()
            self._record(changes, description)

    def _by_name(self, scope: str | None, name: Any) -> str:
        return self._find("name", scope, name)

    def _by_id(self, scope: str | None, ref_id: Any) -> str:
        return self._find("id", scope, ref_id)

    def _by_number(self, scope: str | None, number: Any) -> str:
        return self._find("number", scope, number)

    def _message(self, key: Any) -> str | None:
        try:
            number = int(float(str(key)))
        except ValueError:
            raise HostError(
                "Class doesn't support Automation", number=CLASS_NOT_AUTOMATION
            ) from None
        return self._ui.indexer.message_text(number)

    def _app_name(self) -> str:
        return self._ui.application_name

    def _ref(self, ref: str) -> str:
        return ref

    def _name(self, ref: str) -> str | None:
        return self._ui.indexer.parameter_name(self._ui.local_ref_id(ref))

    def host_functions(self) -> dict[str, HostFunction]:
        return {
            "d.byName": self._by_name,
            "d.byId": self._by_id,
            "d.byNumber": self._by_number,
            "d.message": self._message,
            "d.appName": self._app_name,
            "d.undoBegin": self._undo_begin,
            "d.undoRollback": self._undo_rollback,
            "d.undoCommit": self._undo_commit,
            "p.get": self._get,
            "p.set": self._set,
            "p.active": self._ui.is_parameter_active,
            "p.ref": self._ref,
            "p.name": self._name,
        }


def _context_object(button: UiButton) -> dict[str, Any]:
    if not button.handler_parameters:
        return {}
    try:
        value = loads_lenient(button.handler_parameters)
    except ValueError:
        log.warning(
            "invalid event handler parameters in %s: %r",
            button.id,
            button.handler_parameters,
        )
        return {}
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def run_button(
    ui: DynamicUI,
    button: UiButton,
    access: ParameterAccess,
    *,
    online: Mapping[str, HostFunction] | None = None,
    progress: Mapping[str, HostFunction] | None = None,
    abort: AbortToken | None = None,
    on_log: Callable[[str, str], None] | None = None,
) -> None:
    """Call the button's handler in a fresh script context."""
    if not button.handler:
        return
    host: dict[str, HostFunction] = dict(access.host_functions())
    host.update(online or {})
    host.update(progress or {})
    previous = ui.script_abort
    ui.script_abort = abort
    try:
        ctx = ScriptContext(
            script=ui.indexer.script or "",
            host=host,
            abort=abort,
            on_log=on_log,
            jscript=ui.script_env if ui.script_env is not None else True,
        )
        ctx.invoke(
            button.handler,
            [
                {"$host": "device", "scope": button.module_instance_id},
                {"$host": "online" if online is not None else "undefined"},
                {"$host": "progress" if progress is not None else "undefined"},
                _context_object(button),
            ],
        )
    finally:
        ui.script_abort = previous


def run_offline_button(
    ui: DynamicUI,
    button: UiButton,
    *,
    abort: AbortToken | None = None,
    on_log: Callable[[str, str], None] | None = None,
) -> ChangeSet:
    """Run an offline handler as one transaction: its changes, or nothing if it fails."""
    access = ParameterAccess(ui)
    try:
        run_button(ui, button, access, abort=abort, on_log=on_log)
    except BaseException:
        access.rollback()
        raise
    return _net(access.changes)


__all__ = [
    "ParameterAccess",
    "merge_changes",
    "run_button",
    "run_offline_button",
]

"""ParameterCalculation and ParameterValidation execution."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from xknxeditor.namespaces.intermediate.parameter_calculation_t import (
    ParameterCalculation,
)
from xknxeditor.namespaces.intermediate.parameter_calculation_t_language import (
    ParameterCalculationLanguage,
)

from ...script import (
    CalculationError,
    ParameterValidationError,
    ScriptAborted,
    ScriptContext,
    ScriptError,
)
from ...script.json_compat import loads_lenient
from ...script.values import ScriptValueError, coerce_script_value, to_js

if TYPE_CHECKING:
    from ...script.compat.runtime import JScriptEnv
    from ...script.sandbox import AbortToken
    from ..application_indexer import ApplicationIndexer

log = logging.getLogger(__name__)

ChangeSet = dict[str, tuple[str | None, str | None]]

# Calculations and validations run synchronously on an edit and cannot be canceled by the user, so a
# runaway script is stopped by this wall-clock limit (enforced via the immutable loop-abort hook).
_SCRIPT_DEADLINE = 5.0

_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


@dataclass
class Journal:
    """Values written during one change, with their previous explicit values."""

    read: Callable[[str], str | None]
    explicit: Callable[[str], str | None]
    write: Callable[[str, str | None], None]
    before: dict[str, tuple[str | None, str | None]] = field(
        default_factory=dict[str, tuple[str | None, str | None]]
    )

    def set(self, ref_id: str, value: str) -> None:
        if ref_id not in self.before:
            self.before[ref_id] = (self.read(ref_id), self.explicit(ref_id))
        self.write(ref_id, value)

    def rollback(self) -> None:
        for ref_id, (_, explicit) in reversed(self.before.items()):
            self.write(ref_id, explicit)
        self.before.clear()

    def changes(self) -> ChangeSet:
        out: ChangeSet = {}
        for ref_id, (old, _) in self.before.items():
            new = self.read(ref_id)
            if new != old:
                out[ref_id] = (old, new)
        return out


@dataclass
class CalculationScope:
    """Parameter access inside the scope (application or module instance) of a change."""

    get: Callable[[str], str | None]
    qualify: Callable[[str], str]
    locale: str | None = None
    text_encoding: str = "latin-1"


def _alias(idx: ApplicationIndexer, pr: Any) -> str:
    return pr.alias_name or idx.parameter_name(pr.ref_id) or pr.ref_id


def _as_dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _context_object(text: str | None, calc_id: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = loads_lenient(text)
    except ValueError:
        log.warning("invalid transformation parameters in %s: %r", calc_id, text)
        return {}
    return _as_dict(value)


_vbscript_warned: set[str] = set()


def run_calculations(
    idx: ApplicationIndexer,
    local_ref: str,
    scope: CalculationScope,
    journal: Journal,
    env: JScriptEnv | None,
    *,
    raise_errors: bool,
    abort: AbortToken | None = None,
) -> None:
    """Run the calculation plan a change of ``local_ref`` triggers."""
    run_calculations_many(
        idx, (local_ref,), scope, journal, env, raise_errors=raise_errors, abort=abort
    )


def run_calculations_many(
    idx: ApplicationIndexer,
    local_refs: Iterable[str],
    scope: CalculationScope,
    journal: Journal,
    env: JScriptEnv | None,
    *,
    raise_errors: bool,
    abort: AbortToken | None = None,
) -> None:
    """Run the union calculation plan of a batch of changed parameters, each calculation once."""
    for calc, direction in idx.calculation_plan_many(local_refs):
        try:
            _run_one(idx, calc, direction, scope, journal, env, abort)
        except CalculationError:
            if raise_errors:
                raise
            log.warning("parameter calculation %s failed", calc.id, exc_info=True)


def _run_one(
    idx: ApplicationIndexer,
    calc: ParameterCalculation,
    direction: str,
    scope: CalculationScope,
    journal: Journal,
    env: JScriptEnv | None,
    abort: AbortToken | None,
) -> None:
    if calc.language == ParameterCalculationLanguage.VBSCRIPT:
        if calc.id not in _vbscript_warned:
            _vbscript_warned.add(calc.id)
            log.warning("VBScript parameter calculation %s is not supported", calc.id)
        return
    lr = direction == "LR"
    ins = (calc.lparameters if lr else calc.rparameters).parameter_ref_ref
    outs = (calc.rparameters if lr else calc.lparameters).parameter_ref_ref
    func = calc.lrtransformation_func if lr else calc.rltransformation_func
    inline = calc.lrtransformation if lr else calc.rltransformation
    params = (
        calc.lrtransformation_parameters if lr else calc.rltransformation_parameters
    )
    inputs = {
        _alias(idx, pr): to_js(scope.get(pr.ref_id), idx.type_of(pr.ref_id))
        for pr in ins
    }
    output = {
        _alias(idx, pr): to_js(scope.get(pr.ref_id), idx.type_of(pr.ref_id))
        for pr in outs
    }
    host = {"a.message": idx.message_text}
    jscript: Any = env if env is not None else True
    try:
        if func:
            ctx = ScriptContext(
                script=idx.script or "",
                get_message=True,
                host=host,
                jscript=jscript,
                abort=abort,
                deadline=_SCRIPT_DEADLINE,
            )
            result = ctx.invoke(
                func, [inputs, output, _context_object(params, calc.id)], readback=[1]
            )
            computed = _as_dict(result.readback[0])
        elif inline:
            computed = _run_inline(inline, inputs, output, host, jscript, abort)
        else:
            return
    except ScriptAborted:
        raise
    except ScriptError as exc:
        raise CalculationError(exc.message, calculation_id=calc.id) from exc
    for pr in outs:
        alias = _alias(idx, pr)
        if alias not in computed or computed[alias] is None:
            continue
        try:
            value = coerce_script_value(
                computed[alias],
                idx.type_of(pr.ref_id),
                locale=scope.locale,
                text_encoding=scope.text_encoding,
            )
        except ScriptValueError as exc:
            raise CalculationError(exc.message, calculation_id=calc.id) from exc
        except ValueError as exc:
            raise CalculationError(str(exc), calculation_id=calc.id) from exc
        if value != scope.get(pr.ref_id):
            journal.set(scope.qualify(pr.ref_id), value)


def _run_inline(
    code: str,
    inputs: dict[str, Any],
    output: dict[str, Any],
    host: dict[str, Any],
    jscript: Any,
    abort: AbortToken | None,
) -> dict[str, Any]:
    names = {**output, **inputs}
    decls = "".join(
        f"var {name} = {json.dumps(value)};"
        for name, value in names.items()
        if _IDENT.match(name)
    )
    reader = ",".join(
        f"{json.dumps(name)}: {name}" for name in output if _IDENT.match(name)
    )
    script = (
        decls + "\n" + code + "\nfunction __xk_outputs() { return {" + reader + "}; }\n"
    )
    ctx = ScriptContext(
        script=script,
        get_message=True,
        host=host,
        jscript=jscript,
        abort=abort,
        deadline=_SCRIPT_DEADLINE,
    )
    value = ctx.invoke("__xk_outputs", []).value
    return _as_dict(value)


VALIDATION_FAILED = "Parameter value cannot be set, because validation failed."


def run_validations(
    idx: ApplicationIndexer,
    local_ref: str,
    value: str,
    scope: CalculationScope,
    env: JScriptEnv | None,
    abort: AbortToken | None = None,
) -> None:
    """Run every ParameterValidation containing ``local_ref`` against the proposed ``value``."""
    for validation in idx.validations_for(local_ref):
        refs = validation.parameters.parameter_ref_ref
        changed = next((_alias(idx, pr) for pr in refs if pr.ref_id == local_ref), "")
        new_value = to_js(value, idx.type_of(local_ref))
        previous = to_js(scope.get(local_ref), idx.type_of(local_ref))
        inputs = {
            _alias(idx, pr): new_value
            if pr.ref_id == local_ref
            else to_js(scope.get(pr.ref_id), idx.type_of(pr.ref_id))
            for pr in refs
        }
        context = _context_object(validation.validation_parameters, validation.id)
        jscript: Any = env if env is not None else True
        try:
            ctx = ScriptContext(
                script=idx.script or "",
                get_message=True,
                host={"a.message": idx.message_text},
                jscript=jscript,
                abort=abort,
                deadline=_SCRIPT_DEADLINE,
            )
            result = ctx.invoke(
                validation.validation_func, [inputs, changed, previous, context]
            ).value
        except ScriptAborted:
            raise
        except ScriptError as exc:
            log.warning("parameter validation %s failed: %s", validation.id, exc)
            raise ParameterValidationError(VALIDATION_FAILED, ref_id=local_ref) from exc
        if isinstance(result, str):
            raise ParameterValidationError(
                result or VALIDATION_FAILED, ref_id=local_ref
            )
        if _accepts(result):
            continue
        raise ParameterValidationError(VALIDATION_FAILED, ref_id=local_ref)


def _accepts(result: object) -> bool:
    """No result, ``true`` or a non-zero number accepts; anything else rejects."""
    if result is None or isinstance(result, bool):
        return result is None or result
    if isinstance(result, (int, float)):
        return result != 0
    return False


__all__ = [
    "VALIDATION_FAILED",
    "CalculationScope",
    "ChangeSet",
    "Journal",
    "run_calculations",
    "run_calculations_many",
    "run_validations",
]

"""ParameterCalculation and ParameterValidation execution."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import TYPE_CHECKING, Any, cast

from xknxeditor.namespaces.intermediate.parameter_calculation_t import (
    ParameterCalculation,
)
from xknxeditor.namespaces.intermediate.parameter_calculation_t_language import (
    ParameterCalculationLanguage,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_float import (
    ParameterTypeTypeFloat,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_number import (
    ParameterTypeTypeNumber,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction import (
    ParameterTypeTypeRestriction,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_time import (
    ParameterTypeTypeTime,
)

from ...script import (
    CalculationError,
    ParameterValidationError,
    ScriptAborted,
    ScriptContext,
    ScriptError,
)
from ...script.literal import parse_literal
from ...script.values import (
    ScriptValueError,
    coerce_script_value,
    to_js,
    to_vbscript,
)
from ...script.vbscript import culture_for, run_script

if TYPE_CHECKING:
    from ...script.compat.runtime import JScriptEnv
    from ...script.sandbox import AbortToken
    from ..application_indexer import ApplicationIndexer

log = logging.getLogger(__name__)
# Calculation failures already logged (calculation id, error): a product's broken calculation
# fails again on every recalculation (each recovery round, each edit), logged once.
_failures_logged: set[tuple[str, str]] = set()

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

    def note(self, ref_id: str, old: str | None, explicit: str | None) -> None:
        """Record a value the change wrote without the journal (an Assign), with its value
        (``old``) and explicit value before the change; a value journaled already keeps
        what it had before."""
        self.before.setdefault(ref_id, (old, explicit))

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
        value = parse_literal(text)
    except ValueError:
        log.warning("invalid transformation parameters in %s: %r", calc_id, text)
        return {}
    return _as_dict(value)


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
    stored: Collection[str] | None = None,
) -> None:
    """Run the union calculation plan of a batch of changed parameters, each calculation once.

    Without ``raise_errors`` a failing calculation is logged, and an output it computes that its
    parameter does not take is left out (the other outputs are written). ``stored``: those of
    ``local_refs`` whose values were read from a device (and are downloaded); where the RL
    transformation of a calculation they are on the R side of does not give L values its LR
    transformation gives them back from, such L values are searched (see :func:`_match_stored`)."""
    refs = tuple(dict.fromkeys(local_refs))
    for calc, direction in idx.calculation_plan_many(refs):
        # What the RL transformation fails to compute is searched for below: not a failure yet.
        matching = stored is not None and direction == "RL"
        try:
            _run_one(
                idx,
                calc,
                direction,
                scope,
                journal,
                env,
                abort,
                strict=raise_errors,
                report=not matching,
            )
        except CalculationError as exc:
            if raise_errors:
                raise
            if not matching:
                _log_failure(calc.id, exc)
        if stored is None or not matching:
            continue
        try:
            unmatched = _match_stored(idx, calc, stored, scope, journal, env, abort)
        except CalculationError as exc:
            _log_failure(calc.id, exc)
            continue
        if unmatched and (calc.id, str(unmatched)) not in _failures_logged:
            _failures_logged.add((calc.id, str(unmatched)))
            log.warning(
                "parameter calculation %s gives no settings for the stored values %s",
                calc.id,
                ", ".join(f"{ref}={scope.get(ref)}" for ref in unmatched),
            )


def _log_failure(calc_id: str, exc: CalculationError) -> None:
    if (calc_id, str(exc)) not in _failures_logged:
        _failures_logged.add((calc_id, str(exc)))
        log.warning("parameter calculation %s failed", calc_id, exc_info=exc)


def _run_one(
    idx: ApplicationIndexer,
    calc: ParameterCalculation,
    direction: str,
    scope: CalculationScope,
    journal: Journal,
    env: JScriptEnv | None,
    abort: AbortToken | None,
    *,
    strict: bool = True,
    report: bool = True,
) -> None:
    computed = _compute(idx, calc, direction, scope, env, abort)
    if computed is not None:
        _write_outputs(
            idx,
            calc,
            direction,
            computed,
            scope,
            journal,
            strict=strict,
            report=report,
        )


def _sides(calc: ParameterCalculation, direction: str) -> tuple[list[Any], list[Any]]:
    """The input and output ParameterRefRefs of ``calc`` in ``direction``."""
    left = calc.lparameters.parameter_ref_ref
    right = calc.rparameters.parameter_ref_ref
    return (left, right) if direction == "LR" else (right, left)


def _compute(
    idx: ApplicationIndexer,
    calc: ParameterCalculation,
    direction: str,
    scope: CalculationScope,
    env: JScriptEnv | None,
    abort: AbortToken | None,
    trial: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """What ``calc`` computes in ``direction``, by output alias (None: no code for it), with the
    values of ``trial`` (by local ref) in place of the current ones.

    The code sees nothing but the values of its parameters (and the product's script and
    messages), so what it computed from the same values is computed once per application (with
    the script environment of the system, ``env`` None); a search of a device's settings runs the
    same calculations on the same values again and again."""

    def get(ref_id: str) -> str | None:
        if trial is not None and ref_id in trial:
            return trial[ref_id]
        return scope.get(ref_id)

    ins, outs = _sides(calc, direction)
    if env is not None:
        return _compute_now(idx, calc, direction, scope, env, abort, get)
    key = (
        _COMPUTED,
        calc.id,
        direction,
        scope.locale,
        tuple(get(pr.ref_id) for pr in ins),
        tuple(get(pr.ref_id) for pr in outs),
    )
    results = idx.calculation_results
    found = results.get(key, _NOT_COMPUTED)
    if found is not _NOT_COMPUTED:
        cached = cast("dict[str, Any] | None", found)
        return dict(cached) if cached is not None else None
    computed = _compute_now(idx, calc, direction, scope, env, abort, get)
    if len(results) >= _MAX_RESULTS:
        results.clear()
    results[key] = dict(computed) if computed is not None else None
    return computed


# Marks the results of _compute among the calculation results an indexer keeps, and stands
# for one not computed yet.
_COMPUTED = "_compute"
_NOT_COMPUTED = object()


def _compute_now(
    idx: ApplicationIndexer,
    calc: ParameterCalculation,
    direction: str,
    scope: CalculationScope,
    env: JScriptEnv | None,
    abort: AbortToken | None,
    get: Callable[[str], str | None],
) -> dict[str, Any] | None:
    """:func:`_compute`, run: the values of the parameters read with ``get``."""
    lr = direction == "LR"
    ins, outs = _sides(calc, direction)
    if calc.language == ParameterCalculationLanguage.VBSCRIPT:
        code = calc.lrtransformation if lr else calc.rltransformation
        if not code:
            return None
        return _run_vbscript(idx, calc, code, ins, outs, get, scope.locale, abort)
    func = calc.lrtransformation_func if lr else calc.rltransformation_func
    inline = calc.lrtransformation if lr else calc.rltransformation
    params = (
        calc.lrtransformation_parameters if lr else calc.rltransformation_parameters
    )
    inputs = {
        _alias(idx, pr): to_js(get(pr.ref_id), idx.type_of(pr.ref_id)) for pr in ins
    }
    output = {
        _alias(idx, pr): to_js(get(pr.ref_id), idx.type_of(pr.ref_id)) for pr in outs
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
                values=idx.message_values(),
                deadline=_SCRIPT_DEADLINE,
            )
            result = ctx.invoke(
                func, [inputs, output, _context_object(params, calc.id)], readback=[1]
            )
            return _as_dict(result.readback[0])
        if inline:
            return _run_inline(
                inline, inputs, output, host, jscript, abort, idx.message_values()
            )
        return None
    except ScriptAborted:
        raise
    except ScriptError as exc:
        raise CalculationError(exc.message, calculation_id=calc.id) from exc


def _run_vbscript(
    idx: ApplicationIndexer,
    calc: ParameterCalculation,
    code: str,
    ins: list[Any],
    outs: list[Any],
    get: Callable[[str], str | None],
    locale: str | None,
    abort: AbortToken | None,
) -> dict[str, Any]:
    """Run inline VBScript code; the aliases are its variables, the outputs hold their current
    values when it starts."""
    inputs = {
        _alias(idx, pr): to_vbscript(get(pr.ref_id), idx.type_of(pr.ref_id))
        for pr in ins
    }
    current = {
        _alias(idx, pr): to_vbscript(get(pr.ref_id), idx.type_of(pr.ref_id))
        for pr in outs
    }
    try:
        return run_script(
            code,
            inputs,
            current,
            culture=culture_for(locale),
            abort=abort,
            deadline=_SCRIPT_DEADLINE,
        )
    except ScriptAborted:
        raise
    except ScriptError as exc:
        raise CalculationError(exc.message, calculation_id=calc.id) from exc


def _coerced_outputs(
    idx: ApplicationIndexer,
    calc: ParameterCalculation,
    direction: str,
    computed: Mapping[str, Any],
    scope: CalculationScope,
) -> dict[str, str | CalculationError]:
    """The computed outputs as values of their parameters, by local ref; the error for a value
    its parameter does not take."""
    result: dict[str, str | CalculationError] = {}
    for pr in _sides(calc, direction)[1]:
        alias = _alias(idx, pr)
        if alias not in computed or computed[alias] is None:
            continue
        try:
            result[pr.ref_id] = coerce_script_value(
                computed[alias],
                idx.type_of(pr.ref_id),
                locale=scope.locale,
                text_encoding=scope.text_encoding,
            )
        except ScriptValueError as exc:
            result[pr.ref_id] = CalculationError(exc.message, calculation_id=calc.id)
        except ValueError as exc:
            result[pr.ref_id] = CalculationError(str(exc), calculation_id=calc.id)
    return result


def _write_outputs(
    idx: ApplicationIndexer,
    calc: ParameterCalculation,
    direction: str,
    computed: Mapping[str, Any],
    scope: CalculationScope,
    journal: Journal,
    *,
    strict: bool = True,
    report: bool = True,
) -> None:
    """Coerce the computed outputs to their parameter types and write the changed ones.

    ``strict``: an output its parameter does not take fails the calculation before anything is
    written. Otherwise that output is left out (logged with ``report``), and the others are
    written."""
    values = _coerced_outputs(idx, calc, direction, computed, scope)
    errors = [v for v in values.values() if isinstance(v, CalculationError)]
    if strict and errors:
        raise errors[0]
    for ref_id, value in values.items():
        if isinstance(value, CalculationError):
            if report:
                _log_failure(calc.id, value)
        elif value != scope.get(ref_id):
            journal.set(scope.qualify(ref_id), value)


# Most values tried for one L parameter in the search of _match_stored, and most results it
# keeps for the next search.
_MAX_CANDIDATES = 256
_MAX_RESULTS = 100_000
# Fewer other values than this make a parameter one the search of _match_stored also tries
# together with another such one (switches, choices).
_PAIR_CANDIDATES = 8
# Most pairs of values that search tries for one calculation at a time.
_PAIR_TRIALS = 64


def _match_stored(
    idx: ApplicationIndexer,
    calc: ParameterCalculation,
    stored: Collection[str],
    scope: CalculationScope,
    journal: Journal,
    env: JScriptEnv | None,
    abort: AbortToken | None,
) -> list[str]:
    """Set the L parameters of ``calc`` to values its LR transformation gives the ``stored`` R
    values back from, where the current L values (what its RL transformation computed) do not.

    A product may have no RL transformation, or one that is not the inverse of the LR one a
    download writes the R values with. Each L parameter that changes one of the R values not
    given back (seen by trying two other values of it) is tried with every value of its domain,
    when that is small; the value that gives the most R values back is taken when it gives more
    than before. Where that leaves R values not given back, two L parameters with few values
    each are tried together (two switches that both have to be on for a time to be downloaded),
    and the single ones again after them. An R value no L value gives back (one the product
    cannot download) is left as it is; those are returned. The R values not ``stored`` take
    what the LR transformation computes from the L values, as an edit of them would set them."""
    if not (calc.lrtransformation_func or calc.lrtransformation):
        return []
    targets: dict[str, str] = {}
    for pr in calc.rparameters.parameter_ref_ref:
        value = scope.get(pr.ref_id)
        if pr.ref_id in stored and value is not None:
            targets[pr.ref_id] = value
    if not targets:
        return []

    sides = [
        pr.ref_id
        for pr in (
            *calc.lparameters.parameter_ref_ref,
            *calc.rparameters.parameter_ref_ref,
        )
    ]

    def evaluate(trial: Mapping[str, str]) -> dict[str, str | CalculationError]:
        key = (
            calc.id,
            scope.locale,
            tuple(trial.get(ref, scope.get(ref)) for ref in sides),
        )
        cached = idx.calculation_results.get(key)
        if cached is None:
            computed = _compute(idx, calc, "LR", scope, env, abort, trial)
            cached = _coerced_outputs(idx, calc, "LR", computed or {}, scope)
            if len(idx.calculation_results) >= _MAX_RESULTS:
                idx.calculation_results.clear()
            idx.calculation_results[key] = cached
        return cast("dict[str, str | CalculationError]", cached)

    def given_back(got: Mapping[str, str | CalculationError]) -> set[str]:
        return {
            ref
            for ref, want in targets.items()
            if isinstance(value := got.get(ref), str) and _same_value(value, want)
        }

    def write_unstored(got: Mapping[str, str | CalculationError]) -> None:
        # The R values not read follow the L values, as an edit of them sets them (a switch
        # computed from them that shows or hides other settings).
        for ref, value in got.items():
            if (
                ref not in stored
                and isinstance(value, str)
                and not _same_value(value, scope.get(ref) or "")
            ):
                journal.set(scope.qualify(ref), value)

    current: dict[str, str] = {}
    for pr in calc.lparameters.parameter_ref_ref:
        value = scope.get(pr.ref_id)
        if value is not None:
            current[pr.ref_id] = value
    base = evaluate(current)
    matched = given_back(base)
    if matched == set(targets):
        write_unstored(base)
        return []
    chosen = dict(current)

    def search_singles() -> None:
        nonlocal matched, base
        for ref in current:
            candidates = _candidates(idx.type_of(ref), chosen[ref])
            if not candidates:
                continue
            missing = set(targets) - matched
            affects = set[str]()
            for probe in dict.fromkeys((candidates[0], candidates[-1])):
                got = evaluate({**chosen, ref: probe})
                affects |= {r for r in targets if str(got.get(r)) != str(base.get(r))}
            if not affects & missing:
                continue
            best, best_matched = chosen[ref], matched
            for candidate in candidates:
                same = given_back(evaluate({**chosen, ref: candidate}))
                if len(same) > len(best_matched):
                    best, best_matched = candidate, same
            if best != chosen[ref]:
                chosen[ref] = best
                matched = best_matched
                base = evaluate(chosen)
                if matched == set(targets):
                    return

    def search_pairs() -> bool:
        """Two settings with few values each that change the R values only together (two
        switches that both have to be on), tried together: whether a pair gives more back."""
        nonlocal matched, base
        few = [
            (ref, candidates)
            for ref in current
            if (candidates := _candidates(idx.type_of(ref), chosen[ref]))
            and len(candidates) < _PAIR_CANDIDATES
        ]
        best: tuple[tuple[str, str], tuple[str, str]] | None = None
        best_matched = matched
        pairs = (
            ((first, a), (second, b))
            for n, (first, first_values) in enumerate(few)
            for second, second_values in few[n + 1 :]
            for a in first_values
            for b in second_values
        )
        # A calculation over many such settings (a channel's worth of switches) is not
        # searched pair by pair: each trial runs its script.
        for pair in islice(pairs, _PAIR_TRIALS):
            same = given_back(evaluate({**chosen, **dict(pair)}))
            if len(same) > len(best_matched):
                best, best_matched = pair, same
        if best is None:
            return False
        for ref, value in best:
            chosen[ref] = value
        matched = best_matched
        base = evaluate(chosen)
        return True

    search_singles()
    while matched != set(targets) and search_pairs():
        search_singles()
    for ref, value in chosen.items():
        if value != current[ref]:
            journal.set(scope.qualify(ref), value)
    write_unstored(base)
    return [ref for ref in targets if ref not in matched]


def _same_value(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def _candidates(tc: object, current: str) -> list[str]:
    """Every value but ``current`` of a parameter type with a small domain (choices, an integer
    range, a float range with an increment); empty for a larger domain."""
    values: list[str] = []
    if isinstance(tc, ParameterTypeTypeRestriction):
        values = [str(e.value) for e in tc.enumeration]
    elif isinstance(tc, ParameterTypeTypeNumber | ParameterTypeTypeTime):
        low, high = int(tc.min_inclusive), int(tc.max_inclusive)
        if high - low >= _MAX_CANDIDATES:
            return []
        values = [str(n) for n in range(low, high + 1)]
    elif isinstance(tc, ParameterTypeTypeFloat):
        step = tc.increment
        if not step or step <= 0:
            return []
        count = round((tc.max_inclusive - tc.min_inclusive) / step)
        if count >= _MAX_CANDIDATES:
            return []
        for i in range(count + 1):
            f = round(tc.min_inclusive + i * step, 10)
            if f <= tc.max_inclusive:
                values.append(str(int(f)) if f.is_integer() else repr(f))
    return [v for v in values if not _same_value(v, current)]


def _run_inline(
    code: str,
    inputs: dict[str, Any],
    output: dict[str, Any],
    host: dict[str, Any],
    jscript: Any,
    abort: AbortToken | None,
    values: dict[str, str],
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
        values=values,
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
                values=idx.message_values(),
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

"""Which parameter values show a parameter the current configuration hides.

A parameter is shown when every choose on its way selects the branch it sits in, and a repeat
runs often enough to reach it. Working backwards from the parameter, :func:`plan_activation` picks
a value for each gate whose current value selects another branch, first showing that gate's own
parameter the same way. Every plan is applied to a copy of the device's state before it is
returned, through the same edit path a user's change takes, so a returned plan shows the parameter
(and takes the value, when one is given).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
    ComObjectInstanceRef,
)

from .nodes.choose import satisfies
from .ui import UiNode, UiParameter, UiParameterBlock, UiTab
from .ui.parameter import (
    CheckBoxWidget,
    EnumWidget,
    NumberSliderWidget,
    NumberWidget,
    TimeWidget,
)

if TYPE_CHECKING:
    from .context import Gate, Placement
    from .dynamic import DynamicUI

# How deep gates may nest through other hidden gates, how many plans are built and how many of
# them are tried on a copy of the device's state.
_MAX_DEPTH = 8
_MAX_PLANS = 24
_MAX_TRIES = 8
# How many values of calculation inputs are tried per plan, and how many values of one number
# input (a larger range is tried at its ends and around its current value).
_MAX_TRIALS = 48
_MAX_RANGE = 16
_NUMBER = re.compile(r"-?\d+")


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One edit of a plan: ``value`` for the parameter ``ref_id``, shown under ``sections``.
    ``label`` is the parameter's text, or its name when the widget shows none."""

    ref_id: str
    value: str
    parameter: UiParameter
    sections: tuple[str, ...]
    label: str

    @property
    def value_label(self) -> str | None:
        """The text of an enumeration's ``value``."""
        return value_label(self.parameter, self.value)


@dataclass(frozen=True, slots=True)
class ActivationPlan:
    """The edits, in order, that show ``target`` (and set it to ``value``, when one was asked).

    ``steps`` ends with the target itself when a value was asked. ``shown`` and ``hidden`` are the
    other parameters the plan shows or hides."""

    target: str
    value: str | None
    steps: tuple[PlanStep, ...]
    shown: tuple[str, ...]
    hidden: tuple[str, ...]


class ActivationError(ValueError):
    """No plan shows the parameter; the message says why."""


def value_label(parameter: UiParameter, value: str) -> str | None:
    """The text of an enumeration's ``value`` (``None`` for other widgets)."""
    widget = parameter.widget
    if isinstance(widget, EnumWidget):
        for choice in widget.choices:
            if str(choice.value) == value:
                return choice.label
    return None


def shown_parameters(nodes: Iterable[UiNode]) -> dict[str, UiParameter]:
    """The parameters a UI shows, by ref id: those in tabs and blocks, not in a hidden block."""
    result: dict[str, UiParameter] = {}
    for node in nodes:
        if isinstance(node, UiParameter):
            result.setdefault(node.ref_id, node)
        elif isinstance(node, UiParameterBlock):
            if not node.hidden:
                result.update(
                    (k, v)
                    for k, v in shown_parameters(node.children).items()
                    if k not in result
                )
        elif isinstance(node, UiTab):
            result.update(
                (k, v)
                for k, v in shown_parameters(node.children).items()
                if k not in result
            )
    return result


def _passes(gate: Gate, value: str) -> bool:
    if gate.when is not None:
        return satisfies(gate.when, value)
    return not any(satisfies(condition, value) for condition in gate.unless)


def _test_numbers(gate: Gate) -> list[int]:
    """The numbers a gate's tests name, with their neighbours (for ``<`` / ``>`` tests)."""
    tests = [gate.when] if gate.when is not None else list(gate.unless)
    numbers: list[int] = []
    for test in tests:
        for token in str(test).split():
            match = _NUMBER.search(token)
            if match is not None:
                n = int(match.group())
                numbers.extend((n, n + 1, n - 1))
    return numbers


def _candidates(gate: Gate, parameter: UiParameter, current: str) -> list[str]:
    """The values of ``parameter`` that pass ``gate``: the current and the default value first."""
    widget = parameter.widget
    pool: list[str] = [current, parameter.default_value]
    if isinstance(widget, EnumWidget):
        pool += [str(choice.value) for choice in widget.choices]
        allowed: set[str] | None = {str(choice.value) for choice in widget.choices}
    elif isinstance(widget, CheckBoxWidget):
        pool += ["1", "0"]
        allowed = {"0", "1"}
    elif isinstance(widget, (NumberWidget, NumberSliderWidget, TimeWidget)):
        low, high = widget.min, widget.max
        pool += [str(n) for n in _test_numbers(gate) if low <= n <= high]
        pool += [str(low), str(high)]
        allowed = None
    else:
        pool += [t for t in (gate.when or "").split() if t[:1].isalnum()]
        allowed = None
    result: list[str] = []
    for value in pool:
        if (
            value
            and value not in result
            and (allowed is None or value in allowed)
            and _passes(gate, value)
        ):
            result.append(value)
    return result


def _input_values(parameter: UiParameter, current: str) -> list[str]:
    """The values worth trying for a calculation input: every choice, or the numbers of a small
    range (the ends and the neighbours of the current value of a larger one)."""
    widget = parameter.widget
    if isinstance(widget, EnumWidget):
        values = [str(choice.value) for choice in widget.choices]
    elif isinstance(widget, CheckBoxWidget):
        values = ["1", "0"]
    elif isinstance(widget, (NumberWidget, NumberSliderWidget, TimeWidget)):
        low, high = widget.min, widget.max
        if high - low < _MAX_RANGE:
            values = [str(n) for n in range(low, high + 1)]
        else:
            around = int(current) if current.lstrip("-").isdigit() else low
            values = [
                str(n) for n in (around + 1, around - 1, high, low) if low <= n <= high
            ]
    else:
        values = []
    return [v for v in dict.fromkeys(values) if v != current]


class _Planner:
    def __init__(self, ui: DynamicUI) -> None:
        self._ui = ui
        self._placements: dict[str, list[Placement]] = {}
        for placement in ui.explore_parameters():
            self._placements.setdefault(placement.parameter.ref_id, []).append(
                placement
            )
        self._shown = shown_parameters(ui.ui())
        self._active = ui.active_parameter_ref_ids()
        # Why the last search found nothing, for the error message.
        self.blocked: str | None = None
        # Values of calculation inputs tried on a copy of the state (see _calculated).
        self._trials = 0

    @property
    def shown(self) -> dict[str, UiParameter]:
        """The parameters the device shows now."""
        return self._shown

    def placements(self, ref_id: str) -> list[Placement]:
        return [
            p
            for p in self._placements.get(ref_id, [])
            if not p.hidden_section and p.parameter.access is not Access.NONE
        ]

    def parameter(self, ref_id: str) -> UiParameter | None:
        shown = self._shown.get(ref_id)
        if shown is not None:
            return shown
        placements = self.placements(ref_id)
        return placements[0].parameter if placements else None

    def sections(self, ref_id: str) -> tuple[str, ...]:
        placements = self.placements(ref_id)
        return placements[0].sections if placements else ()

    def current(self, ref_id: str) -> str:
        value = self._ui.get_value(ref_id)
        if value is not None:
            return value
        parameter = self.parameter(ref_id)
        return parameter.default_value if parameter is not None else ""

    def known(self, ref_id: str) -> bool:
        """Whether ``ref_id`` is a parameter-ref of the application."""
        return self._ui.local_ref_id(ref_id) in self._ui.indexer.parameter_refs

    def label(self, ref_id: str) -> str:
        parameter = self.parameter(ref_id)
        if parameter is None:
            placements = self._placements.get(ref_id)
            parameter = placements[0].parameter if placements else None
        if parameter is not None and parameter.label:
            return parameter.label
        idx = self._ui.indexer
        ref = idx.parameter_refs.get(self._ui.local_ref_id(ref_id))
        base = idx.parameters.get(ref.ref_id) if ref is not None else None
        text = (
            (ref.text if ref is not None else None)
            or (base.text if base is not None else None)
            or (base.name if base is not None else None)
        )
        return text or ref_id

    def plans(
        self, ref_id: str, fixed: dict[str, str], stack: frozenset[str]
    ) -> Iterator[list[tuple[str, str]]]:
        """Edit lists that show ``ref_id``, given the values earlier edits set (``fixed``)."""
        if len(stack) > _MAX_DEPTH or ref_id in stack:
            return
        if ref_id in self._shown:
            yield []
            return
        placements = self.placements(ref_id)
        if not placements:
            if self._placements.get(ref_id):
                self.blocked = f"{self.label(ref_id)!r} is never shown to users"
            elif self.known(ref_id):
                self.blocked = (
                    f"{self.label(ref_id)!r} is set by the application, not by the user"
                )
            else:
                self.blocked = "it is not a parameter of this device"
            return

        # Placements whose gates already pass first: they need the fewest edits.
        def unmet(placement: Placement) -> int:
            return sum(
                not _passes(g, fixed.get(g.param, self.current(g.param)))
                for g in placement.gates
            )

        for placement in sorted(placements, key=unmet):
            yield from self._gates(placement.gates, fixed, stack | {ref_id})

    def _gates(
        self, gates: Sequence[Gate], fixed: dict[str, str], stack: frozenset[str]
    ) -> Iterator[list[tuple[str, str]]]:
        if not gates:
            yield []
            return
        gate, rest = gates[0], gates[1:]
        param = gate.param
        if param in fixed:
            if _passes(gate, fixed[param]):
                yield from self._gates(rest, fixed, stack)
            return
        current = self.current(param)
        if _passes(gate, current):
            if not gate.needs_active or param in self._active:
                yield from self._gates(rest, fixed, stack)
                return
            # The value selects the branch, but a choose on a hidden parameter selects nothing.
            for shown in self.plans(param, fixed, stack):
                after = {**fixed, **dict(shown)}
                for tail in self._gates(rest, after, stack):
                    yield shown + tail
            return
        parameter = self.parameter(param)
        if parameter is None or parameter.access is not Access.READ_WRITE:
            found = False
            for steps in self._calculated(gate, fixed, stack):
                found = True
                after = {**fixed, **dict(steps)}
                for tail in self._gates(rest, after, stack):
                    yield steps + tail
            if not found:
                self.blocked = (
                    f"{self.label(param)!r} is set by the application, not by the user"
                    if parameter is None
                    else f"{self.label(param)!r} cannot be changed"
                )
            return
        values = _candidates(gate, parameter, current)
        if not values:
            self.blocked = f"no value of {self.label(param)!r} selects that part"
            return
        for shown in self.plans(param, fixed, stack):
            for value in values:
                after = {**fixed, **dict(shown), param: value}
                for tail in self._gates(rest, after, stack):
                    yield [*shown, (param, value), *tail]

    def _inputs(self, param: str) -> list[str]:
        """The parameters a calculation computes ``param`` from (instance-qualified like it)."""
        local = self._ui.local_ref_id(param)
        prefix = param[: -len(local)] if param.endswith(local) else ""
        idx = self._ui.indexer
        result: list[str] = []
        sides = [
            (calc, calc.lparameters.parameter_ref_ref)
            for calc in idx.calculations_for_r(local)
        ] + [
            (calc, calc.rparameters.parameter_ref_ref)
            for calc in idx.calculations_for_l(local)
            if calc.rltransformation_func or calc.rltransformation is not None
        ]
        for _, refs in sides:
            for ref in refs:
                qualified = (
                    prefix + ref.ref_id
                    if prefix and idx.ref_owner.get(ref.ref_id) is not None
                    else ref.ref_id
                )
                if qualified != param and qualified not in result:
                    result.append(qualified)
        return result

    def _calculated(
        self, gate: Gate, fixed: dict[str, str], stack: frozenset[str]
    ) -> Iterator[list[tuple[str, str]]]:
        """Edits of a calculation's inputs after which it gives ``gate``'s parameter a passing
        value, each found by trying the input's values on a copy of the device's state."""
        for source in self._inputs(gate.param):
            parameter = self.parameter(source)
            if source in fixed or parameter is None:
                continue
            if parameter.access is not Access.READ_WRITE:
                continue
            for shown in islice(self.plans(source, fixed, stack | {gate.param}), 1):
                for value in _input_values(parameter, self.current(source)):
                    if self._trials >= _MAX_TRIALS:
                        return
                    self._trials += 1
                    steps = [*shown, (source, value)]
                    with self._ui.simulation():
                        if _apply(self._ui, [*fixed.items(), *steps]) is not None:
                            continue
                        result = self._ui.get_value(gate.param) or ""
                    if _passes(gate, result):
                        yield steps


def _unique(steps: list[tuple[str, str]]) -> list[tuple[str, str]] | None:
    """The steps without repeats, or ``None`` when one parameter needs two values."""
    seen: dict[str, str] = {}
    result: list[tuple[str, str]] = []
    for ref_id, value in steps:
        if ref_id in seen:
            if seen[ref_id] != value:
                return None
            continue
        seen[ref_id] = value
        result.append((ref_id, value))
    return result


def _instantiate_new_objects(ui: DynamicUI, before: set[str]) -> None:
    """Instantiate the group objects an edit activated, as the project does after a change: the
    device then shows the channels they belong to."""
    if not ui.instantiated_com_object_ref_ids():
        return
    for ref_id in sorted(ui.active_parameter_driven_com_object_ref_ids() - before):
        ui.set_com_obj_instance_ref(ref_id, ComObjectInstanceRef(ref_id=ref_id))


def _apply(ui: DynamicUI, edits: Iterable[tuple[str, str]]) -> str | None:
    """Make ``edits`` on ``ui`` one by one, as a user would: why one is rejected, else ``None``."""
    for ref_id, value in edits:
        before = ui.active_parameter_driven_com_object_ref_ids()
        try:
            ui.edit_parameters([(ref_id, value)])
        except ValueError as exc:
            return str(exc)
        _instantiate_new_objects(ui, before)
    return None


def _try(
    ui: DynamicUI, steps: list[tuple[str, str]], target: str, value: str | None
) -> tuple[dict[str, UiParameter], str | None]:
    """Apply ``steps`` (and the target's ``value``) to ``ui``: the shown parameters afterwards, and
    why the plan fails (``None`` when it shows the target)."""
    edits = [*steps, (target, value)] if value is not None else steps
    rejected = _apply(ui, edits)
    if rejected is not None:
        return {}, rejected
    shown = shown_parameters(ui.ui())
    parameter = shown.get(target)
    if parameter is None:
        return shown, "the edits do not show it"
    if parameter.access is not Access.READ_WRITE:
        return shown, "it is shown read-only"
    return shown, None


def plan_activation(
    ui: DynamicUI, target: str, value: str | None = None
) -> ActivationPlan:
    """The edits that show ``target`` on ``ui``'s device (and set it to ``value``, when given).

    Raises :class:`ActivationError` when no plan does: the parameter is unknown, never shown to
    users, behind a parameter the application sets itself, or every plan tried fails on the copy
    of the device's state. ``ui`` is left as it was."""
    planner = _Planner(ui)
    before = planner.shown
    if target in before and value is None:
        return ActivationPlan(target, None, (), (), ())
    candidates: list[list[tuple[str, str]]] = []
    for steps in islice(planner.plans(target, {}, frozenset()), _MAX_PLANS):
        unique = _unique(steps)
        if unique is not None and unique not in candidates:
            candidates.append(unique)
    if not candidates:
        reason = planner.blocked or "it is not a parameter of this device"
        raise ActivationError(f"{planner.label(target)!r} cannot be shown: {reason}")
    failure = ""
    for steps in sorted(candidates, key=len)[:_MAX_TRIES]:
        with ui.simulation():
            shown, failure_reason = _try(ui, steps, target, value)
        if failure_reason is None:
            return _plan(planner, target, value, steps, before, shown)
        failure = failure_reason
    raise ActivationError(f"{planner.label(target)!r} cannot be shown: {failure}")


def _plan(
    planner: _Planner,
    target: str,
    value: str | None,
    steps: list[tuple[str, str]],
    before: dict[str, UiParameter],
    after: dict[str, UiParameter],
) -> ActivationPlan:
    edits = [*steps, (target, value)] if value is not None else steps
    result: list[PlanStep] = []
    for ref_id, step_value in edits:
        parameter = after.get(ref_id) or planner.parameter(ref_id)
        assert parameter is not None
        result.append(
            PlanStep(
                ref_id,
                step_value,
                parameter,
                planner.sections(ref_id),
                planner.label(ref_id),
            )
        )
    edited = {ref_id for ref_id, _ in edits} | {target}
    return ActivationPlan(
        target,
        value,
        tuple(result),
        tuple(r for r in after if r not in before and r not in edited),
        tuple(r for r in before if r not in after and r not in edited),
    )

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable

from xknxeditor.namespaces.intermediate import (
    ApplicationProgram,
    ApplicationProgramStaticParametersUnion,
    ModuleDef,
    ModuleDefStaticParametersUnion,
    ParameterType,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_messages_message import (
    ApplicationProgramStaticMessagesMessage,
)
from xknxeditor.namespaces.intermediate.com_object_ref_t import ComObjectRef
from xknxeditor.namespaces.intermediate.com_object_t import ComObject
from xknxeditor.namespaces.intermediate.parameter_base_t import ParameterBase
from xknxeditor.namespaces.intermediate.parameter_calculation_t import (
    ParameterCalculation,
)
from xknxeditor.namespaces.intermediate.parameter_ref_t import ParameterRef
from xknxeditor.namespaces.intermediate.parameter_validation_t import (
    ParameterValidation,
)
from xknxeditor.namespaces.intermediate.segment_base_t import SegmentBase

from .allocator import Allocator


class ApplicationIndexer:
    """Prebuilt indexes over the static IR (parameters, refs, types, module defs)."""

    __slots__ = (
        "_calc_sides",
        "_calculations",
        "_plans",
        "_validations",
        "allocators",
        "app_allocators",
        "arg_alloc",
        "code_segments",
        "com_object_refs",
        "com_objects",
        "messages",
        "module_defs",
        "parameter_refs",
        "parameter_types",
        "parameters",
        "ref_owner",
        "refs_by_name",
        "refs_by_number",
        "script",
    )

    def __init__(self, app: ApplicationProgram) -> None:
        self.module_defs: dict[str, ModuleDef] = {}
        self.parameter_refs: dict[str, ParameterRef] = {}
        self.parameters: dict[str, ParameterBase] = {}
        self.parameter_types: dict[str, ParameterType] = {}
        self.com_objects: dict[str, ComObject] = {}
        self.com_object_refs: dict[str, ComObjectRef] = {}
        self.code_segments: dict[str, SegmentBase] = {}
        self._calculations: dict[str, dict[str, list[ParameterCalculation]]] = {}
        self._calc_sides: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
        self._plans: dict[
            tuple[str, ...], tuple[tuple[ParameterCalculation, str], ...]
        ] = {}
        self._validations: dict[str, list[ParameterValidation]] = {}
        self.ref_owner: dict[str, str | None] = {}
        self.refs_by_name: dict[str | None, dict[str, str]] = {}
        self.refs_by_number: dict[str | None, dict[int, str]] = {}
        self.messages: dict[str, ApplicationProgramStaticMessagesMessage] = {}
        self.script: str | None = None
        self.allocators: dict[str, dict[str, Allocator]] = {}
        # Application-level allocators: a module argument's Allocator can be defined on the
        # application (not the module def), so keep them separate for allocate() to fall back to.
        self.app_allocators: dict[str, Allocator] = {}
        self.arg_alloc: dict[str, dict[str, tuple[int, int]]] = {}
        self._index_app(app)
        if app.module_defs is not None:
            for md in app.module_defs.module_def:
                self._index_module_def(md)

    def _index_app(self, app: ApplicationProgram) -> None:
        s = app.static
        if s.script is not None:
            self.script = s.script.value or None
        if s.code is not None:
            for seg in s.code.absolute_segment:
                self.code_segments[seg.id] = seg
            for seg in s.code.relative_segment:
                self.code_segments[seg.id] = seg
        if s.parameter_types is not None:
            for pt in s.parameter_types.parameter_type:
                self.parameter_types[pt.id] = pt
        if s.parameters is not None:
            for p in s.parameters.choice:
                if isinstance(p, ApplicationProgramStaticParametersUnion):
                    for up in p.parameter:
                        self.parameters[up.id] = up
                else:
                    self.parameters[p.id] = p
        if s.parameter_refs is not None:
            for pr in s.parameter_refs.parameter_ref:
                self.parameter_refs[pr.id] = pr
                self._index_ref_lookup(pr, None)
        if s.messages is not None:
            for msg in s.messages.message:
                self.messages[msg.id] = msg
        if s.com_object_table is not None:
            for co in s.com_object_table.com_object:
                self.com_objects[co.id] = co
        if s.com_object_refs is not None:
            for cor in s.com_object_refs.com_object_ref:
                self.com_object_refs[cor.id] = cor
        if s.parameter_calculations is not None:
            self._index_calculations(s.parameter_calculations.parameter_calculation)
        if s.parameter_validations is not None:
            self._index_validations(s.parameter_validations.parameter_validation)
        if s.allocators is not None:
            self.app_allocators = {
                a.id: Allocator(id=a.id, start=a.start, max_inclusive=a.max_inclusive)
                for a in s.allocators.allocator
            }

    def segment_base_addr(self, seg_id: str) -> int:
        seg = self.code_segments.get(seg_id)
        if seg is None:
            return 0
        return int(getattr(seg, "address", getattr(seg, "offset", 0)))

    _REF_NUMBER = re.compile(r"_R-([0-9]+)$")

    def _index_ref_lookup(self, pr: ParameterRef, owner: str | None) -> None:
        self.ref_owner[pr.id] = owner
        base = self.parameters.get(pr.ref_id)
        name = getattr(base, "name", None)
        if name:
            self.refs_by_name.setdefault(owner, {}).setdefault(name, pr.id)
        m = self._REF_NUMBER.search(pr.id)
        if m:
            self.refs_by_number.setdefault(owner, {}).setdefault(int(m.group(1)), pr.id)

    def default_value(self, ref_id: str) -> str | None:
        """Value of a ParameterRef that has never been set."""
        pr = self.parameter_refs.get(ref_id)
        if pr is None:
            return None
        if pr.value is not None:
            return pr.value
        base = self.parameters.get(pr.ref_id)
        return base.value if base is not None else None

    def type_of(self, ref_id: str) -> object | None:
        """The ParameterType restriction (TypeNumber, TypeText, ...) of a ParameterRef."""
        pr = self.parameter_refs.get(ref_id)
        if pr is None:
            return None
        base = self.parameters.get(pr.ref_id)
        if base is None:
            return None
        pt = self.parameter_types.get(base.parameter_type)
        return pt.choice if pt is not None else None

    def parameter_name(self, ref_id: str) -> str | None:
        pr = self.parameter_refs.get(ref_id)
        base = self.parameters.get(pr.ref_id) if pr is not None else None
        return getattr(base, "name", None)

    def message_text(self, key: object) -> str | None:
        """Message text by number (``M-<n>``) or by name."""
        for msg in self.messages.values():
            if isinstance(key, (int, float)) and not isinstance(key, bool):
                if msg.id.endswith(f"_M-{int(key)}"):
                    return msg.text
            elif msg.name == key or msg.id == key:
                return msg.text
        return None

    def calculation_plan(
        self, ref_id: str
    ) -> tuple[tuple[ParameterCalculation, str], ...]:
        """Calculations a change of ``ref_id`` triggers, topologically sorted, each once.

        The direction is ``"LR"`` when the triggering parameter is on the L side and
        ``"RL"`` when it is on the R side.
        """
        return self.calculation_plan_many((ref_id,))

    def calculation_plan_many(
        self, ref_ids: Iterable[str]
    ) -> tuple[tuple[ParameterCalculation, str], ...]:
        """Union calculation plan for a batch of changed parameters, toposorted, each calc once.

        Running one plan per edit re-executes calculations shared between the edits' chains and feeds
        already-recomputed intermediates back in (double application). Seeding the walk with every
        changed ref and emitting each calculation a single time avoids that; apply all direct edits
        before running it.
        """
        seeds = tuple(dict.fromkeys(ref_ids))
        cached = self._plans.get(seeds)
        if cached is not None:
            return cached
        order: list[tuple[ParameterCalculation, str]] = []
        chosen: set[str] = set()
        frontier = list(seeds)
        seen = set(seeds)
        while frontier:
            ref = frontier.pop(0)
            sides = self._calculations.get(ref, {})
            for side, direction in (("l", "LR"), ("r", "RL")):
                for calc in sides.get(side, []):
                    if calc.id in chosen:
                        continue
                    chosen.add(calc.id)
                    order.append((calc, direction))
                    for out in self._outputs(calc, direction):
                        if out not in seen:
                            seen.add(out)
                            frontier.append(out)
        plan = self._toposort(order)
        self._plans[seeds] = plan
        return plan

    def _sides(
        self, calc: ParameterCalculation
    ) -> tuple[frozenset[str], frozenset[str]]:
        cached = self._calc_sides.get(calc.id)
        if cached is None:
            cached = (
                frozenset(pr.ref_id for pr in calc.lparameters.parameter_ref_ref),
                frozenset(pr.ref_id for pr in calc.rparameters.parameter_ref_ref),
            )
            self._calc_sides[calc.id] = cached
        return cached

    def _outputs(self, calc: ParameterCalculation, direction: str) -> frozenset[str]:
        left, right = self._sides(calc)
        return right if direction == "LR" else left

    def _inputs(self, calc: ParameterCalculation, direction: str) -> frozenset[str]:
        left, right = self._sides(calc)
        return left if direction == "LR" else right

    def _toposort(
        self, order: list[tuple[ParameterCalculation, str]]
    ) -> tuple[tuple[ParameterCalculation, str], ...]:
        n = len(order)
        deps: list[set[int]] = [set() for _ in range(n)]
        for a in range(n):
            out_a = self._outputs(*order[a])
            for b in range(n):
                if a != b and out_a & self._inputs(*order[b]):
                    deps[b].add(a)
        done: list[int] = []
        placed: set[int] = set()
        while len(done) < n:
            ready = [i for i in range(n) if i not in placed and deps[i] <= placed]
            if not ready:
                rest = [i for i in range(n) if i not in placed]
                warnings.warn(
                    f"cyclic parameter calculations: {[order[i][0].id for i in rest]}",
                    stacklevel=2,
                )
                ready = rest[:1]
            done.append(ready[0])
            placed.add(ready[0])
        return tuple(order[i] for i in done)

    def calculations_for_l(self, ref_id: str) -> list[ParameterCalculation]:
        return self._calculations.get(ref_id, {}).get("l", [])

    def calculations_for_r(self, ref_id: str) -> list[ParameterCalculation]:
        return self._calculations.get(ref_id, {}).get("r", [])

    def _index_validations(self, validations: list[ParameterValidation]) -> None:
        for v in validations:
            for pr in v.parameters.parameter_ref_ref:
                self._validations.setdefault(pr.ref_id, []).append(v)

    def validations_for(self, ref_id: str) -> list[ParameterValidation]:
        return self._validations.get(ref_id, [])

    def type_error_text(self, ref_id: str) -> str | None:
        """The message a ParameterType's ``ValidationErrorRef`` names for invalid values."""
        pr = self.parameter_refs.get(ref_id)
        base = self.parameters.get(pr.ref_id) if pr is not None else None
        pt = self.parameter_types.get(base.parameter_type) if base is not None else None
        ref = pt.validation_error_ref if pt is not None else None
        msg = self.messages.get(ref) if ref else None
        return msg.text if msg is not None else None

    def _index_calculations(self, calcs: list[ParameterCalculation]) -> None:
        for calc in calcs:
            for pr in calc.lparameters.parameter_ref_ref:
                self._calculations.setdefault(pr.ref_id, {}).setdefault("l", []).append(
                    calc
                )
            for pr in calc.rparameters.parameter_ref_ref:
                self._calculations.setdefault(pr.ref_id, {}).setdefault("r", []).append(
                    calc
                )

    def _index_module_def(self, md: ModuleDef) -> None:
        if md.id:
            self.module_defs[md.id] = md
        if md.static.parameters is not None:
            for p in md.static.parameters.choice:
                if isinstance(p, ModuleDefStaticParametersUnion):
                    for up in p.parameter:
                        self.parameters[up.id] = up
                else:
                    self.parameters[p.id] = p
        if md.static.parameter_refs is not None:
            for pr in md.static.parameter_refs.parameter_ref:
                self.parameter_refs[pr.id] = pr
                self._index_ref_lookup(pr, md.id)
        if md.static.com_objects is not None:
            for co in md.static.com_objects.com_object:
                self.com_objects[co.id] = co
        if md.static.com_object_refs is not None:
            for cor in md.static.com_object_refs.com_object_ref:
                self.com_object_refs[cor.id] = cor
        if md.static.parameter_calculations is not None:
            self._index_calculations(
                md.static.parameter_calculations.parameter_calculation
            )
        if md.static.parameter_validations is not None:
            self._index_validations(
                md.static.parameter_validations.parameter_validation
            )
        if md.static.allocators is not None:
            self.allocators[md.id] = {
                a.id: Allocator(id=a.id, start=a.start, max_inclusive=a.max_inclusive)
                for a in md.static.allocators.allocator
            }
        if md.arguments is not None:
            self.arg_alloc[md.id] = {
                a.id: (a.allocates if a.allocates is not None else 1, a.alignment.value)
                for a in md.arguments.argument
            }
        if md.sub_module_defs is not None:
            for sub in md.sub_module_defs.module_def:
                self._index_module_def(sub)

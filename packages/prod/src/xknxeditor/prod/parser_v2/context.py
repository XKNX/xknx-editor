from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate import ModuleArg
from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
    ComObjectInstanceRef,
)

if TYPE_CHECKING:
    from .application_indexer import ApplicationIndexer
    from .state import ParameterState
    from .ui.parameter import UiParameter


@dataclass(frozen=True, slots=True)
class Gate:
    """A condition on the way to a part of the parameter tree: the (instance-qualified) parameter
    ``param`` has to take a value that satisfies the ``when`` test, or, for a default branch
    (``when`` is None), none of the ``unless`` tests. With ``needs_active`` (a choose) the
    parameter itself has to be shown or assigned too, or its choose selects nothing; a repeat
    count does not."""

    param: str
    when: str | None
    unless: tuple[str, ...] = ()
    needs_active: bool = True


@dataclass(frozen=True, slots=True)
class Placement:
    """One place of a parameter in the tree, with the gates leading to it and the headings of the
    tabs and blocks around it (outermost first). ``hidden_section`` is set when a block on the way
    is never shown (Access None)."""

    parameter: UiParameter
    gates: tuple[Gate, ...]
    sections: tuple[str, ...]
    hidden_section: bool = False


class Explorer:
    """Records every parameter placement of a tree evaluated in explore mode: each choose
    evaluates all its branches and each repeat at least one pass, whatever the current values,
    pushing the condition of the branch it is in (see :class:`Gate`)."""

    __slots__ = ("_gates", "_hidden", "_sections", "placements")

    def __init__(self) -> None:
        self._gates: list[Gate] = []
        self._sections: list[str] = []
        self._hidden = 0
        self.placements: list[Placement] = []

    def push_gate(self, gate: Gate) -> None:
        self._gates.append(gate)

    def pop_gate(self) -> None:
        self._gates.pop()

    def enter_section(self, heading: str, *, hidden: bool = False) -> None:
        self._sections.append(heading)
        self._hidden += hidden

    def leave_section(self, *, hidden: bool = False) -> None:
        self._sections.pop()
        self._hidden -= hidden

    def record(self, parameter: UiParameter) -> None:
        self.placements.append(
            Placement(
                parameter,
                tuple(self._gates),
                tuple(s for s in self._sections if s),
                self._hidden > 0,
            )
        )


class EvalCapture:
    """Records, per emitted com-object, the set of gating parameters on its emission path (its "gate
    chain") during a full tree eval.

    Choose/Repeat nodes ``push`` their gating parameter while evaluating their selected branch and
    ``pop`` after; a com-object leaf calls ``record_object`` to snapshot the current gate chain. An
    object is parameter-driven "active" iff some emission has every gate on its chain driven by an
    active parameter (chain-AND); an ungated object has an empty chain and is always active.

    ``tracked=None`` records every object; a str/iterable restricts recording to emissions whose gate
    chain contains one of those parameters (used to find exactly what a single parameter controls)."""

    __slots__ = ("_stack", "_tracked", "chains")

    def __init__(self, tracked: str | Iterable[str] | None = None) -> None:
        if tracked is None:
            self._tracked: frozenset[str] | None = None
        elif isinstance(tracked, str):
            self._tracked = frozenset({tracked})
        else:
            self._tracked = frozenset(tracked)
        self._stack: list[str] = []
        self.chains: dict[str, list[frozenset[str]]] = {}

    def push(self, param_ref_id: str) -> None:
        self._stack.append(param_ref_id)

    def pop(self) -> None:
        self._stack.pop()

    def record_object(self, ref_id: str) -> None:
        chain = frozenset(self._stack)
        if self._tracked is None or (self._tracked & chain):
            self.chains.setdefault(ref_id, []).append(chain)

    def controlled_ref_ids(self) -> set[str]:
        """The com-object ref-ids recorded (for a ``tracked`` capture: those the tracked parameter(s)
        gate)."""
        return set(self.chains)

    def active_ref_ids(self, active_params: frozenset[str] | set[str]) -> set[str]:
        """Ref-ids with at least one emission whose entire gate chain is active (chain-AND)."""
        return {
            ref
            for ref, chains in self.chains.items()
            if any(chain <= active_params for chain in chains)
        }


class _TextArgs(Mapping[str, str]):
    """The module arguments of a scope, plus parameter-ref values by unique number (looked up
    only when a text asks for one). ``{{0}}`` is not a number here: it is the text parameter."""

    __slots__ = ("_args", "_ctx")

    def __init__(self, ctx: EvalContext) -> None:
        self._ctx = ctx
        self._args = ctx.get_arg_defaults()

    def __getitem__(self, key: str) -> str:
        if key in self._args:
            return self._args[key]
        if key.isdigit() and int(key) != 0:
            ref = self._ctx.ref_by_number(int(key))
            if ref is not None:
                return self._ctx.value_or_default(ref) or ""
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._args)

    def __len__(self) -> int:
        return len(self._args)


class EvalContext:
    """Scope handle wrapping the active state node and the repeat index for the next module_child.

    Reads fall through the state's parent chain (submodule → module → global); writes hit only the active state.
    """

    __slots__ = (
        "_capture",
        "_explore",
        "_idx",
        "_repeat_idx",
        "_scope",
        "_union_suppress",
    )

    def __init__(
        self,
        scope: ParameterState,
        repeat_idx: int = 1,
        idx: ApplicationIndexer | None = None,
        capture: EvalCapture | None = None,
        union_suppress: bool = True,
        explore: Explorer | None = None,
    ) -> None:
        self._scope = scope
        self._repeat_idx = repeat_idx
        self._idx = idx
        self._capture = capture
        # False during the union-discovery pass (render both overlapping members so the reached one
        # is recorded); True during the render pass (suppress the inactive overlay).
        self._union_suppress = union_suppress
        # Set for an explore pass (see Explorer): every branch is evaluated and recorded.
        self._explore = explore

    @property
    def capture(self) -> EvalCapture | None:
        return self._capture

    @property
    def explore(self) -> Explorer | None:
        return self._explore

    @property
    def union_suppress(self) -> bool:
        return self._union_suppress

    def is_discovered_active(self, ref_id: str) -> bool:
        return self._scope.is_discovered_active(ref_id)

    def is_gate_active(self, ref_id: str) -> bool:
        return self._scope.is_gate_active(ref_id)

    def get(self, ref_id: str) -> str | None:
        return self._scope.get(ref_id)

    def qualify(self, ref_id: str) -> str:
        return self._scope.qualify(ref_id)

    @property
    def module_instance_id(self) -> str | None:
        """Id of the innermost module instance being evaluated, if any."""
        mid = getattr(self._scope, "module_instance_id", None)
        return mid if isinstance(mid, str) else None

    def qualify_local(self, ref_id: str) -> str:
        return self._scope.qualify_local(ref_id)

    def set(self, ref_id: str, value: str) -> None:
        self._scope.set(ref_id, value)

    def set_text(self, ref_id: str, text: str) -> None:
        self._scope.set_text(ref_id, text)

    def get_text(self, ref_id: str) -> str | None:
        return self._scope.get_text(ref_id)

    def mark_active_param(self, ref_id: str) -> None:
        self._scope.mark_active_param(ref_id)

    def mark_active_com_object(self, ref_id: str) -> None:
        self._scope.mark_active_com_object(ref_id)

    def get_com_obj_instance_ref(self, ref_id: str) -> ComObjectInstanceRef | None:
        return self._scope.get_com_obj_instance_ref(ref_id)

    def allocate(
        self, def_ref_id: str, alloc_id: str, arg_ref_id: str, base: int = 0
    ) -> int:
        """Take the next address from the pool, bump the scope position, and return it."""
        if self._idx is None:
            raise RuntimeError("allocate() requires an ApplicationIndexer")
        # A module argument's Allocator may be defined on the module definition OR on the
        # application (both are valid); look in the module def first, then fall back to the
        # application-level allocators.
        alloc = (self._idx.allocators.get(def_ref_id) or {}).get(
            alloc_id
        ) or self._idx.app_allocators.get(alloc_id)
        arg_allocs = self._idx.arg_alloc.get(def_ref_id)
        if alloc is None or arg_allocs is None or arg_ref_id not in arg_allocs:
            # Truly unresolved (e.g. partial module data) — don't crash the device load; fall back
            # to the base address so the module's content still renders.
            return base
        allocates, alignment = arg_allocs[arg_ref_id]
        position = self._scope.alloc_position(alloc_id, alloc.start)
        address, next_position = alloc.resolve(position, allocates, alignment, base)
        self._scope.set_alloc_position(alloc_id, next_position)
        return address

    @property
    def repeat_idx(self) -> int:
        return self._repeat_idx

    def repeat_ctx(self, repeat_idx: int) -> EvalContext:
        return EvalContext(
            self._scope,
            repeat_idx,
            self._idx,
            self._capture,
            self._union_suppress,
            self._explore,
        )

    def get_arg_value(self, ref_id: str) -> int:
        arg = self._scope.get_arg(ref_id)
        return arg.value if arg is not None and arg.value is not None else 0

    def get_arg_defaults(self) -> dict[str, str]:
        return self._scope.get_arg_defaults()

    def text_args(self) -> Mapping[str, str]:
        """What ``{{name}}`` placeholders in texts stand for: the module arguments, and for a
        number the value of the parameter-ref with that unique number (``…_R-<number>``) in the
        scope's module definition, else in the application ("Channel A: {{5889}}")."""
        return _TextArgs(self)

    def ref_by_number(self, number: int) -> str | None:
        """The parameter-ref with unique number ``number`` of this scope (see :meth:`text_args`)."""
        if self._idx is None:
            return None
        owner = getattr(self._scope, "ref_id", None)
        for scope_owner in (owner, None) if owner is not None else (None,):
            ref = self._idx.refs_by_number.get(scope_owner, {}).get(number)
            if ref is not None:
                return ref
        return None

    def value_or_default(self, ref_id: str) -> str | None:
        """The value of ``ref_id`` in this scope, else its default."""
        value = self.get(ref_id)
        if value is None and self._idx is not None:
            value = self._idx.default_value(ref_id)
        return value

    def seed_param_ref_defaults(self, param_ref_defaults: dict[str, str]) -> None:
        self._scope.set_param_ref_defaults(param_ref_defaults)

    def module_ctx(
        self,
        module_id: str,
        default_arguments: dict[str, ModuleArg] | None = None,
        param_ref_defaults: dict[str, str] | None = None,
        arg_defaults: dict[str, str] | None = None,
        ref_id: str | None = None,
    ) -> EvalContext:
        ms = self._scope.module_child(
            module_id,
            self._repeat_idx,
            default_arguments,
            param_ref_defaults=param_ref_defaults,
            arg_defaults=arg_defaults,
            ref_id=ref_id,
        )
        return EvalContext(
            ms,
            idx=self._idx,
            capture=self._capture,
            union_suppress=self._union_suppress,
            explore=self._explore,
        )

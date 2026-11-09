from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any, ClassVar, Self

from xknxeditor.namespaces.ids import unpadded_id
from xknxeditor.namespaces.intermediate import (
    ComObjectInstanceRef,
    ModuleArg,
    ModuleInstance,
    ModuleTextArg,
    ParameterInstanceRef,
)
from xknxeditor.namespaces.intermediate.module_t_numeric_arg import ModuleNumericArg

from .application_indexer import ApplicationIndexer


def compute_arg_defaults(
    mod_def_arguments: object, instance_args: list[ModuleArg]
) -> dict[str, str]:
    """Map arg_name to value for a module instance's text args."""
    result: dict[str, str] = {}
    if mod_def_arguments is None:
        return result
    arg_def_by_id = {a.id: a for a in getattr(mod_def_arguments, "argument", [])}
    for arg in instance_args:
        if isinstance(arg, ModuleTextArg):
            arg_def = arg_def_by_id.get(arg.ref_id)
            if arg_def is not None:
                result[arg_def.name] = arg.value
    return result


def compute_param_ref_defaults(
    refs_container: object, idx: ApplicationIndexer
) -> dict[str, str]:
    """Map pr.id to effective value for a static section's ParameterRefs.

    Uses pr.value, else the base Parameter's value via idx.
    """
    result: dict[str, str] = {}
    refs = (
        getattr(refs_container, "parameter_ref", None)
        if refs_container is not None
        else None
    )
    if not refs:
        return result
    for pr in refs:
        value = pr.value
        if value is None:
            base = idx.parameters.get(pr.ref_id)
            if base is not None:
                value = base.value
        if value is not None:
            result[pr.id] = value
    return result


def compute_param_ref_bases(
    refs_container: object, idx: ApplicationIndexer
) -> dict[str, str]:
    """Map pr.id to the module argument its Parameter's ``BaseValue`` names: the parameter
    takes that argument's value."""
    result: dict[str, str] = {}
    refs = getattr(refs_container, "parameter_ref", None)
    if not refs:
        return result
    for pr in refs:
        param = idx.parameters.get(pr.ref_id)
        base = getattr(param, "base_value", None)
        if base is not None:
            result[pr.id] = base
    return result


# The innermost module instance in an instance-qualified ref, e.g. "MD-34_M-15_MI-1" (and
# "MD-34"), or the submodule instance "MD-34_M-15_MI-1_SM-1_M-1_MI-1-1-2" (and "MD-34").
_INSTANCE_IN_REF = re.compile(
    r"(?:^|_)((MD-[^_]+)_M-[^_]+_MI-[^_]+(?:_SM-[^_]+_M-[^_]+_MI-[^_]+)*)_"
)
# A submodule instance in a ref under the id without its enclosing instance, e.g.
# "MD-1_SM-1_M-1_MI-1" (and "MD-1_SM-1").
_LEGACY_SUBMODULE_IN_REF = re.compile(r"(?:^|_)((MD-[^_]+_SM-[^_]+)_M-[^_]+_MI-[^_]+)_")
# A submodule instance id: the enclosing instances' path, then "SM-<n>_M-<m>_MI-<counters>".
_SUBMODULE_INSTANCE = re.compile(
    r"(.*?MD-[^_]+)_M-[^_]+_MI-[^_]+(?:_SM-[^_]+_M-[^_]+_MI-[^_]+)*"
    r"_(SM-[^_]+)_M-[^_]+_MI-[^_]+"
)


def _submodule_definition(instance_id: str) -> tuple[str, str] | None:
    """``(submodule definition, top-level module definition)`` of a submodule instance id,
    e.g. ``("MD-4_SM-1", "MD-4")`` for ``MD-4_M-15_MI-1_SM-1_M-1_MI-1-1-2``; ``None`` for
    the id of a module instance (or a submodule instance id that does not name its
    enclosing instance)."""
    match = _SUBMODULE_INSTANCE.fullmatch(instance_id)
    if match is None:
        return None
    return f"{match.group(1)}_{match.group(2)}", match.group(1)


def _instance_length(scope: ParameterState) -> int:
    """The length of the id of the module instance ``scope`` is (0 for the application)."""
    return len(scope.module_instance_id) if isinstance(scope, ModuleState) else 0


@lru_cache(maxsize=1 << 16)
def _qualified(element_id: str, app_id: str | None) -> str:
    """``element_id`` qualified with the application id, when it is relative to it."""
    if not app_id or element_id.startswith(f"{app_id}_"):
        return element_id
    return f"{app_id}_{element_id}"


@lru_cache(maxsize=1 << 17)
def _qualified_in(module_instance_id: str, ref_id: str) -> str:
    """``ref_id`` (local to a module definition) qualified with the module instance
    ``module_instance_id``: the part they share swapped for the instance's id. Every evaluation
    qualifies each parameter of each instance again."""
    i, n = 0, min(len(module_instance_id), len(ref_id))
    while i < n and module_instance_id[i] == ref_id[i]:
        i += 1
    return module_instance_id + ref_id[i - 1 :]


class _InstanceIds:
    """Module instance ids in their order, to find the first one that is a part of a ref id
    between underscores (or at its start or end) without searching for each of them: such a
    part is a run of the ref id's underscore-separated pieces, which starts with the first
    piece of an instance id and ends with the last piece of one."""

    __slots__ = ("_firsts", "_lasts", "_order", "_owners", "_placed")

    def __init__(self, ids: Iterable[str]) -> None:
        self._order = {mid: n for n, mid in enumerate(ids)}
        self._firsts = {mid.split("_", 1)[0] for mid in self._order}
        self._lasts = {mid.rsplit("_", 1)[-1] for mid in self._order}
        # The owners found so far: the same instances own the same values seeding after seeding.
        self._owners: dict[str, str | None] = {}
        self._placed: dict[tuple[str, str, str], tuple[str, str]] = {}

    def place(self, ref_id: str, owner: str, definition: str) -> tuple[str, str]:
        """For the value of ``ref_id`` its instance ``owner`` owns (a module instance of the
        module definition ``definition``): the instance's id with the application's part of
        ``ref_id`` in front, and the value's ref id in the definition."""
        key = (ref_id, owner, definition)
        placed = self._placed.get(key)
        if placed is None:
            p = f"_{ref_id}_".find(f"_{owner}_")
            app_prefix = ref_id[:p]
            suffix = ref_id[p + len(owner) :]
            placed = self._placed[key] = (
                app_prefix + owner,
                app_prefix + definition + suffix,
            )
        return placed

    def owner(self, ref_id: str) -> str | None:
        """The first instance id that is such a part of ``ref_id``; None when none is."""
        owners = self._owners
        if ref_id in owners:
            return owners[ref_id]
        found = owners[ref_id] = self._find(ref_id)
        return found

    def _find(self, ref_id: str) -> str | None:
        order = self._order
        if len(order) <= 8:
            wrapped = f"_{ref_id}_"
            return next((mid for mid in order if f"_{mid}_" in wrapped), None)
        pieces = ref_id.split("_")
        found: str | None = None
        rank = len(order)
        for start, first in enumerate(pieces):
            if first not in self._firsts:
                continue
            for end in range(start, len(pieces)):
                if pieces[end] not in self._lasts:
                    continue
                part = "_".join(pieces[start : end + 1])
                n = order.get(part)
                if n is not None and n < rank:
                    found, rank = part, n
        return found


@lru_cache(maxsize=16)
def _instance_ids(ids: tuple[str, ...]) -> _InstanceIds:
    """The :class:`_InstanceIds` of module instance ids in their order (of the last few lists:
    a UI is seeded again and again with the same instances)."""
    return _InstanceIds(ids)


@lru_cache(maxsize=1 << 17)
def _instance_in_ref(ref_id: str) -> tuple[str, str] | None:
    """The module instance in an instance-qualified ref id and its module definition (see
    ``_INSTANCE_IN_REF`` and ``_LEGACY_SUBMODULE_IN_REF``); None for another ref id."""
    match = _INSTANCE_IN_REF.search(ref_id) or _LEGACY_SUBMODULE_IN_REF.search(ref_id)
    return None if match is None else (match.group(1), match.group(2))


class ParameterState:
    """Base of GlobalState/ModuleState; nodes form a root/module/submodule tree.

    Reads climb the parent chain, writes stay local, and Rename text overrides
    never walk parents. Active ref sets survive trim_to_active() and reset only
    on reset_active(), so callers can read them after ui()/evaluate() returns.
    """

    __slots__ = (
        "_active_com_object_refs",
        "_active_module_keys",
        "_active_param_refs",
        "_alloc_positions",
        "_assign_targets",
        "_assigned_from",
        "_children",
        "_com_obj_instance_refs",
        "_discovered_active_param_refs",
        "_dormant_children",
        "_gate_param_refs",
        "_known_param_ref_values",
        "_param_ref_defaults",
        "_parent",
        "_pass_texts",
        "_pass_values",
        "_text",
        "param_ref_id_to_value",
    )

    # Fields that are replaced, never changed in place (the static defaults of an application or
    # a module definition, tens of thousands of entries): a copy of the state shares them.
    _SHARED_FIELDS: ClassVar[frozenset[str]] = frozenset({"_param_ref_defaults"})

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        cls = type(self)
        new = cls.__new__(cls)
        memo[id(self)] = new
        for name in (
            n for klass in cls.__mro__ for n in getattr(klass, "__slots__", ())
        ):
            if hasattr(self, name):
                value = getattr(self, name)
                shared = name in self._SHARED_FIELDS
                setattr(new, name, value if shared else copy.deepcopy(value, memo))
        for name, value in getattr(self, "__dict__", {}).items():
            setattr(new, name, copy.deepcopy(value, memo))
        return new

    def __init__(
        self,
        values: dict[str, str] | None = None,
        parent: ParameterState | None = None,
        param_ref_defaults: dict[str, str] | None = None,
    ) -> None:
        self.param_ref_id_to_value: dict[str, str] = dict(values or {})
        # Durable store of every EXPLICIT value ever imported or set in this scope. Unlike
        # param_ref_id_to_value it is never trimmed to the active set, so a parameter whose block was
        # deactivated (its value dropped from the active/encoding map by trim_to_active) can still be
        # resolved to its real value when the block is reactivated -- otherwise get() falls back to a
        # static default and the eval picks a different Choose branch (different ComObjectRef).
        self._known_param_ref_values: dict[str, str] = dict(values or {})
        self._parent: ParameterState | None = parent
        self._children: dict[str, ModuleState] = {}
        # The module instances an evaluation no longer reached, by key: an instance that comes
        # back takes their explicit values again, as a device built from its stored values does.
        self._dormant_children: dict[str, ModuleState] = {}
        self._text: dict[str, str] = {}
        self._active_param_refs: set[str] = set()
        # Params marked active during the union-discovery pass (a full eval WITHOUT union
        # suppression). Frozen by snapshot_discovered_active() before the render pass, then read by
        # a Union-member Choose to decide the active overlay order-independently (the reached member
        # wins, not the one carrying a stale explicit value from an inactive branch).
        self._discovered_active_param_refs: set[str] = set()
        # Params that were active in the previous pass; a Choose on any other param renders
        # nothing. None while no pass has run (the first pass is not gated).
        self._gate_param_refs: set[str] | None = None
        # The module instances the current pass reached, in the order it reached them.
        self._active_module_keys: dict[str, None] = {}
        self._active_com_object_refs: set[str] = set()
        self._alloc_positions: dict[str, int] = {}
        self._param_ref_defaults: dict[str, str] = param_ref_defaults or {}
        self._com_obj_instance_refs: dict[str, ComObjectInstanceRef] = {}
        # The values and texts the current pass wrote (an Assign, a Rename), each with what it
        # was before the pass's first write to it: a pass that leaves one different has to be
        # followed by another, as a node read before the write saw the old value.
        self._pass_values: dict[str, str | None] = {}
        # The param-refs the Assigns of the current pass wrote from another one: target ->
        # source (local ids of this scope).
        self._assigned_from: dict[str, str] = {}
        # The param-refs an Assign of the current pass wrote (local ids of this scope): the
        # targets of the Assigns that apply.
        self._assign_targets: set[str] = set()
        self._pass_texts: dict[str, str | None] = {}

    def get(self, ref_id: str) -> str | None:
        # Prefer the durable known value: it survives trim_to_active(), so an inactive block's
        # imported value still drives branch selection when the block is (re)evaluated.
        known = self._known_param_ref_values.get(ref_id)
        if known is not None:
            return known
        val = self.param_ref_id_to_value.get(ref_id)
        if val is not None:
            return val
        if self._parent is not None:
            parent_val = self._parent.get(ref_id)
            if parent_val is not None:
                return parent_val
        return self._param_ref_defaults.get(ref_id)

    def has_explicit_param(self, ref_id: str) -> bool:
        """Whether ``ref_id`` has an explicit (imported or set) value in this scope or a parent.

        Distinguishes a real, chosen value from a mere static default -- used to resolve which member
        of a Union is the active overlay independent of node/source ordering."""
        if ref_id in self._known_param_ref_values:
            return True
        return (
            self._parent.has_explicit_param(ref_id)
            if self._parent is not None
            else False
        )

    def set_param_ref_defaults(self, param_ref_defaults: dict[str, str]) -> None:
        self._param_ref_defaults = param_ref_defaults

    def get_arg(self, ref_id: str) -> ModuleNumericArg | None:
        return None

    def get_arg_defaults(self) -> dict[str, str]:
        return {}

    def set(self, ref_id: str, value: str) -> None:
        if ref_id not in self._pass_values:
            self._pass_values[ref_id] = self._known_param_ref_values.get(ref_id)
        self.param_ref_id_to_value[ref_id] = value
        self._known_param_ref_values[ref_id] = value

    def assign(self, ref_id: str, value: str) -> None:
        """An Assign of the current pass writes ``value`` into ``ref_id``: it is stored like
        any other value and stays when the Assign no longer applies."""
        self.set(ref_id, value)
        self._assign_targets.add(ref_id)

    def assign_targets(self) -> set[str]:
        """The (instance-qualified) param-refs the Assigns of the last pass wrote, in this
        scope and the module scopes below."""
        result = {self.qualify(ref) for ref in self._assign_targets}
        for child in self._children.values():
            result |= child.assign_targets()
        return result

    def changed_explicit_values(self, before: ParameterState | None) -> dict[str, str]:
        """The explicit values of this scope and the module scopes below that are not in
        ``before`` (a copy of this scope taken earlier; ``None``: none), by instance-qualified
        id."""
        old = before._known_param_ref_values if before is not None else {}
        result = {
            self.qualify(ref): value
            for ref, value in self._known_param_ref_values.items()
            if old.get(ref) != value
        }
        for key, child in self._children.items():
            result.update(
                child.changed_explicit_values(
                    None
                    if before is None
                    else before._children.get(key) or before._dormant_children.get(key)
                )
            )
        return result

    def set_text(self, ref_id: str, text: str) -> None:
        if ref_id not in self._pass_texts:
            self._pass_texts[ref_id] = self._text.get(ref_id)
        self._text[ref_id] = text

    def pass_changed_state(self) -> bool:
        """Whether the pass since :meth:`reset_active` left a value or a text different from
        what it was before the pass (this scope or a module scope below)."""
        if any(
            self._known_param_ref_values.get(ref) != old
            for ref, old in self._pass_values.items()
        ) or any(self._text.get(ref) != old for ref, old in self._pass_texts.items()):
            return True
        return any(child.pass_changed_state() for child in self._children.values())

    def get_text(self, ref_id: str) -> str | None:
        return self._text.get(ref_id)

    def mark_active_param(self, ref_id: str) -> None:
        # A rendered widget re-enters the active/encoding map: restore its durable known value so a
        # parameter whose value was trimmed while inactive encodes its real value once reactivated,
        # not a static default.
        if ref_id not in self.param_ref_id_to_value:
            known = self._known_param_ref_values.get(ref_id)
            if known is not None:
                self.param_ref_id_to_value[ref_id] = known
        self._active_param_refs.add(ref_id)

    def mark_active_com_object(self, ref_id: str) -> None:
        self._active_com_object_refs.add(ref_id)

    def mark_assigned_from(self, target: str, source: str) -> None:
        """An Assign of the current pass wrote ``target`` from ``source``."""
        self._assigned_from[target] = source

    def mark_assigned_constant(self, target: str) -> None:
        """An Assign of the current pass wrote a fixed value into ``target``: a copy an
        earlier Assign of the pass made is written over."""
        self._assigned_from.pop(target, None)

    def assigned_from(self) -> dict[str, str]:
        """The param-refs the Assigns of the last pass wrote from another one, in this scope:
        target -> source."""
        return dict(self._assigned_from)

    def set_com_obj_instance_ref(self, ref_id: str, coir: ComObjectInstanceRef) -> None:
        self._com_obj_instance_refs[ref_id] = coir

    def get_com_obj_instance_ref(self, ref_id: str) -> ComObjectInstanceRef | None:
        result = self._com_obj_instance_refs.get(ref_id)
        if result is not None:
            return result
        if self._parent is not None:
            return self._parent.get_com_obj_instance_ref(ref_id)
        return None

    def com_obj_instance_ref_ids(self) -> set[str]:
        """Every com-object instance ref id known to this scope and its children.

        These are the com objects the device actually instantiated (its GroupObjectTree),
        provided when reconstructing state from a project. Empty for a device configured
        from scratch (no saved instances)."""
        result = set(self._com_obj_instance_refs)
        for child in self._children.values():
            result |= child.com_obj_instance_ref_ids()
        return result

    def alloc_position(self, alloc_id: str, default: int) -> int:
        return self._alloc_positions.get(alloc_id, default)

    def set_alloc_position(self, alloc_id: str, position: int) -> None:
        self._alloc_positions[alloc_id] = position

    def alloc_positions(self) -> dict[str, int]:
        """A copy of this scope's allocator positions."""
        return dict(self._alloc_positions)

    def set_alloc_positions(self, positions: dict[str, int]) -> None:
        self._alloc_positions = dict(positions)

    def reset_alloc_positions(self) -> None:
        """Start every allocator of this scope and the module scopes below at its start again."""
        self._alloc_positions.clear()
        for child in self._children.values():
            child.reset_alloc_positions()

    def reset_active(self) -> None:
        """Reset active ref sets ahead of the next traversal.

        The discovered-active snapshot is intentionally NOT cleared: it is set once by the
        discovery pass and consumed by the following render pass (which resets the live active
        sets in between)."""
        self._active_param_refs.clear()
        self._active_module_keys.clear()
        self._active_com_object_refs.clear()
        self._alloc_positions.clear()
        self._pass_values.clear()
        self._pass_texts.clear()
        self._assigned_from.clear()
        self._assign_targets.clear()
        for child in self._children.values():
            child.reset_active()

    def snapshot_discovered_active(self) -> None:
        """Freeze the current per-scope active params as the union-discovery result.

        Called after a full eval with union suppression disabled; the render pass then reads it via
        is_discovered_active() so a Union member's overlay decision does not depend on eval order."""
        self._discovered_active_param_refs = set(self._active_param_refs)
        for child in self._children.values():
            child.snapshot_discovered_active()

    def is_discovered_active(self, ref_id: str) -> bool:
        """Whether ``ref_id`` was reached (marked active) in the union-discovery pass, in this scope
        or a parent. Mirrors is_active_param but reads the frozen discovery snapshot."""
        if ref_id in self._discovered_active_param_refs:
            return True
        return (
            self._parent.is_discovered_active(ref_id)
            if self._parent is not None
            else False
        )

    def clear_gate(self) -> None:
        """Stop gating Chooses (this scope and its children) until the next update."""
        self._gate_param_refs = None
        for child in self._children.values():
            child.clear_gate()

    def update_gate_active(self) -> bool:
        """Gate the next pass by the params active now; whether that differs from before."""
        changed = self._gate_param_refs != self._active_param_refs
        self._gate_param_refs = set(self._active_param_refs)
        for child in self._children.values():
            changed = child.update_gate_active() or changed
        return changed

    def is_gate_active(self, ref_id: str) -> bool:
        """Whether a Choose on ``ref_id`` may select a branch: the param was active in the
        previous pass, in this scope or a parent. True for a scope without a previous pass."""
        if self._gate_param_refs is None or ref_id in self._gate_param_refs:
            return True
        return (
            self._parent.is_gate_active(ref_id) if self._parent is not None else False
        )

    def discard_active_refs(
        self, param_ref_ids: set[str], com_object_ref_ids: set[str]
    ) -> None:
        """Drop the given refs from the active sets (this scope and its children).

        Used to deactivate an inactive channel's parameters and com objects after the
        traversal, so a device configured with that channel disabled does not encode
        or expose them (matching the device's instantiated group objects)."""
        self._active_param_refs -= param_ref_ids
        self._active_com_object_refs -= com_object_ref_ids
        for child in self._children.values():
            child.discard_active_refs(param_ref_ids, com_object_ref_ids)

    def trim_to_active(self) -> None:
        """Drop inactive param-ref values, then recurse into module children.

        Unvisited module children are dropped so inactive modules encode nothing; their
        explicit values are kept for the instance to take again when it comes back.
        Active ref sets stay intact here for callers to read post-traversal.
        """
        inactive = self.param_ref_id_to_value.keys() - self._active_param_refs
        for key in inactive:
            del self.param_ref_id_to_value[key]
        self._text.clear()
        inactive_modules = self._children.keys() - self._active_module_keys.keys()
        for key in inactive_modules:
            self._dormant_children[key] = self._children.pop(key)
        for child in self._children.values():
            child.trim_to_active()

    def active_param_refs(self) -> set[str]:
        result = set(self._active_param_refs)
        for child in self._children.values():
            result.update(child.active_param_refs())
        return result

    def local_active_param_refs(self) -> set[str]:
        """The active ParameterRefs of this scope alone, by their local id."""
        return set(self._active_param_refs)

    def active_com_object_refs(self) -> set[str]:
        result = set(self._active_com_object_refs)
        for child in self._children.values():
            result.update(child.active_com_object_refs())
        return result

    def qualify(self, ref_id: str) -> str:
        return ref_id

    def qualify_local(self, ref_id: str) -> str:
        """Qualify ``ref_id`` in the scope that actually OWNS it, not the current one.

        A module-scoped Choose/Repeat can gate on an ancestor/application parameter (its ref is not
        module-def-local): that ref is marked active in the ancestor scope (unqualified), so its gate
        must stay unqualified too. Only a ref owned by THIS module scope (in its local value/default
        stores) gets the module-instance prefix. Prevents mis-qualifying inherited gate params, which
        would drop their com-objects from the chain-AND active set. (Com-object leaves are always
        current-scope-local, so they keep the plain ``qualify``.)"""
        if (
            ref_id in self._known_param_ref_values
            or ref_id in self.param_ref_id_to_value
            or ref_id in self._param_ref_defaults
        ):
            return self.qualify(ref_id)
        if self._parent is not None:
            return self._parent.qualify_local(ref_id)
        return self.qualify(ref_id)

    def find_scope_for_qualified(
        self, ref_id: str
    ) -> tuple[ParameterState, str] | None:
        # A project stores the instances side by side, a submodule instance beside the one it
        # is in, whose id starts its own: the innermost instance the id names is its scope.
        found: tuple[ParameterState, str] | None = None
        for child in self._children.values():
            result = child.find_scope_for_qualified(ref_id)
            if result is not None and (
                found is None
                or _instance_length(result[0]) > _instance_length(found[0])
            ):
                found = result
        return found

    def scopes(self) -> tuple[object, ...]:
        """The module scopes below this one in their order, each with its ids and the scopes
        below it: all :meth:`find_scope_for_qualified` goes by."""
        return tuple(child.scope_ids() for child in self._children.values())

    def scope_ids(self) -> tuple[object, ...]:
        """This scope's part of :meth:`scopes`."""
        return (None, None, self.scopes())

    def explicit_value(self, ref_id: str) -> str | None:
        """The value explicitly set in this scope, ignoring parents and defaults."""
        return self._known_param_ref_values.get(ref_id)

    def set_instance_ref(self, ref_id: str, value: str) -> None:
        found = self.find_scope_for_qualified(ref_id)
        if found is not None:
            scope, local = found
            scope.set(local, value)
        else:
            self.set(ref_id, value)

    def clear_instance_ref(self, ref_id: str) -> None:
        found = self.find_scope_for_qualified(ref_id)
        if found is not None:
            scope, local = found
            scope.param_ref_id_to_value.pop(local, None)
            scope._known_param_ref_values.pop(local, None)
        else:
            self.param_ref_id_to_value.pop(ref_id, None)
            self._known_param_ref_values.pop(ref_id, None)

    def module_child(
        self,
        module_id: str,
        repeat_idx: int = 1,
        arguments: dict[str, ModuleArg] | None = None,
        param_ref_defaults: dict[str, str] | None = None,
        arg_defaults: dict[str, str] | None = None,
        ref_id: str | None = None,
        param_ref_bases: dict[str, str] | None = None,
        in_repeat: bool = False,
    ) -> ModuleState:
        """Fetch or create the module instance state, linked into the tree.

        ``repeat_idx`` is the iteration of the ``Repeat`` the ``Module`` is reached in
        (``in_repeat``), 1 outside one."""
        key, path = self._instance_key(module_id, repeat_idx, in_repeat)
        self._active_module_keys[key] = None
        if key not in self._children:
            stored = self._stored_at_root(key)
            legacy = self._stored_legacy(module_id, repeat_idx)
            if legacy is not None:
                if stored is None:
                    # A copy for each enclosing instance; the stored one is dropped with the
                    # root's instances that no pass reaches.
                    stored = copy.deepcopy(legacy, {id(legacy._parent): legacy._parent})
                    stored.module_instance_id = key
                else:
                    stored.take_missing_values(legacy)
            if stored is not None:
                self._children[key] = stored
        if key not in self._children:
            child = ModuleState(
                key,
                arguments,
                param_ref_defaults=param_ref_defaults,
                arg_defaults=arg_defaults,
                ref_id=ref_id,
                param_ref_bases=param_ref_bases,
            )
            child._parent = self
            child.repeat_path = path
            dormant = self._dormant_children.pop(key, None)
            if dormant is not None:
                # The instance comes back with the values it had (its submodule instances
                # too), not with the defaults.
                child.take_missing_values(dormant)
                child._dormant_children.update(dormant._dormant_children)
                child._dormant_children.update(dormant._children)
            self._children[key] = child
        else:
            child = self._children[key]
            child._parent = self
            child.repeat_path = path
            if ref_id is not None:
                child.ref_id = ref_id
            if arguments is not None:
                child.arguments = arguments
            if param_ref_defaults is not None:
                child.set_param_ref_defaults(param_ref_defaults)
            if arg_defaults is not None:
                child.set_arg_defaults(arg_defaults)
            if param_ref_bases is not None:
                child.param_ref_bases = param_ref_bases
        return self._children[key]

    def _instance_key(
        self, module_id: str, repeat_idx: int, in_repeat: bool
    ) -> tuple[str, tuple[int, ...]]:
        """The id of the instance of ``module_id`` reached here, and its repeat path (the
        counters of the ``Repeat`` and ``Module`` elements it is nested in, outermost
        first). A module instance of the application is ``<Module>_MI-<iteration>``."""
        return f"{module_id}_MI-{repeat_idx}", (repeat_idx,) if in_repeat else ()

    def _stored_legacy(self, module_id: str, repeat_idx: int) -> ModuleState | None:
        """The state a project stored at the root for an instance of the submodule
        ``module_id`` under the id without its enclosing instance
        (``..._MD-1_SM-1_M-1_MI-1``), left there: that id stood for the submodule instance of
        every instance of the enclosing module, so each of them reads it. ``None`` for a
        module of the application, or when there is none."""
        if self._parent is None or "_SM-" not in module_id:
            return None
        root: ParameterState = self
        while root._parent is not None:
            root = root._parent
        legacy = f"{module_id}_MI-{repeat_idx}"
        if legacy in root._active_module_keys:
            return None
        # Set aside when the first evaluation did not reach it (see trim_to_active).
        found = root._children.get(legacy) or root._dormant_children.pop(legacy, None)
        if found is not None:
            root._children.setdefault(legacy, found)
        return found

    def take_missing_values(self, other: ParameterState) -> None:
        """Take the explicit values of ``other`` for the param-refs this scope has none for."""
        for ref_id, value in other._known_param_ref_values.items():
            if ref_id not in self._known_param_ref_values:
                self._known_param_ref_values[ref_id] = value
                self.param_ref_id_to_value.setdefault(ref_id, value)

    def _stored_at_root(self, key: str) -> ModuleState | None:
        """The state of the submodule instance ``key`` of this module instance that a project
        or seeding stored at the root (instances are stored side by side, see
        :meth:`GlobalState.from_project`), taken from there; ``None`` if there is none."""
        root: ParameterState = self
        while root._parent is not None:
            root = root._parent
        if root is self or key in root._active_module_keys:
            return None
        # A stored instance the first evaluation did not reach was set aside there (see
        # trim_to_active); it comes back with its values when its instance reaches it.
        found = root._children.pop(key, None)
        return found if found is not None else root._dormant_children.pop(key, None)

    def parameter_instance_refs(self) -> dict[str, str]:
        result = dict(self.param_ref_id_to_value)
        for child in self._children.values():
            result.update(child.parameter_instance_refs())
        return result

    def module_children(self) -> list[ModuleState]:
        return list(self._children.values())

    def reached_module_children(self) -> list[ModuleState]:
        """The module instances of this scope the last pass reached, in the order it reached
        them: the order of the ``Dynamic`` and of the iterations of a ``Repeat``."""
        return [
            self._children[key]
            for key in self._active_module_keys
            if key in self._children
        ]

    def is_active_in_scope(self, ref_id: str) -> bool:
        """Whether ``ref_id`` (a local id) was active in the last pass, in this scope or a
        parent."""
        if ref_id in self._active_param_refs:
            return True
        return (
            self._parent.is_active_in_scope(ref_id)
            if self._parent is not None
            else False
        )

    def relative_param_values(self) -> list[tuple[str, str]]:
        """(def-relative ref_id, value) pairs across every scope.

        Keys stay unqualified, so they resolve directly in
        ApplicationIndexer.parameter_refs no matter the module nesting.
        """
        result = list(self.param_ref_id_to_value.items())
        for child in self._children.values():
            result.extend(child.relative_param_values())
        return result

    def module_instances(self) -> list[tuple[str, str, dict[str, ModuleArg]]]:
        result: list[tuple[str, str, dict[str, ModuleArg]]] = []
        for child in self._children.values():
            result.extend(child.module_instances())
        return result

    def layout(self) -> tuple[object, ...]:
        """What this scope and the module scopes below hold, each in its order: the scope's
        ids, its values and texts, its group object instances, its defaults and arguments,
        and what an evaluation leaves (active refs, gates, allocator positions). Two states of
        one program with the same layout evaluate alike."""
        # A copy of a mapping with its keys in their order compares like its items in their
        # order, and is made much faster (a state holds thousands of values).
        return (
            type(self).__name__,
            self._own_layout(),
            dict(self.param_ref_id_to_value),
            tuple(self.param_ref_id_to_value),
            dict(self._known_param_ref_values),
            tuple(self._known_param_ref_values),
            tuple(self._text.items()),
            tuple(self._com_obj_instance_refs.items()),
            tuple(self._param_ref_defaults.items()),
            tuple(self._alloc_positions.items()),
            tuple(self._active_param_refs),
            tuple(self._active_module_keys),
            tuple(self._active_com_object_refs),
            tuple(self._discovered_active_param_refs),
            None if self._gate_param_refs is None else tuple(self._gate_param_refs),
            tuple((key, child.layout()) for key, child in self._children.items()),
        )

    def _own_layout(self) -> tuple[object, ...]:
        """The part of :meth:`layout` of a kind of scope."""
        return ()


class GlobalState(ParameterState):
    """Root parameter state; module children hang beneath it."""

    @classmethod
    def from_project(
        cls,
        parameter_instance_refs: list[ParameterInstanceRef] | None = None,
        module_instances: list[ModuleInstance] | None = None,
        com_object_instance_refs: list[ComObjectInstanceRef] | None = None,
        app_id: str | None = None,
        padded_ref_ids: Mapping[str, str] | None = None,
        parameter_values: Mapping[str, str] | None = None,
    ) -> GlobalState:
        """Rebuild a GlobalState tree from saved project data.

        ``parameter_values`` holds values by ref id as ``parameter_instance_refs`` do (after
        them): a seeding with thousands of values needs no instance ref made for each.

        A module instance id is either qualified with the application id ``app_id`` or relative to
        it (``MD-1_M-1_MI-1``, as a ``.knxproj`` stores it); its state is keyed by the qualified id,
        the key the evaluated Module looks it up by (see :meth:`module_child`).

        ``padded_ref_ids`` maps the unpadded id of each parameter ref the application writes
        with leading zeros to the application's spelling (see
        :attr:`ApplicationIndexer.padded_ref_ids`): a value stored under another spelling of such
        an id is the value of that parameter ref. When both spellings are stored, the value under
        the application's spelling counts."""
        root = cls()
        exact: set[tuple[int, str]] = set()

        def put(scope: ParameterState, ref_id: str, value: str) -> None:
            if padded_ref_ids:
                own = padded_ref_ids.get(unpadded_id(ref_id))
                if own is not None and own != ref_id:
                    if (id(scope), own) in exact:
                        return
                    ref_id = own
                else:
                    exact.add((id(scope), ref_id))
            scope.set(ref_id, value)

        # Instance id -> (its state, the definition its parameters' local ids start with). A
        # submodule instance's parameters keep their ids below the top-level module definition
        # (``MD-4_SM-1_P-1_R-1``, qualified ``MD-4_M-15_MI-1_SM-1_M-1_MI-1-1-1_SM-1_P-1_R-1``).
        stored: dict[str, tuple[ModuleState, str]] = {}

        def add(instance_id: str, definition: str) -> None:
            key = _qualified(instance_id, app_id)
            submodule = _submodule_definition(instance_id)
            if submodule is not None:
                definition, base = submodule
            else:
                base = definition
            ms = ModuleState(key, ref_id=_qualified(definition, app_id))
            ms._parent = root
            root._children[key] = ms
            stored[instance_id] = (ms, base)

        for mi in module_instances or []:
            if mi.id is None or mi.ref_id is None:
                continue
            add(mi.id, mi.ref_id)
        # A value of a module instance the saved list lacks (created by a later edit) still belongs
        # to that instance, not to the application.
        known = {_qualified(mid, app_id) for mid in stored}
        pairs: Iterable[tuple[str, str | None]] = (
            parameter_values.items()
            if parameter_instance_refs is None and parameter_values is not None
            else [
                *((pir.ref_id, pir.value) for pir in parameter_instance_refs or []),
                *(parameter_values or {}).items(),
            ]
        )
        for ref_id, value in pairs:
            if value is None:
                continue
            match = _instance_in_ref(ref_id)
            if match is None or _qualified(match[0], app_id) in known:
                continue
            known.add(_qualified(match[0], app_id))
            add(match[0], match[1])
        # The innermost instance a value is of: a submodule instance's id starts with that of
        # its enclosing instance. With many instances, the parts of the value's ref id are
        # looked up instead of every instance being searched for.
        by_length = sorted(stored.items(), key=lambda item: len(item[0]), reverse=True)
        instances = _instance_ids(tuple(mid for mid, _ in by_length))
        for ref_id, value in pairs:
            if value is None:
                continue
            owner = instances.owner(ref_id)
            if owner is None:
                put(root, ref_id, value)
                continue
            ms, base = stored[owner]
            ms.module_instance_id, local = instances.place(ref_id, owner, base)
            put(ms, local, value)

        for coir in com_object_instance_refs or []:
            root._com_obj_instance_refs[coir.ref_id] = coir

        return root


class ModuleState(ParameterState):
    """One module instance's state, keyed by local (def-relative) ref IDs.

    parameter_instance_refs() qualifies keys by swapping module_instance_id for
    the shared prefix, e.g. M-..._MD-1_P-5_R-5 -> M-..._MD-1_M-100_MI-2_P-5_R-5.
    """

    __slots__ = (
        "_arg_defaults",
        "arguments",
        "module_instance_id",
        "param_ref_bases",
        "ref_id",
        "repeat_path",
    )

    _SHARED_FIELDS: ClassVar[frozenset[str]] = ParameterState._SHARED_FIELDS | {
        "_arg_defaults",
        "param_ref_bases",
    }

    def __init__(
        self,
        module_instance_id: str,
        arguments: dict[str, ModuleArg] | None = None,
        param_ref_defaults: dict[str, str] | None = None,
        arg_defaults: dict[str, str] | None = None,
        ref_id: str | None = None,
        param_ref_bases: dict[str, str] | None = None,
    ) -> None:
        super().__init__(param_ref_defaults=param_ref_defaults)
        self.module_instance_id = module_instance_id
        self.ref_id: str | None = ref_id
        self.arguments: dict[str, ModuleArg] = arguments or {}
        self._arg_defaults: dict[str, str] = arg_defaults or {}
        # ParameterRef -> the argument whose value is the parameter's value (its Parameter's
        # BaseValue). While the argument has a value, the parameter's own value, stored or
        # default, does not count.
        self.param_ref_bases: dict[str, str] = param_ref_bases or {}
        # The counters of the Repeat and Module elements the instance is nested in, outermost
        # first (see ``RepeatIndex`` of a project's ModuleInstance): set when it is evaluated.
        self.repeat_path: tuple[int, ...] = ()

    def _instance_key(
        self, module_id: str, repeat_idx: int, in_repeat: bool
    ) -> tuple[str, tuple[int, ...]]:
        """A submodule instance is named after its enclosing instance, as a project stores it:
        ``MD-4_M-15_MI-1`` + ``_SM-1_M-1_MI-<counters>``, the counters being those of every
        Repeat and Module it is nested in (``1-1-2``: the first iteration of the enclosing
        instance, its Module, the second iteration of the submodule's Repeat)."""
        at = module_id.rfind("_SM-")
        if at == -1:
            return super()._instance_key(module_id, repeat_idx, in_repeat)
        path = (*self.repeat_path, 1, *((repeat_idx,) if in_repeat else ()))
        counters = "-".join(str(n) for n in path)
        return f"{self.module_instance_id}_{module_id[at + 1 :]}_MI-{counters}", path

    def _local_base(self) -> str | None:
        """The id the local ids of this instance's parameters and objects start with: its
        definition, or for a submodule instance named after its enclosing instance, the
        top-level module definition (``MD-4`` for ``MD-4_SM-1_P-1_R-1``)."""
        ref_id = self.ref_id
        if ref_id is None:
            return None
        at = ref_id.find("_SM-")
        if at != -1 and "_SM-" in self.module_instance_id.partition("_MI-")[2]:
            return ref_id[:at]
        return ref_id

    def get(self, ref_id: str) -> str | None:
        base = self.param_ref_bases.get(ref_id)
        arg = self.get_arg(base) if base is not None else None
        if arg is not None and arg.value is not None:
            return str(arg.value)
        return super().get(ref_id)

    def get_arg_defaults(self) -> dict[str, str]:
        return self._arg_defaults

    def set_arg_defaults(self, arg_defaults: dict[str, str]) -> None:
        self._arg_defaults = arg_defaults

    def get_arg(self, ref_id: str) -> ModuleNumericArg | None:
        arg = self.arguments.get(ref_id)
        return arg if isinstance(arg, ModuleNumericArg) else None

    def _own_layout(self) -> tuple[object, ...]:
        return (
            self.module_instance_id,
            self.ref_id,
            tuple(self.arguments.items()),
            tuple(self._arg_defaults.items()),
            tuple(self.param_ref_bases.items()),
        )

    def as_module_instance(self) -> tuple[str, str, dict[str, ModuleArg]]:
        instance_id = self.module_instance_id
        ref_id = (
            self.ref_id if self.ref_id is not None else instance_id.rsplit("_MI-", 1)[0]
        )
        args = {k[k.find("_MD-") + 1 :]: v for k, v in self.arguments.items()}
        return instance_id, ref_id, args

    def qualify(self, ref_id: str) -> str:
        return self._qualify(ref_id)

    def scope_ids(self) -> tuple[object, ...]:
        return (self.module_instance_id, self.ref_id, self.scopes())

    def find_scope_for_qualified(
        self, ref_id: str
    ) -> tuple[ParameterState, str] | None:
        # A submodule instance's ids start with its parent instance's
        # (``..._MD-7_M-30_MI-1_SM-1_M-1_MI-1-1_SM-1_P-1_R-1`` inside ``..._MD-7_M-30_MI-1``):
        # the innermost scope is looked for first.
        for child in self._children.values():
            result = child.find_scope_for_qualified(ref_id)
            if result is not None:
                return result
        mid = self.module_instance_id
        if not ref_id.startswith(mid + "_"):
            return None
        suffix = ref_id[len(mid) :]
        base = self._local_base()
        if base is None:
            return None
        return (self, base + suffix)

    def _qualify(self, ref_id: str) -> str:
        base = self._local_base()
        if base is not None and ref_id.startswith(base + "_"):
            return self.module_instance_id + ref_id[len(base) :]
        return _qualified_in(self.module_instance_id, ref_id)

    def active_param_refs(self) -> set[str]:
        result = {self.qualify(ref) for ref in self._active_param_refs}
        for child in self._children.values():
            result.update(child.active_param_refs())
        return result

    def active_com_object_refs(self) -> set[str]:
        result = {self.qualify(ref) for ref in self._active_com_object_refs}
        for child in self._children.values():
            result.update(child.active_com_object_refs())
        return result

    def parameter_instance_refs(self) -> dict[str, str]:
        result = {self._qualify(k): v for k, v in self.param_ref_id_to_value.items()}
        for child in self._children.values():
            result.update(child.parameter_instance_refs())
        return result

    def module_instances(self) -> list[tuple[str, str, dict[str, ModuleArg]]]:
        result: list[tuple[str, str, dict[str, ModuleArg]]] = [
            self.as_module_instance()
        ]
        for child in self._children.values():
            result.extend(child.module_instances())
        return result


__all__ = [
    "GlobalState",
    "ModuleState",
    "compute_arg_defaults",
    "compute_param_ref_defaults",
]

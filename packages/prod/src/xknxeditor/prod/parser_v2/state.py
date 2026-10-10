from __future__ import annotations

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
        "_children",
        "_com_obj_instance_refs",
        "_discovered_active_param_refs",
        "_gate_param_refs",
        "_known_param_ref_values",
        "_param_ref_defaults",
        "_parent",
        "_text",
        "param_ref_id_to_value",
    )

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
        self._active_module_keys: set[str] = set()
        self._active_com_object_refs: set[str] = set()
        self._alloc_positions: dict[str, int] = {}
        self._param_ref_defaults: dict[str, str] = param_ref_defaults or {}
        self._com_obj_instance_refs: dict[str, ComObjectInstanceRef] = {}

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
        self.param_ref_id_to_value[ref_id] = value
        self._known_param_ref_values[ref_id] = value

    def set_text(self, ref_id: str, text: str) -> None:
        self._text[ref_id] = text

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

    def reset_active(self) -> None:
        """Reset active ref sets ahead of the next traversal.

        The discovered-active snapshot is intentionally NOT cleared: it is set once by the
        discovery pass and consumed by the following render pass (which resets the live active
        sets in between)."""
        self._active_param_refs.clear()
        self._active_module_keys.clear()
        self._active_com_object_refs.clear()
        self._alloc_positions.clear()
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

        Unvisited module children are dropped so inactive modules encode nothing.
        Active ref sets stay intact here for callers to read post-traversal.
        """
        inactive = self.param_ref_id_to_value.keys() - self._active_param_refs
        for key in inactive:
            del self.param_ref_id_to_value[key]
        self._text.clear()
        inactive_modules = self._children.keys() - self._active_module_keys
        for key in inactive_modules:
            del self._children[key]
        for child in self._children.values():
            child.trim_to_active()

    def active_param_refs(self) -> set[str]:
        result = set(self._active_param_refs)
        for child in self._children.values():
            result.update(child.active_param_refs())
        return result

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
        for child in self._children.values():
            result = child.find_scope_for_qualified(ref_id)
            if result is not None:
                return result
        return None

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
    ) -> ModuleState:
        """Fetch or create the module instance state, linked into the tree."""
        key = f"{module_id}_MI-{repeat_idx}"
        self._active_module_keys.add(key)
        if key not in self._children:
            child = ModuleState(
                key,
                arguments,
                param_ref_defaults=param_ref_defaults,
                arg_defaults=arg_defaults,
                ref_id=ref_id,
            )
            child._parent = self
            self._children[key] = child
        else:
            child = self._children[key]
            child._parent = self
            if ref_id is not None:
                child.ref_id = ref_id
            if arguments is not None:
                child.arguments = arguments
            if param_ref_defaults is not None:
                child.set_param_ref_defaults(param_ref_defaults)
            if arg_defaults is not None:
                child.set_arg_defaults(arg_defaults)
        return self._children[key]

    def parameter_instance_refs(self) -> dict[str, str]:
        result = dict(self.param_ref_id_to_value)
        for child in self._children.values():
            result.update(child.parameter_instance_refs())
        return result

    def module_children(self) -> list[ModuleState]:
        return list(self._children.values())

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


class GlobalState(ParameterState):
    """Root parameter state; module children hang beneath it."""

    @classmethod
    def from_project(
        cls,
        parameter_instance_refs: list[ParameterInstanceRef] | None = None,
        module_instances: list[ModuleInstance] | None = None,
        com_object_instance_refs: list[ComObjectInstanceRef] | None = None,
    ) -> GlobalState:
        """Rebuild a GlobalState tree from saved project data."""
        root = cls()
        mid_to_mdid: dict[str, str] = {}
        for mi in module_instances or []:
            if mi.id is None or mi.ref_id is None:
                continue
            ms = ModuleState(mi.id, ref_id=mi.ref_id)
            ms._parent = root
            root._children[mi.id] = ms
            mid_to_mdid[mi.id] = mi.ref_id
        for pir in parameter_instance_refs or []:
            if pir.value is None:
                continue
            ref_id, value = pir.ref_id, pir.value
            for mid, ms in root._children.items():
                p = f"_{ref_id}_".find(f"_{mid}_")
                if p != -1:
                    app_prefix = ref_id[:p]
                    suffix = ref_id[p + len(mid) :]
                    ms.module_instance_id = app_prefix + mid
                    ms.set(app_prefix + mid_to_mdid[mid] + suffix, value)
                    break
            else:
                root.set(ref_id, value)

        for coir in com_object_instance_refs or []:
            root._com_obj_instance_refs[coir.ref_id] = coir

        return root


class ModuleState(ParameterState):
    """One module instance's state, keyed by local (def-relative) ref IDs.

    parameter_instance_refs() qualifies keys by swapping module_instance_id for
    the shared prefix, e.g. M-..._MD-1_P-5_R-5 -> M-..._MD-1_M-100_MI-2_P-5_R-5.
    """

    __slots__ = ("_arg_defaults", "arguments", "module_instance_id", "ref_id")

    def __init__(
        self,
        module_instance_id: str,
        arguments: dict[str, ModuleArg] | None = None,
        param_ref_defaults: dict[str, str] | None = None,
        arg_defaults: dict[str, str] | None = None,
        ref_id: str | None = None,
    ) -> None:
        super().__init__(param_ref_defaults=param_ref_defaults)
        self.module_instance_id = module_instance_id
        self.ref_id: str | None = ref_id
        self.arguments: dict[str, ModuleArg] = arguments or {}
        self._arg_defaults: dict[str, str] = arg_defaults or {}

    def get_arg_defaults(self) -> dict[str, str]:
        return self._arg_defaults

    def set_arg_defaults(self, arg_defaults: dict[str, str]) -> None:
        self._arg_defaults = arg_defaults

    def get_arg(self, ref_id: str) -> ModuleNumericArg | None:
        arg = self.arguments.get(ref_id)
        return arg if isinstance(arg, ModuleNumericArg) else None

    def as_module_instance(self) -> tuple[str, str, dict[str, ModuleArg]]:
        instance_id = self.module_instance_id
        ref_id = (
            self.ref_id if self.ref_id is not None else instance_id.rsplit("_MI-", 1)[0]
        )
        args = {k[k.find("_MD-") + 1 :]: v for k, v in self.arguments.items()}
        return instance_id, ref_id, args

    def qualify(self, ref_id: str) -> str:
        return self._qualify(ref_id)

    def find_scope_for_qualified(
        self, ref_id: str
    ) -> tuple[ParameterState, str] | None:
        mid = self.module_instance_id
        if not ref_id.startswith(mid + "_"):
            return None
        for child in self._children.values():
            result = child.find_scope_for_qualified(ref_id)
            if result is not None:
                return result
        suffix = ref_id[len(mid) :]
        if self.ref_id is None:
            return None
        return (self, self.ref_id + suffix)

    def _qualify(self, ref_id: str) -> str:
        i, n = 0, min(len(self.module_instance_id), len(ref_id))
        while i < n and self.module_instance_id[i] == ref_id[i]:
            i += 1
        return self.module_instance_id + ref_id[i - 1 :]

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

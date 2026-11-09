"""The load procedures of a device's module instances.

A module definition can carry load procedures of its own, each with a merge id. A load
procedure of the application (or of an enclosing module) names the place for them with
``LdCtrlMerge MergeId="<id>"``: there the procedure of every module instance with that merge id
runs, once per instance, in the order of the instances. Each instance runs it with its own
arguments: a control's ``BaseObjIdx``, ``BaseOccurrence`` and ``BaseStartElement`` name an
argument of the module whose value is added to its ``ObjIdx``, ``Occurrence`` and
``StartElement``; a ``choose`` tests the instance's parameter.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import fields
from typing import Any, cast

from xknxeditor.namespaces.intermediate.ld_ctrl_base_choose_t import LdCtrlBaseChoose
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_invoke_function_prop_t import (
    LdCtrlInvokeFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_merge_t import LdCtrlMerge
from xknxeditor.namespaces.intermediate.ld_ctrl_read_function_prop_t import (
    LdCtrlReadFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.module_def_ld_ctrl_base_choose_t import (
    ModuleDefLdCtrlBaseChoose,
)
from xknxeditor.namespaces.intermediate.module_def_ld_ctrl_compare_prop_t import (
    ModuleDefLdCtrlCompareProp,
)
from xknxeditor.namespaces.intermediate.module_def_ld_ctrl_invoke_function_prop_t import (
    ModuleDefLdCtrlInvokeFunctionProp,
)
from xknxeditor.namespaces.intermediate.module_def_ld_ctrl_read_function_prop_t import (
    ModuleDefLdCtrlReadFunctionProp,
)
from xknxeditor.namespaces.intermediate.module_def_ld_ctrl_write_prop_t import (
    ModuleDefLdCtrlWriteProp,
)

from .application_indexer import ApplicationIndexer
from .nodes.choose import satisfies
from .state import ModuleState, ParameterState

logger = logging.getLogger(__name__)

_ModuleControl = (
    ModuleDefLdCtrlWriteProp
    | ModuleDefLdCtrlCompareProp
    | ModuleDefLdCtrlInvokeFunctionProp
    | ModuleDefLdCtrlReadFunctionProp
)

# A module's property control and the control it stands for once its arguments are applied.
_CONTROLS: dict[type[Any], type[Any]] = {
    ModuleDefLdCtrlWriteProp: LdCtrlWriteProp,
    ModuleDefLdCtrlCompareProp: LdCtrlCompareProp,
    ModuleDefLdCtrlInvokeFunctionProp: LdCtrlInvokeFunctionProp,
    ModuleDefLdCtrlReadFunctionProp: LdCtrlReadFunctionProp,
}

# The attribute naming an argument, and the attribute the argument's value is added to.
_BASES = (
    ("base_obj_idx", "obj_idx"),
    ("base_occurrence", "occurrence"),
    ("base_start_element", "start_element"),
)


def module_load_controls(
    scope: ParameterState, idx: ApplicationIndexer
) -> dict[int, list[object]]:
    """The load controls of the module instances of ``scope`` (and of the submodule
    instances an instance's own procedures do not merge), by merge id, each instance's in
    the order the instances are reached."""
    result: dict[int, list[object]] = {}
    for instance in scope.reached_module_children():
        for merge_id, controls in _instance_controls(instance, idx).items():
            result.setdefault(merge_id, []).extend(controls)
    return result


def _instance_controls(
    instance: ModuleState, idx: ApplicationIndexer
) -> dict[int, list[object]]:
    """The load controls of one module instance by merge id: its definition's procedures, the
    submodule instances' controls in their merge points, and the submodule instances'
    controls of the merge ids the definition does not merge."""
    submodules = module_load_controls(instance, idx)
    definition = (
        idx.module_defs.get(instance.ref_id) if instance.ref_id is not None else None
    )
    static = definition.static if definition is not None else None
    procedures = static.load_procedures if static is not None else None
    result: dict[int, list[object]] = {}
    merged: set[int] = set()
    for procedure in procedures.load_procedure if procedures is not None else ():
        merged.update(_merge_ids(procedure.choice))
        result.setdefault(procedure.merge_id, []).extend(
            _resolve(procedure.choice, instance, idx, submodules)
        )
    for merge_id, controls in submodules.items():
        if merge_id not in merged:
            result.setdefault(merge_id, []).extend(controls)
    return result


def _merge_ids(controls: Sequence[object]) -> set[int]:
    """The merge ids the ``LdCtrlMerge`` in ``controls`` name, in every branch."""
    result: set[int] = set()
    for control in controls:
        if isinstance(control, LdCtrlMerge):
            result.add(control.merge_id)
        elif isinstance(control, (LdCtrlBaseChoose, ModuleDefLdCtrlBaseChoose)):
            for when in control.when:
                result.update(_merge_ids(when.choice))
    return result


def _resolve(
    controls: Sequence[object],
    instance: ModuleState,
    idx: ApplicationIndexer,
    submodules: dict[int, list[object]],
) -> list[object]:
    """``controls`` as ``instance`` runs them: each ``choose`` replaced by the branches its
    parameter selects (none while the parameter is inactive), each merge point by the
    submodule instances' controls, the property controls with the instance's arguments."""
    result: list[object] = []
    for control in controls:
        if isinstance(control, (LdCtrlBaseChoose, ModuleDefLdCtrlBaseChoose)):
            ref = control.param_ref_id
            if not instance.is_active_in_scope(ref):
                continue
            value = instance.get(ref)
            if value is None:
                value = idx.default_value(ref)
            if value is None:
                continue
            selected = [
                when
                for when in control.when
                if when.test is not None and satisfies(when.test, value)
            ] or [when for when in control.when if when.default]
            for when in selected:
                result.extend(_resolve(when.choice, instance, idx, submodules))
        elif isinstance(control, LdCtrlMerge):
            result.extend(submodules.get(control.merge_id, []))
        elif type(control) in _CONTROLS:
            resolved = _with_arguments(cast("_ModuleControl", control), instance)
            if resolved is not None:
                result.append(resolved)
        else:
            result.append(control)
    return result


def _with_arguments(control: _ModuleControl, instance: ModuleState) -> object | None:
    """The plain control ``control`` stands for in ``instance``: the values of the arguments
    its ``Base*`` attributes name added to the object index, occurrence and start element.
    ``None`` when the instance has no value for such an argument."""
    values: dict[str, Any] = {
        f.name: getattr(control, f.name) for f in fields(_CONTROLS[type(control)])
    }
    for base, target in _BASES:
        argument = getattr(control, base, None)
        if argument is None:
            continue
        arg = instance.get_arg(argument)
        if arg is None or arg.value is None:
            logger.debug(
                "%s of module instance %s: argument %s has no value, not run",
                type(control).__name__,
                instance.module_instance_id,
                argument,
            )
            return None
        if target == "obj_idx":
            # An object addressed by its type keeps that, unless an index is added.
            if arg.value:
                values[target] = (values[target] or 0) + arg.value
        else:
            values[target] = values[target] + arg.value
    return _CONTROLS[type(control)](**values)


__all__ = ["module_load_controls"]

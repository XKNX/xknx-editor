from __future__ import annotations

from xknxeditor.namespaces.intermediate import Assign

from ..context import EvalContext
from ..ui import UiNode
from .base import DynamicNode


class AssignNode(DynamicNode):
    """Writes a value into a parameter (stored like any other value) when it starts applying
    and whenever its source changes (see :meth:`ParameterState.run_assign`); state-only,
    renders nothing."""

    def __init__(self, elem: Assign):
        self._elem = elem

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        # The Assign applies, also while its source has no value.
        key = id(self._elem)
        ctx.note_assign(key)
        target = self._elem.target_param_ref_ref
        # An assigned parameter is active like a shown one, and so is its source.
        ctx.mark_active_param(target)
        if self._elem.source_param_ref_ref is not None:
            ctx.mark_active_param(self._elem.source_param_ref_ref)
        if self._elem.value is not None:
            # The last Assign of the pass decides where the target's value comes from.
            ctx.mark_assigned_constant(target)
            ctx.run_assign(key, target, self._elem.value, from_source=False)
        elif self._elem.source_param_ref_ref is not None:
            ctx.mark_assigned_from(target, self._elem.source_param_ref_ref)
            value = ctx.get(self._elem.source_param_ref_ref)
            ctx.run_assign(key, target, value, from_source=True)
        return []

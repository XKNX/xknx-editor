from __future__ import annotations

from xknxeditor.namespaces.intermediate import Assign

from ..context import EvalContext
from ..ui import UiNode
from .base import DynamicNode


class AssignNode(DynamicNode):
    """Writes a value into a parameter (stored like any other value); state-only, renders
    nothing."""

    def __init__(self, elem: Assign):
        self._elem = elem

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        # The Assign applies, also while its source has no value.
        ctx.note_assign(id(self._elem))
        # An assigned parameter is active like a shown one, and so is its source.
        ctx.mark_active_param(self._elem.target_param_ref_ref)
        if self._elem.source_param_ref_ref is not None:
            ctx.mark_active_param(self._elem.source_param_ref_ref)
        if self._elem.value is not None:
            # The last Assign of the pass decides where the target's value comes from.
            ctx.mark_assigned_constant(self._elem.target_param_ref_ref)
            ctx.assign(self._elem.target_param_ref_ref, self._elem.value)
        elif self._elem.source_param_ref_ref is not None:
            ctx.mark_assigned_from(
                self._elem.target_param_ref_ref, self._elem.source_param_ref_ref
            )
            value = ctx.get(self._elem.source_param_ref_ref)
            if value is not None:
                ctx.assign(self._elem.target_param_ref_ref, value)
        return []

from __future__ import annotations

from xknxeditor.namespaces.intermediate import Assign

from ..context import EvalContext
from ..ui import UiNode
from .base import DynamicNode


class AssignNode(DynamicNode):
    """Writes a value into a parameter; state-only, renders nothing."""

    def __init__(self, elem: Assign):
        self._elem = elem

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        # An assigned parameter is active like a shown one.
        ctx.mark_active_param(self._elem.target_param_ref_ref)
        if self._elem.value is not None:
            ctx.set(self._elem.target_param_ref_ref, self._elem.value)
        elif self._elem.source_param_ref_ref is not None:
            value = ctx.get(self._elem.source_param_ref_ref)
            if value is not None:
                ctx.set(self._elem.target_param_ref_ref, value)
        return []

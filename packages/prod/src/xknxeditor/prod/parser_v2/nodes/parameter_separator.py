from __future__ import annotations

from xknxeditor.namespaces.intermediate import ParameterSeparator
from xknxeditor.namespaces.intermediate.access_t import Access

from .._name import apply_text_args, fill_name
from ..context import EvalContext
from ..ui import UiNode
from ..ui.separator import UiSeparator
from .base import DynamicNode


class ParameterSeparatorNode(DynamicNode):
    """Leaf: label or divider sitting between block parameters."""

    def __init__(self, elem: ParameterSeparator):
        self._elem = elem

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        if self._elem.access == Access.NONE:
            return []
        raw = self._elem.text
        if raw:
            template = apply_text_args(raw, ctx.get_arg_defaults())
            name_value = (
                ctx.get(self._elem.text_parameter_ref_id)
                if self._elem.text_parameter_ref_id
                else None
            )
            text: str | None = (
                fill_name(template, name_value or "", keep_whitespace=True) or None
            )
        else:
            text = None
        elem = self._elem
        return [
            UiSeparator(
                id=elem.id,
                text=text,
                cell=elem.cell,
                hint=elem.uihint.value if elem.uihint is not None else None,
                alignment=elem.text_alignment.value
                if elem.text_alignment is not None
                else None,
                icon=elem.icon,
            )
        ]

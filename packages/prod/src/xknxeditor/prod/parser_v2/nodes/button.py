from __future__ import annotations

from xknxeditor.namespaces.intermediate import Button
from xknxeditor.namespaces.intermediate.access_t import Access

from .._name import apply_text_args, fill_name
from ..context import EvalContext
from ..ui import UiNode
from ..ui.button import UiButton
from .base import DynamicNode


class ButtonNode(DynamicNode):
    """A clickable button rendered inside a parameter block."""

    def __init__(self, elem: Button):
        self._elem = elem

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        elem = self._elem
        if elem.access == Access.NONE:
            return []
        args = ctx.get_arg_defaults()
        text = apply_text_args(elem.text, ctx.text_args())
        if elem.text_parameter_ref_id:
            text = fill_name(text, ctx.get(elem.text_parameter_ref_id) or "")
        params = elem.event_handler_parameters
        return [
            UiButton(
                id=ctx.qualify(elem.id),
                text=text,
                cell=elem.cell,
                read_only=elem.access == Access.READ,
                icon=elem.icon,
                name=elem.name,
                handler=elem.event_handler,
                handler_parameters=apply_text_args(params, args) if params else None,
                online=elem.event_handler_online.value
                if elem.event_handler_online is not None
                else None,
                module_instance_id=ctx.module_instance_id,
            )
        ]

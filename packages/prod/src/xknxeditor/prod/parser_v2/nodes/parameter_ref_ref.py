from __future__ import annotations

from xknxeditor.namespaces.intermediate import ParameterRefRef, ParameterType
from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.parameter_base_t import ParameterBase
from xknxeditor.namespaces.intermediate.parameter_ref_t import ParameterRef

from .._name import apply_text_args
from ..context import EvalContext
from ..ui import UiNode
from ..ui.parameter import UiParameter, resolve_widget
from .base import DynamicNode


class ParameterRefRefNode(DynamicNode):
    """Leaf: a parameter widget placed straight into the UI."""

    def __init__(
        self,
        elem: ParameterRefRef,
        param_ref: ParameterRef,
        param: ParameterBase,
        param_type: ParameterType,
    ) -> None:
        self._elem = elem
        self._param_ref = param_ref
        self._param = param
        self._param_type = param_type

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        ctx.mark_active_param(self._elem.ref_id)
        access = (
            self._param_ref.access
            if self._param_ref.access is not None
            else self._param.access
        )
        if self._param_type.plugin and access is not Access.NONE:
            access = Access.READ
        if access is Access.NONE:
            return []
        local_ref_id = self._elem.ref_id
        arg_defaults = ctx.text_args()
        static_label = apply_text_args(
            self._param_ref.text or self._param.text or "", arg_defaults
        )
        text_ref = self._param_ref.text_parameter_ref_id
        label = (ctx.get(text_ref) if text_ref else None) or static_label
        raw_suffix = self._param_ref.suffix_text or self._param.suffix_text
        suffix = apply_text_args(raw_suffix, arg_defaults) if raw_suffix else raw_suffix
        default_value = self._param_ref.value or self._param.value or ""
        value = ctx.get(local_ref_id) or default_value
        return [
            UiParameter(
                ref_id=ctx.qualify(local_ref_id),
                label=label,
                value=value,
                widget=resolve_widget(self._param_type),
                default_value=default_value,
                indent_level=self._elem.indent_level,
                suffix=suffix,
                access=access,
                icon=self._elem.icon,
                cell=self._elem.cell,
                text_alignment=self._param_type.text_alignment,
                help_context=self._elem.help_context,
            )
        ]

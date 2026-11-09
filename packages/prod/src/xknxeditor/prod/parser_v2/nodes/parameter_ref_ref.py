from __future__ import annotations

from xknxeditor.namespaces.intermediate import ParameterRefRef, ParameterType
from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.parameter_base_t import ParameterBase
from xknxeditor.namespaces.intermediate.parameter_ref_t import ParameterRef

from .._name import apply_text_args
from ..context import EvalContext
from ..ui import UiNode
from ..ui.parameter import UiParameter, Widget, resolve_widget
from .base import DynamicNode

# UiParameters a node keeps at most in each of its two generations (a node of a module
# definition places one per instance, each pass).
_MADE = 512


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
        # Built on first use: it depends on the parameter type only, and each settle evaluates
        # the tree several times.
        self._widget: Widget = None
        self._widget_resolved = False
        # The UiParameters made so far, by what differs between them (the rest, the access
        # too, is this node's): each pass of each evaluation places the same parameters again.
        self._made: dict[tuple[str, str, str, str | None], UiParameter] = {}
        # The generation before: what is used again moves back into ``_made``.
        self._made_before: dict[tuple[str, str, str, str | None], UiParameter] = {}

    def _get_widget(self) -> Widget:
        if not self._widget_resolved:
            self._widget = resolve_widget(self._param_type)
            self._widget_resolved = True
        return self._widget

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        ctx.mark_active_param(self._elem.ref_id)
        access = (
            self._param_ref.access
            if self._param_ref.access is not None
            else self._param.access
        )
        if self._param_type.plugin and access is not Access.NONE:
            access = Access.READ
        explorer = ctx.explore
        if access is Access.NONE and explorer is None:
            return []
        parameter = self._ui_parameter(ctx, access)
        if explorer is not None:
            explorer.record(parameter)
            if access is Access.NONE:
                return []
        return [parameter]

    def _ui_parameter(self, ctx: EvalContext, access: Access) -> UiParameter:
        local_ref_id = self._elem.ref_id
        static_label = self._param_ref.text or self._param.text or ""
        raw_suffix = self._param_ref.suffix_text or self._param.suffix_text
        suffix = raw_suffix
        # The arguments are looked up only for a text with a placeholder (few of them; every
        # evaluation of a large application places tens of thousands of parameters).
        if "{{" in static_label or (raw_suffix and "{{" in raw_suffix):
            arg_defaults = ctx.text_args()
            static_label = apply_text_args(static_label, arg_defaults)
            if raw_suffix:
                suffix = apply_text_args(raw_suffix, arg_defaults)
        text_ref = self._param_ref.text_parameter_ref_id
        label = (ctx.get(text_ref) if text_ref else None) or static_label
        default_value = self._param_ref.value or self._param.value or ""
        value = ctx.get(local_ref_id) or default_value
        ref_id = ctx.qualify(local_ref_id)
        handed = ctx.handed
        if handed is None:
            return self._make(ref_id, label, value, suffix, access, default_value)
        key = (ref_id, label, value, suffix)
        made = self._made.get(key)
        if made is None:
            made = self._made_before.get(key)
        if made is None or id(made) in handed:
            made = self._make(ref_id, label, value, suffix, access, default_value)
        if key not in self._made:
            if len(self._made) >= _MADE:
                self._made_before = self._made
                self._made = {}
            self._made[key] = made
        elif self._made[key] is not made:
            self._made[key] = made
        handed.add(id(made))
        return made

    def _make(
        self,
        ref_id: str,
        label: str,
        value: str,
        suffix: str | None,
        access: Access,
        default_value: str,
    ) -> UiParameter:
        return UiParameter(
            ref_id=ref_id,
            label=label,
            value=value,
            widget=self._get_widget(),
            default_value=default_value,
            indent_level=self._elem.indent_level,
            suffix=suffix,
            access=access,
            icon=self._elem.icon,
            cell=self._elem.cell,
            text_alignment=self._param_type.text_alignment,
            help_context=self._elem.help_context,
        )

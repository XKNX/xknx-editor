from __future__ import annotations

from .._name import apply_text_args, fill_name
from ..context import EvalContext
from ..ui import UiNode
from ..ui.tab import UiTab
from .base import DynamicNode


class ChannelNode(DynamicNode):
    """Renders a UI tab (a Channel or a ChannelIndependentBlock)."""

    __slots__ = (
        "_children",
        "_icon",
        "_id",
        "_independent",
        "_name",
        "_number",
        "_text",
        "_text_param_ref_id",
    )

    def __init__(
        self,
        children: list[DynamicNode | None],
        id: str | None = None,
        name: str | None = None,
        text: str | None = None,
        number: str | None = None,
        icon: str | None = None,
        text_parameter_ref_id: str | None = None,
        independent: bool = False,
    ) -> None:
        self._id = id
        self._name = name
        self._text = text
        self._number = number
        self._icon = icon
        self._text_param_ref_id = text_parameter_ref_id
        self._children = children
        self._independent = independent

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        items = [u for c in self._children if c for u in c.eval(ctx)]
        template = apply_text_args(
            self._text or self._name or "", ctx.get_arg_defaults()
        )
        name_value = (
            ctx.get(self._text_param_ref_id) if self._text_param_ref_id else None
        )
        text = fill_name(template, name_value or "") or None
        return [
            UiTab(
                children=tuple(items),
                id=ctx.qualify(self._id or ""),
                name=self._name,
                text=text,
                number=self._number,
                icon=self._icon,
                independent=self._independent,
            )
        ]

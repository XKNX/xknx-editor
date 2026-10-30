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

    def _heading(self, ctx: EvalContext) -> str | None:
        template = apply_text_args(self._text or self._name or "", ctx.text_args())
        name_value = (
            ctx.get(self._text_param_ref_id) if self._text_param_ref_id else None
        )
        return fill_name(template, name_value or "") or None

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        explorer = ctx.explore
        if explorer is not None:
            explorer.enter_section(self._heading(ctx) or "")
        tree = ctx.object_tree
        # The channel-independent block and a converted application's dummy channel are no
        # node of the group object tree: their objects belong to the level around them.
        if tree is not None and (
            self._independent
            or (
                tree.pre_ets4_style
                and self._name == "Generic"
                and not self._text
                and self._number == "0"
            )
        ):
            tree = None
        if tree is not None:
            tree.enter("Channel", ctx.qualify(self._id or ""))
        try:
            items = [u for c in self._children if c for u in c.eval(ctx)]
        finally:
            if explorer is not None:
                explorer.leave_section()
            if tree is not None:
                tree.leave()
        text = self._heading(ctx)
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

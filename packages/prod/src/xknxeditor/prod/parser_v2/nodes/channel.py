from __future__ import annotations

from typing import TYPE_CHECKING

from .._name import apply_text_args, fill_name
from ..context import EvalContext
from ..ui import UiNode
from ..ui.tab import UiTab
from .base import DynamicNode, eval_all

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate import ApplicationProgramChannel


class ChannelNode(DynamicNode):
    """Renders a UI tab (a Channel or a ChannelIndependentBlock)."""

    __slots__ = (
        "_children",
        "_elem",
        "_fixed_name",
        "_icon",
        "_id",
        "_independent",
        "_number",
        "_text_param_ref_id",
        "pure",
    )

    def __init__(
        self,
        children: list[DynamicNode | None],
        id: str | None = None,
        name: str | None = None,
        number: str | None = None,
        icon: str | None = None,
        text_parameter_ref_id: str | None = None,
        independent: bool = False,
        elem: ApplicationProgramChannel | None = None,
    ) -> None:
        self._id = id
        # A channel's name and text are read from it when it is shown: they are in the language
        # of the program then (see xknxeditor.prod.translate), and the tree is the same in every
        # language. ``name`` is the name of a block that is no channel of the program.
        self._elem = elem
        self._fixed_name = name
        self._number = number
        self._icon = icon
        self._text_param_ref_id = text_parameter_ref_id
        self._children = children
        self._independent = independent
        self.pure = all(child is None or child.pure for child in children)

    @property
    def _name(self) -> str | None:
        return self._elem.name if self._elem is not None else self._fixed_name

    @property
    def _text(self) -> str | None:
        return self._elem.text if self._elem is not None else None

    def _heading(self, ctx: EvalContext) -> str | None:
        template = apply_text_args(self._text or self._name or "", ctx.text_args())
        name_value = (
            ctx.get(self._text_param_ref_id) if self._text_param_ref_id else None
        )
        return fill_name(template, name_value or "") or None

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        if self.pure and ctx.explore is None:
            # A tab of blocks, parameters and Chooses alone gives the same again when what it
            # reads is the same (see EvalMemo).
            memo = ctx.memo
            if memo is not None:
                return memo.evaluate(self, ctx, self._eval)
        return self._eval(ctx)

    def _eval(self, ctx: EvalContext) -> list[UiNode]:
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
        tree_node = tree.enter("Channel", ctx.qualify(self._id or "")) if tree else None
        try:
            items = eval_all(self._children, ctx)
        finally:
            if explorer is not None:
                explorer.leave_section()
            if tree is not None:
                tree.leave()
        text = self._heading(ctx)
        if tree_node is not None:
            tree_node.text = text or self._name or tree_node.ref_id
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

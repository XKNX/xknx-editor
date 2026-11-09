from __future__ import annotations

from ..context import EvalContext
from ..ui import UiNode
from .base import DynamicNode, eval_all


class GenericCollectionNode(DynamicNode):
    """Pass-through container; concatenates child results without wrapping."""

    def __init__(self, children: list[DynamicNode | None]):
        self._children = children

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        return eval_all(self._children, ctx)

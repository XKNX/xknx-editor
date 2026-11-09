from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..context import EvalContext
from ..ui import UiNode


class DynamicNode(ABC):
    """Root type for the dynamic tree; one eval(ctx) per node.

    eval applies its side effects and yields the UiNodes from this node plus its
    active children. Containers concatenate child output; leaves give [] or one node.
    """

    @abstractmethod
    def eval(self, ctx: EvalContext) -> list[UiNode]: ...


def eval_all(nodes: Iterable[DynamicNode | None], ctx: EvalContext) -> list[UiNode]:
    """The UiNodes of ``nodes`` (the missing ones left out) in their order, each evaluated in
    ``ctx``: the loop of every container, run tens of thousands of times per evaluation."""
    result: list[UiNode] = []
    for node in nodes:
        if node:
            result.extend(node.eval(ctx))
    return result

from __future__ import annotations

from xknxeditor.namespaces.intermediate import Rename

from .._name import apply_text_args
from ..context import EvalContext
from ..ui import UiNode
from .base import DynamicNode


class RenameNode(DynamicNode):
    """Leaf: retitles a ComObjectParameterBlock inside a choose branch."""

    __slots__ = ("_elem",)

    # It sets a text the evaluation records (see EvalMemo); its text arguments are those of
    # the application where the memory is used.
    pure = True

    def __init__(self, elem: Rename):
        self._elem = elem

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        text = apply_text_args(self._elem.text, ctx.get_arg_defaults())
        ctx.set_text(self._elem.ref_id, text)
        return []

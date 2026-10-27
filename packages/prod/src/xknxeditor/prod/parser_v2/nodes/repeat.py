from __future__ import annotations

from xknxeditor.namespaces.intermediate import Repeat

from ..context import EvalContext, Explorer, Gate
from ..ui import UiNode
from .base import DynamicNode


class RepeatNode(DynamicNode):
    """Container: emits children N times, one repeat context per pass.

    N is count, or a parameter value when count=0 and parameter_ref_id is set.
    Index substitution for non-Module direct children is still TODO.
    """

    def __init__(
        self,
        elem: Repeat,
        children: list[DynamicNode | None],
        widget_param_refs: set[str] | None = None,
    ):
        self._elem = elem
        self._children = children
        # See ChooseWhenNode: only a count parameter that is also rendered as a widget can be "active",
        # so only such a gate is pushed onto the capture chain (chain-AND activeness).
        self._widget_param_refs = widget_param_refs

    def _count(self, ctx: EvalContext) -> int:
        if self._elem.count != 0:
            return self._elem.count
        if self._elem.parameter_ref_id:
            try:
                return int(ctx.get(self._elem.parameter_ref_id) or "0")
            except ValueError:
                return 0
        return 0

    def _active(self) -> list[DynamicNode]:
        return [c for c in self._children if c is not None]

    def _explore(self, ctx: EvalContext, explorer: Explorer) -> list[UiNode]:
        """At least one pass; a pass of a parameter-driven count needs the count to reach it."""
        count_param = self._elem.parameter_ref_id if self._elem.count == 0 else None
        result: list[UiNode] = []
        for i in range(1, max(self._count(ctx), 1) + 1):
            rctx = ctx.repeat_ctx(i)
            if count_param:
                explorer.push_gate(
                    Gate(ctx.qualify_local(count_param), f">={i}", needs_active=False)
                )
            try:
                for c in self._active():
                    result.extend(c.eval(rctx))
            finally:
                if count_param:
                    explorer.pop_gate()
        return result

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        explorer = ctx.explore
        if explorer is not None:
            return self._explore(ctx, explorer)
        result: list[UiNode] = []
        capture = ctx.capture
        # A parameter-driven instance count gates the objects this Repeat produces; put that parameter
        # on the capture's gate chain while evaluating the iterations (see EvalCapture / chain-AND).
        count_param = self._elem.parameter_ref_id if capture is not None else None
        gating = bool(count_param) and (
            self._widget_param_refs is None or count_param in self._widget_param_refs
        )
        if gating and count_param:
            # Qualify the count gate in its OWNING scope (see ChooseWhenNode): module-local ->
            # instance-qualified, ancestor/app param -> unqualified. Identity at global scope.
            capture.push(ctx.qualify_local(count_param))  # type: ignore[union-attr]
        try:
            for i in range(1, self._count(ctx) + 1):
                rctx = ctx.repeat_ctx(i)
                for c in self._active():
                    result.extend(c.eval(rctx))
        finally:
            if gating and count_param:
                capture.pop()  # type: ignore[union-attr]
        return result

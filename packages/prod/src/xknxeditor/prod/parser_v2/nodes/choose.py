from __future__ import annotations

from ..context import EvalContext
from ..ui import UiNode
from .base import DynamicNode

_OPERATORS = ("!=", ">=", "<=", ">", "<", "=")


def _token_matches(value: str, token: str) -> bool:
    """Match one Test token: a comparison (``>0``, ``<=5``, ``!=2``, ``=3``) or a bare literal.

    Operator operands are compared as integers, so ``=`` / ``!=`` are numeric equality. A token with
    no operator prefix matches numerically when both are numbers (``01`` matches ``1``), else as a
    plain literal.
    """
    for op in _OPERATORS:
        if token.startswith(op):
            try:
                left, right = int(value), int(token[len(op) :])
            except ValueError:
                return False
            if op == ">=":
                return left >= right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            if op == "<":
                return left < right
            if op == "!=":
                return left != right
            return left == right
    try:
        return float(value) == float(token)
    except ValueError:
        return value == token


def _value_matches(value: str, test_values: list[str]) -> bool:
    return any(_token_matches(value, t) for t in test_values)


def satisfies(condition: str | None, value: str) -> bool:
    if condition is None:
        return False
    test_values = [v.strip() for v in str(condition).split() if v.strip()]
    return _value_matches(value, test_values)


class ChooseWhenNode(DynamicNode):
    def __init__(
        self,
        param_ref_id: str,
        condition_to_nodes: dict[str, list[DynamicNode | None]],
        default_nodes: list[DynamicNode | None] | None,
        widget_param_refs: set[str] | None = None,
        union_sibling_refs: set[str] | None = None,
    ):
        self._param_ref_id = param_ref_id
        self._condition_to_nodes = condition_to_nodes
        self._default_nodes = default_nodes
        # Params ever rendered as a widget somewhere in the app (shared, build-populated set). Only such
        # a gate can become "active"; a purely structural selector (dummy ParameterType, never a widget)
        # can never be active, so it must not be pushed onto the capture chain — otherwise it would
        # disqualify every object under it (chain-AND) even though the branch is genuinely selected.
        self._widget_param_refs = widget_param_refs
        # If this Choose's parameter is a Union member overlaying shared memory, the parameter-refs
        # that COMPETE with it (other members at the same offset — aliases of the same member are
        # excluded). Only the active overlay's Choose should render; a Choose on an inactive member
        # renders nothing (see eval), else overlapping members both render (duplicate content).
        self._union_sibling_refs = union_sibling_refs

    def _gates_capture(self) -> bool:
        return self._widget_param_refs is None or (
            self._param_ref_id in self._widget_param_refs
        )

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        # Union-member gate: this Choose's parameter shares memory with its siblings, so only the
        # ACTIVE overlay's content should render (else overlapping members duplicate content). The
        # active overlay is the member REACHED under the current values, determined order-independently
        # by a discovery pass (union suppression off) that runs before this render pass. Suppress this
        # member iff it was not reached but a sibling was. During discovery (union_suppress False) this
        # is skipped so both members render and the reached one is recorded.
        if (
            self._union_sibling_refs
            and ctx.union_suppress
            and not ctx.is_discovered_active(self._param_ref_id)
            and any(ctx.is_discovered_active(sib) for sib in self._union_sibling_refs)
        ):
            return []
        # A Choose on a parameter that is neither shown nor assigned selects no branch at all,
        # not even its default.
        if not ctx.is_gate_active(self._param_ref_id):
            return []
        value = ctx.get(self._param_ref_id) or ""
        result: list[UiNode] = []
        matched = False
        capture = ctx.capture
        # While evaluating the selected branch, this Choose's gating parameter is on the capture's gate
        # chain, so every com-object emitted below records it — the basis for the chain-AND activeness
        # test (an object is active only if every gate on its path is driven by an active parameter).
        gating = capture is not None and self._gates_capture()
        if gating:
            # Push the gate id qualified in the scope that OWNS it: a module-local gate becomes
            # instance-qualified (matching the qualified ids that com-object leaves record and that
            # ``active_param_refs()`` returns for module scopes), while a gate on an ancestor/app
            # parameter stays unqualified (it is marked active in the ancestor scope). ``qualify_local``
            # (not blind ``qualify``) so an inherited gate param is not mis-qualified — otherwise its
            # com-objects drop from the chain-AND active set. Identity at global scope.
            capture.push(ctx.qualify_local(self._param_ref_id))  # type: ignore[union-attr]
        try:
            for condition, nodes in self._condition_to_nodes.items():
                if satisfies(condition, value):
                    matched = True
                    result.extend(u for n in nodes if n for u in n.eval(ctx))
            if not matched and self._default_nodes is not None:
                result.extend(u for n in self._default_nodes if n for u in n.eval(ctx))
        finally:
            if gating:
                capture.pop()  # type: ignore[union-attr]
        return result

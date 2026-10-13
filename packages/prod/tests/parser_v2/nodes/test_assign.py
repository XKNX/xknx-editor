from xknxeditor.namespaces.intermediate import Assign
from xknxeditor.prod.parser_v2.nodes import (
    AssignNode,
    ChooseWhenNode,
    DynamicNode,
    EvalContext,
    GenericCollectionNode,
    GlobalState,
)
from xknxeditor.prod.parser_v2.ui import UiNode
from xknxeditor.prod.parser_v2.ui.separator import UiSeparator

_BASE = "M-0008_A-7072-21-5CC3-O000A"
_REF_MODE = f"{_BASE}_P-1_R-1"
_REF_TARGET = f"{_BASE}_P-2_R-2"
_REF_SOURCE = f"{_BASE}_P-3_R-3"
_REF_OTHER = f"{_BASE}_P-4_R-4"
_REF_MISSING = f"{_BASE}_P-5_R-5"


class UiLeaf(DynamicNode):
    """Stub leaf that returns a UiSeparator so we can verify it was included."""

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        return [UiSeparator(id="leaf", text=None)]


class TestAssignNode:
    def test_eval_returns_empty_list(self):
        node = AssignNode(Assign(target_param_ref_ref=_REF_TARGET, value="42"))
        assert node.eval(EvalContext(GlobalState())) == []

    def test_assign_literal_value_mutates_state(self):
        node = AssignNode(Assign(target_param_ref_ref=_REF_TARGET, value="42"))
        state = GlobalState()
        node.eval(EvalContext(state))
        assert state.parameter_instance_refs()[_REF_TARGET] == "42"

    def test_assign_copies_source_param(self):
        node = AssignNode(
            Assign(target_param_ref_ref=_REF_TARGET, source_param_ref_ref=_REF_SOURCE)
        )
        state = GlobalState({_REF_SOURCE: "7"})
        node.eval(EvalContext(state))
        assert state.parameter_instance_refs()[_REF_TARGET] == "7"

    def test_assign_source_missing_skips_write(self):
        node = AssignNode(
            Assign(target_param_ref_ref=_REF_TARGET, source_param_ref_ref=_REF_MISSING)
        )
        state = GlobalState()
        node.eval(EvalContext(state))
        assert _REF_TARGET not in state.parameter_instance_refs()

    def test_assign_value_takes_precedence_over_source(self):
        node = AssignNode(
            Assign(
                target_param_ref_ref=_REF_TARGET,
                value="literal",
                source_param_ref_ref=_REF_OTHER,
            )
        )
        state = GlobalState({_REF_OTHER: "should-be-ignored"})
        node.eval(EvalContext(state))
        assert state.parameter_instance_refs()[_REF_TARGET] == "literal"

    def test_assign_mutation_visible_to_subsequent_sibling_in_collection(self):
        # Assign fires before choose in eval order, so the leaf is reachable.
        assign = AssignNode(Assign(target_param_ref_ref=_REF_MODE, value="1"))
        leaf = UiLeaf()
        choose = ChooseWhenNode(_REF_MODE, {"1": [leaf]}, None)
        collection = GenericCollectionNode([assign, choose])
        result = collection.eval(EvalContext(GlobalState()))
        assert result == [UiSeparator(id="leaf", text=None)]

    def test_assign_no_op_when_both_value_and_source_are_none(self):
        node = AssignNode(Assign(target_param_ref_ref=_REF_TARGET))
        state = GlobalState()
        node.eval(EvalContext(state))
        assert state.parameter_instance_refs() == {}

    def test_assign_activates_target_and_source(self):
        node = AssignNode(
            Assign(target_param_ref_ref=_REF_TARGET, source_param_ref_ref=_REF_SOURCE)
        )
        state = GlobalState({_REF_SOURCE: "7"})
        node.eval(EvalContext(state))
        assert state.active_param_refs() == {_REF_TARGET, _REF_SOURCE}

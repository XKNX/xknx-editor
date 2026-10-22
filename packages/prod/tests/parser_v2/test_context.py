from xknxeditor.namespaces.intermediate import (
    ModuleArg,
    ModuleInstance,
    ModuleNumericArg,
    ParameterInstanceRef,
)
from xknxeditor.prod.parser_v2.allocator import Allocator
from xknxeditor.prod.parser_v2.nodes import (
    ChooseWhenNode,
    DynamicNode,
    EvalContext,
    GlobalState,
    ModuleState,
)
from xknxeditor.prod.parser_v2.ui import UiNode


class _ParamLeaf(DynamicNode):
    """Stub leaf: marks a param ref active as a side effect."""

    def __init__(self, ref_id: str) -> None:
        self._ref_id = ref_id

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        ctx.mark_active_param(self._ref_id)
        return []


_BASE = "M-0008_A-7072-21-5CC3-O000A"

_REF_MODE = f"{_BASE}_P-1_R-1"
_REF_TARGET = f"{_BASE}_P-2_R-2"

_DEF_PREFIX = f"{_BASE}_MD-1"
_MODULE_ID = f"{_BASE}_MD-1_M-C8"  # Module(0xC8 = 200)
_MODULE_INSTANCE_ID = f"{_BASE}_MD-1_M-C8_MI-1"
_LOCAL_REF = f"{_BASE}_MD-1_P-96_R-F3"  # ParamRef(0x96=150, 0xF3=243)
_QUALIFIED_REF = f"{_BASE}_MD-1_M-C8_MI-1_P-96_R-F3"
_ARG_REF = f"{_BASE}_MD-1_A-1"

_SM_DEF_PREFIX = f"{_BASE}_MD-1_SM-1"
_SM_MODULE_INSTANCE_ID = (
    f"{_BASE}_MD-1_M-64_MI-1_SM-1_M-C8_MI-1"  # Module(0x64=100), SubModule(0xC8=200)
)
_SM_ARG_REF = f"{_BASE}_MD-1_SM-1_A-1"
_REL_SM_ARG_REF = "MD-1_SM-1_A-1"  # relative form; no manufacturer prefix


def _num_arg(ref_id: str, value: int) -> ModuleNumericArg:
    return ModuleNumericArg(ref_id=ref_id, value=value)


def _alloc_arg(ref_id: str, allocator_ref_id: str) -> ModuleNumericArg:
    return ModuleNumericArg(ref_id=ref_id, allocator_ref_id=allocator_ref_id)


class TestModuleStateArguments:
    def test_get_arg_returns_value(self):
        arg = _num_arg(_ARG_REF, 5)
        ms = ModuleState(_MODULE_INSTANCE_ID, {_ARG_REF: arg})
        assert ms.get_arg(_ARG_REF) is arg

    def test_get_arg_returns_none_for_missing(self):
        ms = ModuleState(_MODULE_INSTANCE_ID)
        assert ms.get_arg(_ARG_REF) is None

    def test_args_not_in_parameter_instance_refs(self):
        ms = ModuleState(_MODULE_INSTANCE_ID, {_ARG_REF: _num_arg(_ARG_REF, 5)})
        assert ms.parameter_instance_refs() == {}

    def test_args_not_visible_via_ctx_get(self):
        ms = ModuleState(_MODULE_INSTANCE_ID, {_ARG_REF: _num_arg(_ARG_REF, 5)})
        assert EvalContext(ms).get(_ARG_REF) is None

    def test_as_module_instance_for_submodule_roundtrips_args(self):
        arg = _num_arg(_SM_ARG_REF, 9)
        ms = ModuleState(_SM_MODULE_INSTANCE_ID, {_SM_ARG_REF: arg})
        instance_id, ref_id, args = ms.as_module_instance()
        assert instance_id == _SM_MODULE_INSTANCE_ID
        assert ref_id == f"{_BASE}_MD-1_M-64_MI-1_SM-1_M-C8"
        assert args == {_REL_SM_ARG_REF: arg}

    def test_allocator_arg_stored_without_value(self):
        allocator_ref = f"{_BASE}_MD-1_L-2"
        arg = _alloc_arg(_ARG_REF, allocator_ref)
        ms = ModuleState(_MODULE_INSTANCE_ID, {_ARG_REF: arg})
        result = ms.get_arg(_ARG_REF)
        assert isinstance(result, ModuleNumericArg)
        assert result.value is None
        assert result.allocator_ref_id == allocator_ref


class TestEvalContext:
    def test_get_returns_global_value(self):
        ctx = EvalContext(GlobalState({_REF_MODE: "42"}))
        assert ctx.get(_REF_MODE) == "42"

    def test_get_returns_none_for_missing_key(self):
        ctx = EvalContext(GlobalState())
        assert ctx.get(_REF_MODE) is None

    def test_set_writes_to_global(self):
        state = GlobalState()
        ctx = EvalContext(state)
        ctx.set(_REF_TARGET, "99")
        assert state.parameter_instance_refs() == {_REF_TARGET: "99"}

    def test_module_ctx_set_appears_in_parameter_instance_refs(self):
        state = GlobalState()
        mctx = EvalContext(state).module_ctx(_MODULE_ID)
        mctx.set(_LOCAL_REF, "5")
        assert state.parameter_instance_refs() == {_QUALIFIED_REF: "5"}

    def test_repeat_ctx_sets_instance_idx_for_module(self):
        state = GlobalState()
        mctx = EvalContext(state).repeat_ctx(3).module_ctx(_MODULE_ID)
        mctx.set(_LOCAL_REF, "5")
        expected_ref = f"{_BASE}_MD-1_M-C8_MI-3_P-96_R-F3"
        assert state.parameter_instance_refs() == {expected_ref: "5"}

    def test_module_ctx_reads_initial_value(self):
        state = GlobalState.from_project(
            [ParameterInstanceRef(ref_id=_QUALIFIED_REF, value="7")],
            [ModuleInstance(id=_MODULE_INSTANCE_ID, ref_id=_DEF_PREFIX)],
        )
        mctx = EvalContext(state).module_ctx(_MODULE_ID)
        assert mctx.get(_LOCAL_REF) == "7"

    def test_module_ctx_write_overwrites_initial_value(self):
        state = GlobalState.from_project(
            [ParameterInstanceRef(ref_id=_QUALIFIED_REF, value="old")],
            [ModuleInstance(id=_MODULE_INSTANCE_ID, ref_id=_DEF_PREFIX)],
        )
        mctx = EvalContext(state).module_ctx(_MODULE_ID)
        mctx.set(_LOCAL_REF, "new")
        assert mctx.get(_LOCAL_REF) == "new"

    def test_module_ctx_reads_value_of_relative_instance_id(self):
        # A .knxproj stores module instances relative to the application: MD-1_M-C8_MI-1.
        state = GlobalState.from_project(
            [ParameterInstanceRef(ref_id=_QUALIFIED_REF, value="7")],
            [ModuleInstance(id="MD-1_M-C8_MI-1", ref_id="MD-1")],
            app_id=_BASE,
        )
        state.reset_active()
        assert EvalContext(state).module_ctx(_MODULE_ID).get(_LOCAL_REF) == "7"
        state.trim_to_active()
        assert [m.module_instance_id for m in state.module_children()] == [
            _MODULE_INSTANCE_ID
        ]
        assert EvalContext(state).module_ctx(_MODULE_ID).get(_LOCAL_REF) == "7"

    def test_module_value_without_a_saved_instance_belongs_to_the_instance(self):
        # An instance created by a later edit can be missing from the saved list: its values still
        # reach it, not the application.
        state = GlobalState.from_project(
            [ParameterInstanceRef(ref_id=_QUALIFIED_REF, value="7")], [], app_id=_BASE
        )
        state.reset_active()
        assert EvalContext(state).module_ctx(_MODULE_ID).get(_LOCAL_REF) == "7"
        assert _QUALIFIED_REF not in state.param_ref_id_to_value
        state.trim_to_active()
        assert [m.module_instance_id for m in state.module_children()] == [
            _MODULE_INSTANCE_ID
        ]


class TestTrimToActive:
    def test_hidden_param_is_evicted_after_trim(self):
        state = GlobalState({_REF_MODE: "2", _REF_TARGET: "5"})
        y_leaf = _ParamLeaf(_REF_MODE)
        x_leaf = _ParamLeaf(_REF_TARGET)
        choose = ChooseWhenNode(_REF_MODE, {"1": [x_leaf]}, None)

        ctx = EvalContext(state)
        y_leaf.eval(ctx)
        choose.eval(ctx)

        state.trim_to_active()

        assert _REF_MODE in state.param_ref_id_to_value
        assert _REF_TARGET not in state.param_ref_id_to_value

    def test_active_param_is_kept_after_trim(self):
        state = GlobalState({_REF_MODE: "1", _REF_TARGET: "5"})
        y_leaf = _ParamLeaf(_REF_MODE)
        x_leaf = _ParamLeaf(_REF_TARGET)
        choose = ChooseWhenNode(_REF_MODE, {"1": [x_leaf]}, None)

        ctx = EvalContext(state)
        y_leaf.eval(ctx)
        choose.eval(ctx)

        state.trim_to_active()

        assert _REF_MODE in state.param_ref_id_to_value
        assert _REF_TARGET in state.param_ref_id_to_value

    def test_stale_rename_text_is_cleared_after_trim(self):
        state = GlobalState()
        state.set_text("some-block-id", "Renamed")
        state.trim_to_active()
        assert state.get_text("some-block-id") is None

    def test_trim_is_clean_for_next_cycle(self):
        state = GlobalState({_REF_MODE: "1", _REF_TARGET: "5"})
        y_leaf = _ParamLeaf(_REF_MODE)
        x_leaf = _ParamLeaf(_REF_TARGET)
        choose = ChooseWhenNode(_REF_MODE, {"1": [x_leaf]}, None)

        state.reset_active()
        ctx = EvalContext(state)
        y_leaf.eval(ctx)
        choose.eval(ctx)
        state.trim_to_active()
        assert _REF_TARGET in state.param_ref_id_to_value

        state.set(_REF_MODE, "2")
        state.reset_active()
        ctx = EvalContext(state)
        y_leaf.eval(ctx)
        choose.eval(ctx)
        state.trim_to_active()
        assert _REF_TARGET not in state.param_ref_id_to_value

    def test_active_param_refs_returns_marked_refs(self):
        state = GlobalState({_REF_MODE: "1", _REF_TARGET: "5"})
        y_leaf = _ParamLeaf(_REF_MODE)
        x_leaf = _ParamLeaf(_REF_TARGET)
        choose = ChooseWhenNode(_REF_MODE, {"1": [x_leaf]}, None)

        state.reset_active()
        ctx = EvalContext(state)
        y_leaf.eval(ctx)
        choose.eval(ctx)
        state.trim_to_active()

        assert state.active_param_refs() == {_REF_MODE, _REF_TARGET}

    def test_active_param_refs_excludes_hidden(self):
        state = GlobalState({_REF_MODE: "2", _REF_TARGET: "5"})
        y_leaf = _ParamLeaf(_REF_MODE)
        x_leaf = _ParamLeaf(_REF_TARGET)
        choose = ChooseWhenNode(_REF_MODE, {"1": [x_leaf]}, None)

        state.reset_active()
        ctx = EvalContext(state)
        y_leaf.eval(ctx)
        choose.eval(ctx)
        state.trim_to_active()

        assert state.active_param_refs() == {_REF_MODE}

    def test_active_param_refs_qualified_in_module_scope(self):
        state = GlobalState()
        ms = state.module_child(_MODULE_ID)
        ms.mark_active_param(_LOCAL_REF)

        assert state.active_param_refs() == {_QUALIFIED_REF}


_ALLOC_ID = f"{_BASE}_MD-1_L-2"


class TestModuleChildMerge:
    def test_arg_overwritten_on_second_visit(self):
        first = ModuleNumericArg(ref_id=_ARG_REF, value=42)
        state = GlobalState()
        child = state.module_child(_MODULE_ID, arguments={_ARG_REF: first})

        second = ModuleNumericArg(ref_id=_ARG_REF, value=99)
        state.module_child(_MODULE_ID, arguments={_ARG_REF: second})

        assert child.get_arg(_ARG_REF) is second


_ARGS: dict[str, ModuleArg] = {
    _ARG_REF: ModuleNumericArg(ref_id=_ARG_REF, allocator_ref_id=_ALLOC_ID)
}


class TestAllocator:
    def test_resolves_at_given_position(self):
        address, next_pos = Allocator(
            id=_ALLOC_ID, start=100, max_inclusive=199
        ).resolve(100, 10, 1, 0)
        assert address == 100
        assert next_pos == 110

    def test_resolves_at_advanced_position(self):
        address, next_pos = Allocator(
            id=_ALLOC_ID, start=100, max_inclusive=199
        ).resolve(110, 10, 1, 0)
        assert address == 110
        assert next_pos == 120

    def test_alignment_rounds_up_position(self):
        # position=103, allocates=3, alignment=4 → aligned to 104, next=107
        address, next_pos = Allocator(
            id=_ALLOC_ID, start=100, max_inclusive=199
        ).resolve(103, 3, 4, 0)
        assert address == 104
        assert next_pos == 107

    def test_overflow_raises(self):
        import pytest

        with pytest.raises(OverflowError):
            Allocator(id=_ALLOC_ID, start=100, max_inclusive=109).resolve(100, 11, 1, 0)


class TestSetInstanceRef:
    def test_global_ref_written_to_root(self):
        state = GlobalState()
        state.set_instance_ref(_REF_TARGET, "42")
        assert state.param_ref_id_to_value[_REF_TARGET] == "42"

    def test_qualified_module_ref_written_to_module_scope(self):
        state = GlobalState()
        _ = state.module_child(_MODULE_ID, ref_id=_DEF_PREFIX)
        state.set_instance_ref(_QUALIFIED_REF, "7")
        ms = next(
            c
            for c in state.module_children()
            if c.module_instance_id == _MODULE_INSTANCE_ID
        )
        assert ms.param_ref_id_to_value[_LOCAL_REF] == "7"

    def test_qualified_ref_visible_via_parameter_instance_refs(self):
        state = GlobalState()
        _ = state.module_child(_MODULE_ID, ref_id=_DEF_PREFIX)
        state.set_instance_ref(_QUALIFIED_REF, "9")
        assert state.parameter_instance_refs()[_QUALIFIED_REF] == "9"

    def test_unknown_ref_written_to_root(self):
        state = GlobalState()
        unknown = f"{_BASE}_P-99_R-99"
        state.set_instance_ref(unknown, "1")
        assert state.param_ref_id_to_value[unknown] == "1"

    def test_no_module_scope_writes_to_root(self):
        # No module_child() called; qualified ref has nowhere to route, lands at root.
        state = GlobalState()
        state.set_instance_ref(_QUALIFIED_REF, "5")
        assert state.param_ref_id_to_value[_QUALIFIED_REF] == "5"


_REF_ACT = f"{_BASE}_P-10_R-10"  # activation param (e.g. "Tastenpaar = aktiv")
_REF_FUNC = f"{_BASE}_P-11_R-11"  # function-type param inside the gated block
_OBJ_IMPORTED = f"{_BASE}_P-12_R-12"  # object of the imported branch (e.g. R-1980)
_OBJ_DEFAULT = f"{_BASE}_P-13_R-13"  # object of the static-default branch (e.g. R-1266)


class TestReactivateRetainsImportedValue:
    """Regression: reactivating a deactivated, parameter-gated block must reselect the SAME branch
    it was imported with, not the static default.

    At the state/eval level: an activation param gates a block; inside it a Choose on a
    function-type param selects the object.
    Deactivating trims the function value out of the active map; without the durable known-value
    store, reactivation resolves the function param to its default and picks a DIFFERENT branch
    (a different ComObjectRef, e.g. R-1266 instead of R-1980)."""

    def _tree(self) -> tuple[DynamicNode, DynamicNode]:
        # func_choose emits the imported-branch object for value "temp", else the default-branch one.
        func_choose = ChooseWhenNode(
            _REF_FUNC,
            {"temp": [_ParamLeaf(_REF_FUNC), _ParamLeaf(_OBJ_IMPORTED)]},
            default_nodes=[_ParamLeaf(_REF_FUNC), _ParamLeaf(_OBJ_DEFAULT)],
        )
        # The activation widget is always rendered; the block only when it is "1".
        act_leaf = _ParamLeaf(_REF_ACT)
        block = ChooseWhenNode(_REF_ACT, {"1": [func_choose]}, default_nodes=None)
        return act_leaf, block

    def _cycle(
        self, state: GlobalState, nodes: tuple[DynamicNode, DynamicNode]
    ) -> None:
        state.reset_active()
        ctx = EvalContext(state)
        for node in nodes:
            node.eval(ctx)
        state.trim_to_active()

    def test_reactivation_reselects_imported_branch(self) -> None:
        # Imported active: activation "1", function "temp".
        state = GlobalState.from_project(
            [
                ParameterInstanceRef(ref_id=_REF_ACT, value="1"),
                ParameterInstanceRef(ref_id=_REF_FUNC, value="temp"),
            ]
        )
        nodes = self._tree()

        self._cycle(state, nodes)  # import: imported branch active
        assert _OBJ_IMPORTED in state.active_param_refs()

        state.set(
            _REF_ACT, "0"
        )  # deactivate -> block gone, func value trimmed from active map
        self._cycle(state, nodes)
        assert _REF_FUNC not in state.param_ref_id_to_value  # trimmed while inactive

        state.set(_REF_ACT, "1")  # reactivate
        self._cycle(state, nodes)
        # The imported function value must survive and reselect the imported branch, NOT the default.
        assert _OBJ_IMPORTED in state.active_param_refs()
        assert _OBJ_DEFAULT not in state.active_param_refs()


class TestKnownValueStore:
    def test_get_survives_trim_for_inactive_param(self) -> None:
        state = GlobalState.from_project(
            [ParameterInstanceRef(ref_id=_REF_TARGET, value="imported")]
        )
        state.reset_active()
        state.trim_to_active()  # _REF_TARGET never marked active -> dropped from active map
        assert _REF_TARGET not in state.param_ref_id_to_value
        assert state.get(_REF_TARGET) == "imported"  # ...but still resolvable
        assert state.has_explicit_param(_REF_TARGET) is True

    def test_default_only_param_is_not_explicit(self) -> None:
        state = GlobalState(param_ref_defaults={_REF_TARGET: "5"})
        assert state.get(_REF_TARGET) == "5"
        assert state.has_explicit_param(_REF_TARGET) is False

    def test_clear_instance_ref_drops_known_value(self) -> None:
        state = GlobalState.from_project(
            [ParameterInstanceRef(ref_id=_REF_TARGET, value="imported")]
        )
        state.clear_instance_ref(_REF_TARGET)
        assert state.has_explicit_param(_REF_TARGET) is False
        assert state.get(_REF_TARGET) is None


_REF_UA = f"{_BASE}_P-20_R-20"  # union member A
_REF_UB = f"{_BASE}_P-21_R-21"  # union member B (shares memory with A)


class TestUnionSuppressionIsOrderIndependent:
    """The inactive overlay of a Union must render nothing regardless of node evaluation order.

    The active overlay is the member REACHED in the discovery pass (frozen via
    snapshot_discovered_active); the render pass suppresses the non-reached member no matter which
    member's Choose runs first, so a freshly-activated member is not dropped because a sibling from
    an inactive branch still carries a stale explicit value."""

    def _members(self) -> tuple[ChooseWhenNode, ChooseWhenNode]:
        member_a = ChooseWhenNode(
            _REF_UA, {"1": [_ParamLeaf(_REF_UA)]}, None, union_sibling_refs={_REF_UB}
        )
        member_b = ChooseWhenNode(
            _REF_UB, {"1": [_ParamLeaf(_REF_UB)]}, None, union_sibling_refs={_REF_UA}
        )
        return member_a, member_b

    def test_inactive_member_suppressed_even_when_evaluated_first(self) -> None:
        # A is the reached overlay; B is not. Evaluate B FIRST — still suppressed, because discovery
        # already recorded A as reached.
        state = GlobalState({_REF_UA: "1", _REF_UB: "1"})
        state.mark_active_param(_REF_UA)  # only A reached in discovery
        state.snapshot_discovered_active()
        member_a, member_b = self._members()
        ctx = EvalContext(state)
        assert (
            member_b.eval(ctx) == []
        )  # suppressed by the discovery result, order-independent
        assert _REF_UB not in state.active_param_refs()
        member_a.eval(ctx)
        assert _REF_UA in state.active_param_refs()


class TestComObjInstanceRefIds:
    """`com_obj_instance_ref_ids()` exposes the device's instantiated com objects.

    A download uses this as the authoritative active set (the GroupObjectTree),
    instead of the parameter-driven visible set which can over-activate objects of
    channel modes the device is not in (e.g. individual per-channel objects on a
    device configured as "2x Tunable White")."""

    def test_empty_without_saved_instances(self):
        # A device configured from scratch has no saved instances.
        assert GlobalState().com_obj_instance_ref_ids() == set()

    def test_exposes_saved_instance_ref_ids(self):
        from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
            ComObjectInstanceRef,
        )

        refs = [
            ComObjectInstanceRef(ref_id=f"{_BASE}_O-64_R-1"),
            ComObjectInstanceRef(ref_id=f"{_BASE}_O-74_R-2"),
        ]
        state = GlobalState.from_project(com_object_instance_refs=refs)
        assert state.com_obj_instance_ref_ids() == {
            f"{_BASE}_O-64_R-1",
            f"{_BASE}_O-74_R-2",
        }

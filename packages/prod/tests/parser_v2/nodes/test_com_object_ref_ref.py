import pytest

from xknxeditor.namespaces.intermediate import ComObjectRefRef
from xknxeditor.namespaces.intermediate.com_object_ref_t import (
    ComObjectRef as IrComObjectRef,
)
from xknxeditor.namespaces.intermediate.com_object_size_t import ComObjectSize
from xknxeditor.namespaces.intermediate.com_object_t import ComObject as IrComObject
from xknxeditor.namespaces.intermediate.enable_t import Enable
from xknxeditor.prod.errors import ParseError
from xknxeditor.prod.parser_v2.nodes import EvalContext, GlobalState
from xknxeditor.prod.parser_v2.nodes.com_object_ref_ref import ComObjectRefRefNode
from xknxeditor.prod.parser_v2.ui.com_object import UiComObject

_BASE = "M-0008_A-7072-21-5CC3-O000A"
_REF_ID = f"{_BASE}_O-1_R-1"
_CO_ID = f"{_BASE}_O-1"
_TEXT_PARAM_REF = f"{_BASE}_P-1_R-1"

_ELEM = ComObjectRefRef(ref_id=_REF_ID)


def _cor(**kwargs: object) -> IrComObjectRef:
    defaults = dict(
        id=_REF_ID,
        ref_id=_CO_ID,
        name=None,
        text=None,
        communication_flag=None,
        read_flag=None,
        write_flag=None,
        transmit_flag=None,
        update_flag=None,
        read_on_init_flag=None,
        datapoint_type=[],
        text_parameter_ref_id=None,
    )
    defaults.update(kwargs)
    return IrComObjectRef(**defaults)  # type: ignore[arg-type]


def _co(**kwargs: object) -> IrComObject:
    defaults = dict(
        id=_CO_ID,
        name="Switch",
        text="",
        number=5,
        function_text="",
        object_size=None,
        communication_flag=Enable.ENABLED,
        read_flag=Enable.DISABLED,
        write_flag=Enable.ENABLED,
        transmit_flag=Enable.DISABLED,
        update_flag=Enable.DISABLED,
        read_on_init_flag=Enable.DISABLED,
        datapoint_type=["DPT-1"],
        read_flag_locked=False,
        write_flag_locked=False,
        transmit_flag_locked=False,
        update_flag_locked=False,
        read_on_init_flag_locked=False,
    )
    defaults.update(kwargs)
    return IrComObject(**defaults)  # type: ignore[arg-type]


class TestComObjectRefRefNodeBasic:
    def test_eval_returns_ui_com_object(self):
        node = ComObjectRefRefNode(_ELEM, _cor(), _co())
        result = node.eval(EvalContext(GlobalState()))
        assert len(result) == 1
        assert isinstance(result[0], UiComObject)

    def test_ref_id_is_passed_through(self):
        node = ComObjectRefRefNode(_ELEM, _cor(), _co())
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.ref_id == _REF_ID

    def test_number_from_base(self):
        node = ComObjectRefRefNode(_ELEM, _cor(), _co(number=42))
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.number == 42

    def test_dpt_codes_parsed_from_base(self):
        node = ComObjectRefRefNode(
            _ELEM, _cor(), _co(datapoint_type=["DPT-1", "DPST-1-1"])
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        # Main-only "DPT-1" stays a bare "1"; a sub-type keeps its dotted form.
        assert co.dpt_codes == ("1", "1.1")

    def test_dpt_codes_keep_zero_subtype_distinct(self):
        node = ComObjectRefRefNode(
            _ELEM, _cor(), _co(datapoint_type=["DPT-16", "DPST-16-0"])
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        # "DPST-16-0" (sub-type zero) must not collapse onto main-only "DPT-16".
        assert co.dpt_codes == ("16", "16.0")

    def test_dpt_codes_ref_overrides_base(self):
        node = ComObjectRefRefNode(
            _ELEM, _cor(datapoint_type=["DPT-5"]), _co(datapoint_type=["DPT-1"])
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.dpt_codes == ("5",)


class TestFlags:
    def test_flags_from_base(self):
        node = ComObjectRefRefNode(
            _ELEM,
            _cor(),
            _co(
                communication_flag=Enable.ENABLED,
                read_flag=Enable.DISABLED,
                write_flag=Enable.ENABLED,
                transmit_flag=Enable.DISABLED,
                update_flag=Enable.DISABLED,
                read_on_init_flag=Enable.DISABLED,
            ),
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.communication is True
        assert co.read is False
        assert co.write is True
        assert co.transmit is False

    def test_ref_flag_overrides_base(self):
        node = ComObjectRefRefNode(
            _ELEM,
            _cor(read_flag=Enable.ENABLED),
            _co(read_flag=Enable.DISABLED),
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.read is True

    def test_locked_flags_from_base_only(self):
        node = ComObjectRefRefNode(
            _ELEM,
            _cor(),
            _co(
                read_flag_locked=True,
                write_flag_locked=False,
                transmit_flag_locked=True,
            ),
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.read_locked is True
        assert co.write_locked is False
        assert co.transmit_locked is True

    def test_none_base_falls_back_to_defaults(self):
        node = ComObjectRefRefNode(_ELEM, None, None)
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.communication is False
        assert co.number == 0
        assert co.dpt_codes == ()


class TestName:
    def test_name_from_base_text(self):
        node = ComObjectRefRefNode(
            _ELEM, _cor(text=None), _co(text="Switch Output", name="Switch")
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.name == "Switch Output"

    def test_ref_text_overrides_base_text(self):
        node = ComObjectRefRefNode(_ELEM, _cor(text="Override"), _co(text="Base"))
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.name == "Override"

    def test_name_with_placeholder_filled_from_param(self):
        node = ComObjectRefRefNode(
            _ELEM,
            _cor(text="{{0}} Switch", text_parameter_ref_id=_TEXT_PARAM_REF),
            _co(),
        )
        state = GlobalState({_TEXT_PARAM_REF: "Channel A"})
        co = node.eval(EvalContext(state))[0]
        assert isinstance(co, UiComObject)
        assert co.name == "Channel A Switch"

    def test_name_placeholder_empty_when_param_missing(self):
        node = ComObjectRefRefNode(
            _ELEM,
            _cor(text="{{0}} Switch", text_parameter_ref_id=_TEXT_PARAM_REF),
            _co(),
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.name == "Switch"


class TestActiveTracking:
    def test_marks_local_ref_active(self):
        node = ComObjectRefRefNode(_ELEM, _cor(), _co())
        state = GlobalState()
        node.eval(EvalContext(state))
        assert _REF_ID in state.active_com_object_refs()


class TestValuesOutsideTheSchema:
    def test_a_known_size_and_priority_are_their_text(self):
        node = ComObjectRefRefNode(
            _ELEM, _cor(), _co(object_size=ComObjectSize.VALUE_1_BYTE)
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.object_size == "1 Byte"

    def test_a_size_the_schema_does_not_have_names_itself(self):
        # Product data with an object size no KNX schema has cannot be used: the group object
        # says what is wrong instead of failing later.
        with pytest.raises(ParseError, match=r"object size 'Huge' of .*_O-1_R-1"):
            ComObjectRefRefNode(_ELEM, _cor(), _co(object_size="Huge"))

    def test_the_variable_length_size_is_its_text(self):
        node = ComObjectRefRefNode(
            _ELEM, _cor(), _co(object_size=ComObjectSize.LEGACY_VAR_DATA)
        )
        co = node.eval(EvalContext(GlobalState()))[0]
        assert isinstance(co, UiComObject)
        assert co.object_size == "LegacyVarData"

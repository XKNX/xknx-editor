"""What the nodes of a tree gave before is taken again where an evaluation reads the same
(see EvalMemo): a UI evaluated with what other UIs of the program left in it is the UI
evaluated anew, value for value, for every fixture application and every value of each of
its settings; a Choose whose reads give the same is not evaluated again, one whose reads
differ is; a pass that records more, and another thread, evaluate anew."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2 import context
from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try
from xknxeditor.prod.parser_v2.context import EvalContext, EvalMemo, Recording
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.nodes.choose import ChooseWhenNode
from xknxeditor.prod.parser_v2.state import GlobalState

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_APPLICATIONS = sorted(path.name for path in _FIXTURES.glob("*_application.xml"))


def _application(name: str) -> Application:
    data = (_FIXTURES / name).read_bytes()
    manufacturer = data.split(b'Manufacturer RefId="', 1)[1].split(b'"', 1)[0]
    return parse_application_xml(data, manufacturer.decode())[0]


def _ui(
    application: Application, values: dict[str, str], memo: EvalMemo | None
) -> DynamicUI:
    program, builder = application.resolved()
    ui = DynamicUI(program, tree_builder=builder, parameter_values=values)
    ui.eval_memo = memo
    return ui


def _observed(ui: DynamicUI) -> tuple[object, ...]:
    nodes = ui.ui()
    try:
        memory: object = ui.encode_to_memory()
    except Exception as exc:  # compared, not handled
        memory = type(exc)
    return (
        nodes,
        ui.active_parameter_ref_ids(),
        frozenset(ui._state.active_com_object_refs()),  # pyright: ignore[reportPrivateUsage]
        memory,
    )


def _trials(application: Application) -> Iterator[dict[str, str]]:
    """The default values, and each other value of each setting shown with them."""
    yield {}
    ui = _ui(application, {}, None)
    for ref_id, parameter in shown_parameters(ui.ui()).items():
        for value in values_to_try(parameter, ui.get_value(ref_id) or "")[:6]:
            yield {ref_id: value}


@pytest.mark.parametrize("name", _APPLICATIONS)
def test_a_ui_evaluated_with_the_memo_is_the_ui_evaluated_anew(name: str) -> None:
    application = _application(name)
    memo = EvalMemo()
    trials = list(_trials(application))
    # Each trial twice: the second takes what the first left where it reads the same.
    for values in trials + trials:
        assert _observed(_ui(application, values, memo)) == _observed(
            _ui(application, values, None)
        ), values


@pytest.fixture
def selections(monkeypatch: pytest.MonkeyPatch) -> list[ChooseWhenNode]:
    """The Chooses that evaluate their branches from now on."""
    selected: list[ChooseWhenNode] = []
    select = ChooseWhenNode._select  # pyright: ignore[reportPrivateUsage]

    def counted(self: ChooseWhenNode, ctx: object) -> object:
        selected.append(self)
        return select(self, ctx)  # type: ignore[arg-type]

    monkeypatch.setattr(ChooseWhenNode, "_select", counted)
    return selected


_INACTIVE = "inactive_choose_application.xml"


def _pure_chooses(application: Application) -> set[int]:
    _program, builder = application.resolved()
    found: set[int] = set()
    stack: list[object] = [builder.tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ChooseWhenNode) and node.pure:
            found.add(id(node))
        for name in ("_subtree", "_children", "_default_nodes"):
            value = getattr(node, name, None)
            if isinstance(value, list):
                stack.extend(child for child in value if child is not None)
            elif value is not None and name == "_subtree":
                stack.append(value)
        branches = getattr(node, "_condition_to_nodes", None)
        if isinstance(branches, dict):
            for nodes in branches.values():
                stack.extend(child for child in nodes if child is not None)
    return found


def test_a_choose_that_reads_the_same_is_not_evaluated_again(
    selections: list[ChooseWhenNode],
) -> None:
    application = _application(_INACTIVE)
    pure = _pure_chooses(application)
    assert pure
    memo = EvalMemo()
    _ui(application, {}, memo).ui()
    assert {id(node) for node in selections} & pure
    selections.clear()
    again = _ui(application, {}, memo)
    nodes = again.ui()
    # Each Choose that changes nothing but the marks reads the same: none selects again.
    assert not {id(node) for node in selections} & pure
    assert nodes == _ui(application, {}, None).ui()


def test_a_choose_whose_value_changed_is_evaluated_again(
    selections: list[ChooseWhenNode],
) -> None:
    application = _application(_INACTIVE)
    memo = EvalMemo()
    first = _ui(application, {}, memo)
    first.ui()
    for values in list(_trials(application))[1:]:
        selections.clear()
        changed = _ui(application, values, memo)
        nodes = changed.ui()
        assert nodes == _ui(application, values, None).ui()
        if nodes != first.ui():
            # Something reads the value differently: evaluated again.
            assert selections


def test_a_pass_that_records_more_evaluates_anew(
    selections: list[ChooseWhenNode],
) -> None:
    application = _application(_INACTIVE)
    pure = _pure_chooses(application)
    memo = EvalMemo()
    _ui(application, {}, memo).ui()
    selections.clear()
    ui = _ui(application, {}, memo)
    # The gates on each group object's way are recorded in such a pass.
    ui.active_parameter_driven_com_object_ref_ids()
    assert {id(node) for node in selections} & pure


def test_another_thread_evaluates_anew(selections: list[ChooseWhenNode]) -> None:
    application = _application(_INACTIVE)
    memo = EvalMemo()
    _ui(application, {}, memo).ui()
    pure = _pure_chooses(application)
    selections.clear()
    elsewhere = threading.Thread(target=lambda: _ui(application, {}, memo).ui())
    elsewhere.start()
    elsewhere.join()
    assert {id(node) for node in selections} & pure


_RENAMING = "M-00FA_A-00FD-01-0000"
_MODE = f"{_RENAMING}_P-1_R-1"
_OTHER = f"{_RENAMING}_P-2_R-2"
_RENAMING_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_RENAMING}" ApplicationNumber="253" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Renamed block"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_RENAMING}_PT-OnOff" Name="OnOff"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_RENAMING}_PT-OnOff_EN-0" />
<Enumeration Text="On" Value="1" Id="{_RENAMING}_PT-OnOff_EN-1" />
</TypeRestriction></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_RENAMING}_P-1" Name="Mode" ParameterType="{_RENAMING}_PT-OnOff" Text="Mode" Value="0" />
<Parameter Id="{_RENAMING}_P-2" Name="Other" ParameterType="{_RENAMING}_PT-OnOff" Text="Other" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_MODE}" RefId="{_RENAMING}_P-1" />
<ParameterRef Id="{_OTHER}" RefId="{_RENAMING}_P-2" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock>
<ParameterBlock Id="{_RENAMING}_PB-1" Text="Plain">
<ParameterRefRef RefId="{_MODE}" />
<ParameterRefRef RefId="{_OTHER}" />
<choose ParamRefId="{_MODE}"><when test="1">
<Rename Id="{_RENAMING}_PB-1_RN-1" RefId="{_RENAMING}_PB-1" Text="Renamed" />
</when></choose>
</ParameterBlock>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _block_text(ui: DynamicUI) -> str | None:
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if getattr(node, "id", None) == f"{_RENAMING}_PB-1":
            return getattr(node, "text", None)
        stack.extend(getattr(node, "children", ()))
    return None


def test_a_block_a_rename_retitles_is_taken_again_with_its_text(
    selections: list[ChooseWhenNode],
) -> None:
    application = parse_application_xml(_RENAMING_XML.encode(), "M-00FA")[0]
    # The Choose with the Rename and the block around it change nothing but the marks and
    # the text they record.
    assert _pure_chooses(application)
    memo = EvalMemo()
    trials = [{}, {_MODE: "1"}, {_MODE: "1", _OTHER: "1"}, {_OTHER: "1"}, {_MODE: "1"}]
    for values in trials + trials:
        ui = _ui(application, values, memo)
        anew = _ui(application, values, None)
        assert _observed(ui) == _observed(anew), values
        assert _block_text(ui) == ("Renamed" if values.get(_MODE) == "1" else "Plain")
    # Taken again: the last trials select nothing anew.
    selections.clear()
    _ui(application, {_MODE: "1"}, memo).ui()
    assert not selections


@pytest.fixture
def fast_checks(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """What the check without reading again (EvalMemo.unchanged) told from now on, for every
    recording however few its reads; each one that tells the reads give the same is checked by
    reading them again."""
    monkeypatch.setattr(context, "_READ_AGAIN", 0)
    told: list[bool] = []
    unchanged = EvalMemo.unchanged

    def checked(recording: Recording, ctx: EvalContext) -> bool:
        result = unchanged(recording, ctx)
        told.append(result)
        if result:
            assert ctx.reads_same(recording)
        return result

    monkeypatch.setattr(EvalMemo, "unchanged", staticmethod(checked))
    return told


@pytest.mark.parametrize("name", _APPLICATIONS)
def test_what_is_told_without_reading_again_is_what_reading_again_gives(
    name: str, fast_checks: list[bool]
) -> None:
    application = _application(name)
    memo = EvalMemo()
    trials = list(_trials(application))
    for values in trials + trials:
        assert _observed(_ui(application, values, memo)) == _observed(
            _ui(application, values, None)
        ), values


def test_the_same_state_again_is_told_without_reading_again(
    fast_checks: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application(_INACTIVE)
    memo = EvalMemo()
    _ui(application, {}, memo).ui()
    reads: list[object] = []
    reads_same = EvalContext.reads_same

    def counted(self: EvalContext, recording: Recording) -> bool:
        if self._scope.is_root():  # pyright: ignore[reportPrivateUsage]
            reads.append(recording)
        return reads_same(self, recording)

    fast_checks.clear()
    monkeypatch.setattr(EvalContext, "reads_same", counted)
    nodes = _ui(application, {}, memo).ui()
    # Each pass compares with the same pass of the first evaluation: nothing differs, and
    # nothing of the application's scope is read again but to check what was told (see
    # fast_checks; a module instance's is read again).
    assert True in fast_checks
    assert len(reads) == fast_checks.count(True)
    assert nodes == _ui(application, {}, None).ui()


def test_a_value_read_that_changed_is_read_again(fast_checks: list[bool]) -> None:
    application = parse_application_xml(_RENAMING_XML.encode(), "M-00FA")[0]
    memo = EvalMemo()
    _ui(application, {}, memo).ui()
    fast_checks.clear()
    ui = _ui(application, {_MODE: "1"}, memo)
    # The block reads the mode, which differs from the pass before: read again.
    assert _block_text(ui) == "Renamed"
    assert False in fast_checks


def test_a_pass_inside_another_is_compared_with_none() -> None:
    memo = EvalMemo()
    outer_scope = GlobalState()
    first = memo.begin_pass(outer_scope, True)
    memo.end_pass(first, outer_scope)
    outer = memo.begin_pass(outer_scope, True)
    assert outer.previous == first.number
    inner = memo.begin_pass(GlobalState(), True)
    # Neither tells what changed from a pass the other may have changed things in between.
    assert inner.previous is None
    assert not outer.clean
    assert not inner.clean
    memo.end_pass(inner, GlobalState())
    memo.end_pass(outer, outer_scope)
    assert memo.begin_pass(outer_scope, True).previous == first.number

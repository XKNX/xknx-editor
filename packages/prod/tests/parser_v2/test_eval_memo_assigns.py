"""The memo (see EvalMemo) takes a block or Choose with Assigns again where it reads the same,
the Assigns done again as they would write now; an evaluation that reads a value one of its
Assigns wrote is not taken again; and a Choose of a module instance a Repeat places is taken
again too. A UI evaluated with the memo is the UI evaluated anew, also after an edit."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.context import EvalContext, EvalMemo, Recording
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.nodes.choose import ChooseWhenNode
from xknxeditor.prod.parser_v2.nodes.com_object_parameter_block import (
    ComObjectParameterBlockNode,
)
from xknxeditor.prod.parser_v2.nodes.module import ModuleNode

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_ASSIGNING = sorted(
    path.name
    for path in _FIXTURES.glob("*_application.xml")
    if b"<Assign " in path.read_bytes()
)

_APP = "M-00FA_A-00FE-01-0000"
_MODE = f"{_APP}_P-1_R-1"
_TARGET = f"{_APP}_P-2_R-2"
_OTHER = f"{_APP}_P-3_R-3"
_COPY = f"{_APP}_P-4_R-4"


def _xml(block: str, modules: str = "") -> bytes:
    return f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="254" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assigning"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-OnOff" Name="OnOff"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
<Enumeration Text="On" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
</TypeRestriction></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-OnOff" Text="Mode" Value="0" />
<Parameter Id="{_APP}_P-2" Name="Target" ParameterType="{_APP}_PT-OnOff" Text="Target" Value="0" />
<Parameter Id="{_APP}_P-3" Name="Other" ParameterType="{_APP}_PT-OnOff" Text="Other" Value="0" />
<Parameter Id="{_APP}_P-4" Name="Copy" ParameterType="{_APP}_PT-OnOff" Text="Copy" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_MODE}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_TARGET}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_OTHER}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_COPY}" RefId="{_APP}_P-4" />
</ParameterRefs>
</Static>
{modules}
<Dynamic><ChannelIndependentBlock>
{block}
<ParameterBlock Id="{_APP}_PB-9" Text="Shown">
<ParameterRefRef RefId="{_TARGET}" />
<ParameterRefRef RefId="{_COPY}" />
</ParameterBlock>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>""".encode()


# A block whose Choose sets the target and copies the other setting where the mode is on.
_ASSIGNS = f"""<ParameterBlock Id="{_APP}_PB-1" Text="Mode">
<ParameterRefRef RefId="{_MODE}" />
<ParameterRefRef RefId="{_OTHER}" />
<choose ParamRefId="{_MODE}"><when test="1">
<Assign TargetParamRefRef="{_TARGET}" Value="1" />
<Assign TargetParamRefRef="{_COPY}" SourceParamRefRef="{_OTHER}" />
</when></choose>
</ParameterBlock>"""

# The same, and a Choose after it on the target it sets: it reads what the Assign wrote.
_READS_ITS_OWN = _ASSIGNS.replace(
    "</when></choose>",
    f"""</when></choose>
<choose ParamRefId="{_TARGET}"><when test="1">
<ParameterRefRef RefId="{_COPY}" />
</when></choose>""",
)

_MODULES = f"""<ModuleDefs><ModuleDef Id="{_APP}_MD-1" Name="Channel">
<Arguments><Argument Id="{_APP}_MD-1_A-1" Name="Ch" /></Arguments>
<Static>
<Parameters>
<Parameter Id="{_APP}_MD-1_P-1" Name="Kind" ParameterType="{_APP}_PT-OnOff" Text="Kind" Value="0" />
<Parameter Id="{_APP}_MD-1_P-2" Name="Detail" ParameterType="{_APP}_PT-OnOff" Text="Detail" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" />
<ParameterRef Id="{_APP}_MD-1_P-2_R-2" RefId="{_APP}_MD-1_P-2" />
</ParameterRefs>
</Static>
<Dynamic><ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Channel">
<ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
<choose ParamRefId="{_APP}_MD-1_P-1_R-1"><when test="1">
<ParameterRefRef RefId="{_APP}_MD-1_P-2_R-2" />
</when></choose>
</ParameterBlock></Dynamic>
</ModuleDef></ModuleDefs>"""

_REPEATED = f"""<ParameterBlock Id="{_APP}_PB-1" Text="Channels">
<ParameterRefRef RefId="{_MODE}" />
<Repeat Id="{_APP}_X-1" Name="" Count="2">
<Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="1" /></Module>
</Repeat>
</ParameterBlock>"""


def _parsed(xml: bytes) -> Application:
    return parse_application_xml(xml, "M-00FA")[0]


def _fixture(name: str) -> Application:
    data = (_FIXTURES / name).read_bytes()
    manufacturer = data.split(b'Manufacturer RefId="', 1)[1].split(b'"', 1)[0]
    return parse_application_xml(data, manufacturer.decode())[0]


def _ui(
    application: Application,
    values: dict[str, str],
    memo: EvalMemo | None,
    *,
    keep_assigned: bool = False,
) -> DynamicUI:
    program, builder = application.resolved()
    ui = DynamicUI(
        program,
        tree_builder=builder,
        parameter_values=values,
        keep_assigned=keep_assigned,
    )
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
        ui.explicit_values(),
        ui.assigned_from(),
        frozenset(ui.active_assigns()),
        memory,
    )


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


_TRIALS: list[dict[str, str]] = [
    {},
    {_MODE: "1"},
    {_MODE: "1", _OTHER: "1"},
    {_OTHER: "1"},
    {_MODE: "1", _TARGET: "0"},
]


@pytest.mark.parametrize("block", [_ASSIGNS, _READS_ITS_OWN])
def test_a_ui_with_assigns_evaluated_with_the_memo_is_the_ui_evaluated_anew(
    block: str,
) -> None:
    application = _parsed(_xml(block))
    memo = EvalMemo()
    for values in _TRIALS + _TRIALS:
        assert _observed(_ui(application, values, memo)) == _observed(
            _ui(application, values, None)
        ), values


def test_a_choose_with_assigns_that_reads_the_same_is_taken_again(
    selections: list[ChooseWhenNode],
) -> None:
    application = _parsed(_xml(_ASSIGNS))
    memo = EvalMemo()
    _ui(application, {_MODE: "1", _OTHER: "1"}, memo).ui()
    selections.clear()
    ui = _ui(application, {_MODE: "1", _OTHER: "1"}, memo)
    observed = _observed(ui)
    # Taken again, and its Assigns wrote as they do anew.
    assert not selections
    assert observed == _observed(_ui(application, {_MODE: "1", _OTHER: "1"}, None))
    assert ui.get_value(_TARGET) == "1"
    assert ui.get_value(_COPY) == "1"


def test_a_block_that_reads_what_its_assign_wrote_is_evaluated_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks: list[str] = []
    evaluate = ComObjectParameterBlockNode._eval  # pyright: ignore[reportPrivateUsage]

    def counted(self: ComObjectParameterBlockNode, ctx: object) -> object:
        blocks.append(self._elem.id)  # pyright: ignore[reportPrivateUsage]
        return evaluate(self, ctx)  # type: ignore[arg-type]

    monkeypatch.setattr(ComObjectParameterBlockNode, "_eval", counted)
    application = _parsed(_xml(_READS_ITS_OWN))
    memo = EvalMemo()
    _ui(application, {_MODE: "1"}, memo).ui()
    blocks.clear()
    ui = _ui(application, {_MODE: "1"}, memo)
    observed = _observed(ui)
    # The block reads the target after its Choose wrote it: evaluated again (each Choose in
    # it reads at its own start what it read before, and is taken again); the other block
    # is taken again.
    assert f"{_APP}_PB-1" in blocks
    assert f"{_APP}_PB-9" not in blocks
    assert observed == _observed(_ui(application, {_MODE: "1"}, None))


def test_a_block_that_is_never_replayable_is_no_longer_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[Recording] = []
    record = Recording.__init__

    def counted(self: Recording, *args: object) -> None:
        record(self, *args)  # type: ignore[arg-type]
        if self.assigns and not self.replayable:
            recorded.append(self)

    monkeypatch.setattr(Recording, "__init__", counted)
    application = _parsed(_xml(_READS_ITS_OWN))
    memo = EvalMemo()
    for n in range(8):
        values = {_MODE: "1", _OTHER: str(n % 2)}
        assert _observed(_ui(application, values, memo)) == _observed(
            _ui(application, values, None)
        )
    # The block and the channel around it, each recorded until it was not replayable
    # UNREPLAYABLE times, then evaluated as it is.
    assert len(recorded) == 2 * EvalMemo.UNREPLAYABLE


def test_a_ui_edited_after_an_evaluation_with_the_memo_is_the_one_evaluated_anew() -> (
    None
):
    for application in [_parsed(_xml(_ASSIGNS)), *map(_fixture, _ASSIGNING)]:
        memo = EvalMemo()
        refs = list(_ui(application, {}, None).parameter_ref_ids())
        for ref in refs:
            with_memo, anew = _ui(application, {}, memo), _ui(application, {}, None)
            with_memo.ui()
            anew.ui()
            for ui in (with_memo, anew):
                ui.apply_parameter_values({ref: "1"})
            assert _observed(with_memo) == _observed(anew), ref


def _recording(*events: object) -> Recording:
    return Recording(False, events, ())  # type: ignore[arg-type]


class _Assign:
    def __init__(self, target: str) -> None:
        self.target = target

    def redo(self, scope: object) -> None:
        pass


def test_a_recording_that_reads_what_its_assign_wrote_is_not_replayable() -> None:
    assign = _Assign("T")
    assert _recording(("g", "T", "0"), ("A", assign, None)).replayable
    assert not _recording(("A", assign, None), ("g", "T", "1")).replayable
    assert _recording(("A", assign, None), ("g", "U", "1")).replayable
    # A text argument after an Assign (a value by unique number) cannot be told.
    assert not _recording(("A", assign, None), ("x", "7", "1")).replayable
    assert _recording(("x", "7", "1"), ("A", assign, None)).replayable
    # Of one inside it: its reads after the Assigns before it, and its own.
    inner = _recording(("g", "T", "1"))
    assert not _recording(("A", assign, None), inner).replayable
    assert _recording(inner, ("A", assign, None)).replayable
    assert not _recording(_recording(("A", assign, None), ("g", "T", "1"))).replayable
    outer = _recording(_recording(("A", assign, None)), ("g", "T", "1"))
    assert not outer.replayable
    assert outer.assigns == (assign,)


def test_a_choose_of_a_module_instance_a_repeat_places_is_taken_again(
    selections: list[ChooseWhenNode],
) -> None:
    application = _parsed(_xml(_REPEATED, _MODULES))
    memo = EvalMemo()
    values = {_MODE: "1"}
    assert _observed(_ui(application, values, memo)) == _observed(
        _ui(application, values, None)
    )
    selections.clear()
    ui = _ui(application, values, memo)
    observed = _observed(ui)
    assert not selections
    assert observed == _observed(_ui(application, values, None))
    assert len(ui.get_module_instances()) == 2


# A module whose block sets its Detail and its Label where its Kind is on, copies the
# application's Other into its Copy, and reads them after that: the Detail and the Copy in
# Chooses on them, the Label in its heading only, by its unique number (4). It shows its
# Extra where the application's Target is on.
_ASSIGNING_MODULE = f"""<ModuleDefs><ModuleDef Id="{_APP}_MD-2" Name="Assigning">
<Arguments><Argument Id="{_APP}_MD-2_A-1" Name="Ch" /></Arguments>
<Static>
<Parameters>
<Parameter Id="{_APP}_MD-2_P-1" Name="Kind" ParameterType="{_APP}_PT-OnOff" Text="Kind" Value="0" />
<Parameter Id="{_APP}_MD-2_P-2" Name="Detail" ParameterType="{_APP}_PT-OnOff" Text="Detail" Value="0" />
<Parameter Id="{_APP}_MD-2_P-3" Name="Copy" ParameterType="{_APP}_PT-OnOff" Text="Copy" Value="0" />
<Parameter Id="{_APP}_MD-2_P-4" Name="Label" ParameterType="{_APP}_PT-OnOff" Text="Label" Value="0" />
<Parameter Id="{_APP}_MD-2_P-5" Name="Extra" ParameterType="{_APP}_PT-OnOff" Text="Extra" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_APP}_MD-2_P-1_R-1" RefId="{_APP}_MD-2_P-1" />
<ParameterRef Id="{_APP}_MD-2_P-2_R-2" RefId="{_APP}_MD-2_P-2" />
<ParameterRef Id="{_APP}_MD-2_P-3_R-3" RefId="{_APP}_MD-2_P-3" />
<ParameterRef Id="{_APP}_MD-2_P-4_R-4" RefId="{_APP}_MD-2_P-4" />
<ParameterRef Id="{_APP}_MD-2_P-5_R-5" RefId="{_APP}_MD-2_P-5" />
</ParameterRefs>
</Static>
<Dynamic><ParameterBlock Id="{_APP}_MD-2_PB-1" Text="Channel {{{{Ch}}}}: {{{{4}}}}">
<ParameterRefRef RefId="{_APP}_MD-2_P-1_R-1" />
<choose ParamRefId="{_APP}_MD-2_P-1_R-1"><when test="1">
<Assign TargetParamRefRef="{_APP}_MD-2_P-2_R-2" Value="1" />
<Assign TargetParamRefRef="{_APP}_MD-2_P-4_R-4" Value="1" />
</when></choose>
<Assign TargetParamRefRef="{_APP}_MD-2_P-3_R-3" SourceParamRefRef="{_OTHER}" />
<choose ParamRefId="{_APP}_MD-2_P-2_R-2"><when test="1">
<ParameterRefRef RefId="{_APP}_MD-2_P-2_R-2" />
</when></choose>
<choose ParamRefId="{_APP}_MD-2_P-3_R-3"><when test="1">
<ParameterRefRef RefId="{_APP}_MD-2_P-3_R-3" />
</when></choose>
<choose ParamRefId="{_TARGET}"><when test="1">
<ParameterRefRef RefId="{_APP}_MD-2_P-5_R-5" />
</when></choose>
</ParameterBlock></Dynamic>
</ModuleDef></ModuleDefs>"""

# The application's Other the instances copy, which its Mode sets (in the pass, before them).
_ASSIGNING_REPEATED = f"""<ParameterBlock Id="{_APP}_PB-1" Text="Channels">
<ParameterRefRef RefId="{_MODE}" />
<ParameterRefRef RefId="{_OTHER}" />
<choose ParamRefId="{_MODE}"><when test="1">
<Assign TargetParamRefRef="{_OTHER}" Value="1" />
</when></choose>
<Repeat Id="{_APP}_X-1" Name="" Count="2">
<Module Id="{_APP}_MD-2_M-1" RefId="{_APP}_MD-2"><NumericArg RefId="{_APP}_MD-2_A-1" Value="1" /></Module>
</Repeat>
</ParameterBlock>"""

_KIND_1 = f"{_APP}_MD-2_M-1_MI-1_P-1_R-1"
_KIND_2 = f"{_APP}_MD-2_M-1_MI-2_P-1_R-1"
_DETAIL_1 = f"{_APP}_MD-2_M-1_MI-1_P-2_R-2"

_INSTANCE_TRIALS: list[dict[str, str]] = [
    {},
    {_KIND_1: "1"},
    {_KIND_1: "1", _OTHER: "1"},
    {_OTHER: "1"},
    {_KIND_2: "1"},
    {_KIND_1: "1", _KIND_2: "1", _OTHER: "1"},
    {_KIND_1: "1", _DETAIL_1: "0"},
    {_MODE: "1"},
    {_MODE: "1", _KIND_1: "1"},
    {_MODE: "1", _OTHER: "0"},
]


def _instance_blocks(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """The module instances whose block is evaluated from now on."""
    blocks: list[str | None] = []
    evaluate = ComObjectParameterBlockNode._eval  # pyright: ignore[reportPrivateUsage]

    def counted(self: ComObjectParameterBlockNode, ctx: EvalContext) -> object:
        if self._elem.id.endswith("_MD-2_PB-1"):  # pyright: ignore[reportPrivateUsage]
            blocks.append(ctx.module_instance_id)
        return evaluate(self, ctx)

    monkeypatch.setattr(ComObjectParameterBlockNode, "_eval", counted)
    return blocks


def test_a_module_instance_block_that_reads_what_its_assigns_wrote_is_taken_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = _instance_blocks(monkeypatch)
    application = _parsed(_xml(_ASSIGNING_REPEATED, _ASSIGNING_MODULE))
    memo = EvalMemo()
    values = {_KIND_1: "1", _OTHER: "1"}
    _ui(application, values, memo).ui()
    blocks.clear()
    ui = _ui(application, values, memo)
    observed = _observed(ui)
    # Each instance's block reads the same, its Assigns writing what they wrote: taken
    # again, and it is what the evaluation anew gives.
    assert not blocks
    assert observed == _observed(_ui(application, values, None))
    assert ui.get_value(_DETAIL_1) == "1"
    headings = [
        child.text
        for node in observed[0]  # type: ignore[attr-defined]
        for block in node.children
        for child in block.children
        if str(getattr(child, "id", "")).endswith("_MD-2_PB-1")
    ]
    assert headings == ["Channel 1: 1", "Channel 1: 0"]


def test_a_module_instance_block_whose_assign_writes_otherwise_is_evaluated_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = _instance_blocks(monkeypatch)
    application = _parsed(_xml(_ASSIGNING_REPEATED, _ASSIGNING_MODULE))
    memo = EvalMemo()
    _ui(application, {_OTHER: "1"}, memo).ui()
    blocks.clear()
    # The Other its Assign copies differs: each instance is evaluated again.
    values = {_OTHER: "0"}
    observed = _observed(_ui(application, values, memo))
    assert set(blocks) == {f"{_APP}_MD-2_M-1_MI-1", f"{_APP}_MD-2_M-1_MI-2"}
    assert observed == _observed(_ui(application, values, None))
    blocks.clear()
    # Only the first instance's Kind differs: the second one is taken again.
    values = {_OTHER: "0", _KIND_1: "1"}
    observed = _observed(_ui(application, values, memo))
    assert set(blocks) == {f"{_APP}_MD-2_M-1_MI-1"}
    assert observed == _observed(_ui(application, values, None))


_LABEL_1 = f"{_APP}_MD-2_M-1_MI-1_P-4_R-4"


@pytest.mark.parametrize("kept", [_DETAIL_1, _LABEL_1])
def test_a_value_an_assign_no_longer_writes_is_read_as_it_is(kept: str) -> None:
    # Evaluated as a device built again after an edit, the instance's Assigns keep the value
    # it has: everything but what the Assign wrote reads as before (the Kind, the value at
    # the start), and the Detail read in a Choose or the Label shown in the heading (a text
    # argument) gives the value kept, not the one written before.
    application = _parsed(_xml(_ASSIGNING_REPEATED, _ASSIGNING_MODULE))
    memo = EvalMemo()
    values = {_KIND_1: "1", kept: "0"}
    _ui(application, values, memo).ui()
    with_memo = _ui(application, values, memo, keep_assigned=True)
    anew = _ui(application, values, None, keep_assigned=True)
    assert _observed(with_memo) == _observed(anew)
    assert with_memo.get_value(kept) == "0"


def _instance_reads(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """The module instances whose evaluations are read again from now on (to tell whether
    the memo may take them, see EvalContext.reads_same)."""
    read: list[str | None] = []
    reads_same = EvalContext.reads_same

    def counted(self: EvalContext, *args: object) -> bool:
        if self.module_instance_id:
            read.append(self.module_instance_id)
        return reads_same(self, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(EvalContext, "reads_same", counted)
    return read


_COPY_1 = f"{_APP}_MD-2_M-1_MI-1_P-3_R-3"


@pytest.mark.parametrize(
    "turns",
    [
        [{_KIND_1: "1", _OTHER: "1"}],
        # The first instance with a value of its own its Assign writes over, and without.
        [{_KIND_1: "1"}, {_KIND_1: "1", _COPY_1: "0"}],
    ],
)
def test_a_module_instance_block_that_holds_what_it_held_is_not_read_again(
    monkeypatch: pytest.MonkeyPatch, turns: list[dict[str, str]]
) -> None:
    read = _instance_reads(monkeypatch)
    blocks = _instance_blocks(monkeypatch)
    application = _parsed(_xml(_ASSIGNING_REPEATED, _ASSIGNING_MODULE))
    memo = EvalMemo()
    for values in turns * 3:
        _ui(application, values, memo).ui()
    read.clear()
    blocks.clear()
    for values in turns:
        observed = _observed(_ui(application, values, memo))
        # The instances hold what they held, and nothing they read of the application
        # changed since the pass before: taken again without reading again.
        assert not read
        assert not blocks
        assert observed == _observed(_ui(application, values, None))
        blocks.clear()


@pytest.mark.parametrize("written", [False, True])
def test_a_module_instance_block_whose_application_values_changed_is_evaluated_again(
    monkeypatch: pytest.MonkeyPatch, written: bool
) -> None:
    blocks = _instance_blocks(monkeypatch)
    application = _parsed(_xml(_ASSIGNING_REPEATED, _ASSIGNING_MODULE))
    memo = EvalMemo()
    for _ in range(3):
        _ui(application, {_TARGET: "0"}, memo).ui()
    blocks.clear()
    values = {_TARGET: "0"}
    if written:
        # The Target is the same when each pass starts, but written in it while the
        # instances are evaluated (and written back after them).
        evaluate = ModuleNode.eval

        def writing(self: ModuleNode, ctx: EvalContext) -> object:
            ctx.set(_TARGET, "1")
            try:
                return evaluate(self, ctx)
            finally:
                ctx.set(_TARGET, "0")

        monkeypatch.setattr(ModuleNode, "eval", writing)
    else:
        # The Target differs from the pass before.
        values = {_TARGET: "1"}
    ui = _ui(application, values, memo)
    observed = _observed(ui)
    assert set(blocks) == {f"{_APP}_MD-2_M-1_MI-1", f"{_APP}_MD-2_M-1_MI-2"}
    assert observed == _observed(_ui(application, values, None))
    assert f"{_APP}_MD-2_M-1_MI-1_P-5_R-5" in ui.active_parameter_ref_ids()


def test_a_module_instance_block_taken_in_another_pass_than_the_one_before_is_read_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read = _instance_reads(monkeypatch)
    application = _parsed(_xml(_ASSIGNING_REPEATED, _ASSIGNING_MODULE))
    memo = EvalMemo()
    for _ in range(3):
        _ui(application, {}, memo).ui()
    # As if taken last in a pass before the one before: what changed since is not known.
    for _node, in_orders in memo._in_order.values():  # pyright: ignore[reportPrivateUsage]
        for in_order in in_orders:
            in_order.valid = -1
    read.clear()
    observed = _observed(_ui(application, {}, memo))
    assert set(read) == {f"{_APP}_MD-2_M-1_MI-1", f"{_APP}_MD-2_M-1_MI-2"}
    assert observed == _observed(_ui(application, {}, None))


def test_module_instances_with_assigns_evaluated_with_the_memo_are_evaluated_anew() -> (
    None
):
    application = _parsed(_xml(_ASSIGNING_REPEATED, _ASSIGNING_MODULE))
    memo = EvalMemo()
    for values in _INSTANCE_TRIALS + _INSTANCE_TRIALS[::-1] + _INSTANCE_TRIALS:
        assert _observed(_ui(application, values, memo)) == _observed(
            _ui(application, values, None)
        ), values
    # Edited after an evaluation: an Assign writes where its source changed only.
    for edit in ({_OTHER: "1"}, {_KIND_2: "1"}, {_DETAIL_1: "1"}, {_OTHER: "0"}):
        with_memo, anew = _ui(application, {}, memo), _ui(application, {}, None)
        for ui in (with_memo, anew):
            ui.ui()
            ui.apply_parameter_values(edit)
        assert _observed(with_memo) == _observed(anew), edit
        for ui in (with_memo, anew):
            ui.apply_parameter_values({_KIND_1: "1"})
        assert _observed(with_memo) == _observed(anew), edit

"""A block of many settings, each with Chooses that pick its group objects, has its children
taken from the memo in parts (see EvalMemo.eval_children): only those whose reads changed are
evaluated again, and the UI is the one evaluated anew. A block with an Assign in it is
evaluated child by child as before."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.context import EvalMemo
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.nodes.choose import ChooseWhenNode

_APP = "M-00FA_A-00FD-01-0000"
_SETTINGS = 300


def _setting(n: int) -> str:
    return f"{_APP}_P-{n}_R-{n}"


def _xml(*, assign: bool = False) -> bytes:
    parameters = "".join(
        f'<Parameter Id="{_APP}_P-{n}" Name="S{n}" ParameterType="{_APP}_PT-Kind"'
        f' Text="Setting {n}" Value="0" />'
        for n in range(1, _SETTINGS + 1)
    )
    refs = "".join(
        f'<ParameterRef Id="{_setting(n)}" RefId="{_APP}_P-{n}" />'
        for n in range(1, _SETTINGS + 1)
    )
    objects = "".join(
        f'<ComObject Id="{_APP}_O-{n}" Name="O{n}" Text="Object {n}" Number="{n}"'
        ' FunctionText="Switch" ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled"'
        ' CommunicationFlag="Enabled" TransmitFlag="Disabled" UpdateFlag="Disabled"'
        ' ReadOnInitFlag="Disabled" />'
        for n in range(1, _SETTINGS + 1)
    )
    object_refs = "".join(
        f'<ComObjectRef Id="{_APP}_O-{n}_R-{n}" RefId="{_APP}_O-{n}" />'
        f'<ComObjectRef Id="{_APP}_O-{n}_R-{n + _SETTINGS}" RefId="{_APP}_O-{n}"'
        ' ObjectSize="1 Byte" />'
        for n in range(1, _SETTINGS + 1)
    )
    # Each setting picks one of its object's two sizes (none for 0).
    children = "".join(
        f'<ParameterRefRef RefId="{_setting(n)}" />'
        f'<choose ParamRefId="{_setting(n)}"><when test="1">'
        f'<ComObjectRefRef RefId="{_APP}_O-{n}_R-{n}" /></when></choose>'
        f'<choose ParamRefId="{_setting(n)}"><when test="2">'
        f'<ComObjectRefRef RefId="{_APP}_O-{n}_R-{n + _SETTINGS}" /></when></choose>'
        for n in range(1, _SETTINGS + 1)
    )
    if assign:
        children += (
            f'<choose ParamRefId="{_setting(1)}"><when test="2">'
            f'<Assign TargetParamRefRef="{_setting(2)}" Value="1" /></when></choose>'
        )
    return f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="253" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Gateway"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-Kind" Name="Kind"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="None" Value="0" Id="{_APP}_PT-Kind_EN-0" />
<Enumeration Text="Bit" Value="1" Id="{_APP}_PT-Kind_EN-1" />
<Enumeration Text="Byte" Value="2" Id="{_APP}_PT-Kind_EN-2" />
</TypeRestriction></ParameterType>
</ParameterTypes>
<Parameters>{parameters}</Parameters>
<ParameterRefs>{refs}</ParameterRefs>
<ComObjectTable>{objects}</ComObjectTable>
<ComObjectRefs>{object_refs}</ComObjectRefs>
</Static>
<Dynamic><Channel Id="{_APP}_CH-1" Name="A" Text="Gateway" Number="1">
<ParameterBlock Id="{_APP}_PB-1" Text="Points">{children}</ParameterBlock>
</Channel></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>""".encode()


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_xml(), "M-00FA")[0]


@pytest.fixture(scope="module")
def assigning() -> Application:
    return parse_application_xml(_xml(assign=True), "M-00FA")[0]


def _ui(
    application: Application, values: dict[str, str], memo: EvalMemo | None
) -> DynamicUI:
    program, builder = application.resolved()
    ui = DynamicUI(program, tree_builder=builder, parameter_values=values)
    ui.eval_memo = memo
    return ui


def _observed(ui: DynamicUI) -> tuple[object, ...]:
    state = ui._state  # pyright: ignore[reportPrivateUsage]
    return (
        ui.ui(),
        # What the evaluation marked active.
        state.active_param_refs(),
        state.active_com_object_refs(),
        ui.active_parameter_ref_ids(),
        ui.active_parameter_driven_com_object_ref_ids(),
        ui.explicit_values(),
        ui.encode_to_memory(),
    )


@pytest.fixture
def selections(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The parameters of the Chooses that evaluate their branches from now on."""
    selected: list[str] = []
    select = ChooseWhenNode._select  # pyright: ignore[reportPrivateUsage]

    def counted(self: ChooseWhenNode, ctx: object) -> object:
        selected.append(self._param_ref_id)  # pyright: ignore[reportPrivateUsage]
        return select(self, ctx)  # type: ignore[arg-type]

    monkeypatch.setattr(ChooseWhenNode, "_select", counted)
    return selected


_TRIALS: list[dict[str, str]] = [
    {},
    {_setting(5): "1"},
    {_setting(5): "2"},
    {_setting(5): "2", _setting(200): "1"},
    {_setting(7): "1"},
    {_setting(5): "1", _setting(7): "2", _setting(300): "2"},
    {},
]


@pytest.mark.parametrize("name", ["application", "assigning"])
def test_a_ui_of_a_block_taken_in_parts_is_the_ui_evaluated_anew(
    name: str, request: pytest.FixtureRequest
) -> None:
    application: Application = request.getfixturevalue(name)
    memo = EvalMemo()
    for values in _TRIALS + _TRIALS[::-1]:
        assert _observed(_ui(application, values, memo)) == _observed(
            _ui(application, values, None)
        ), values
    # Edited after an evaluation.
    with_memo, anew = _ui(application, {}, memo), _ui(application, {}, None)
    for values in ({_setting(9): "2"}, {_setting(9): "0", _setting(10): "1"}):
        for ui in (with_memo, anew):
            ui.ui()
            ui.apply_parameter_values(dict(values))
        assert _observed(with_memo) == _observed(anew), values


def test_only_the_children_whose_reads_changed_are_evaluated_again(
    application: Application, selections: list[str]
) -> None:
    memo = EvalMemo()
    for values in ({}, {_setting(5): "1"}, {}):
        _ui(application, values, memo).ui()
    selections.clear()
    ui = _ui(application, {_setting(8): "2"}, memo)
    ui.ui()
    # The Chooses of the setting tried, and of the one tried before (back to its value).
    assert selections
    assert set(selections) <= {_setting(5), _setting(8)}
    assert _observed(ui) == _observed(_ui(application, {_setting(8): "2"}, None))


def test_a_block_with_an_assign_is_evaluated_child_by_child(
    application: Application, assigning: Application
) -> None:
    for app, parts in ((application, True), (assigning, False)):
        memo = EvalMemo()
        for values in ({}, {_setting(5): "1"}):
            _ui(app, values, memo).ui()
        assert bool(memo._parts) is parts  # pyright: ignore[reportPrivateUsage]

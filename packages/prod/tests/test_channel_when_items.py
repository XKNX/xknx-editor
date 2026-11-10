"""Parameters and separators straight in a ``when`` of a channel's ``choose`` (where the schemas
allow pages and group objects only) are left out: the product imports, and they never show."""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import (
    ApplicationProgramChannel,
    ChannelChoose,
    ComObjectParameterBlock,
    ParameterRefRef,
    ParameterSeparator,
)
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.heal import drop_channel_when_items
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import (
    UiNode,
    UiParameter,
    UiParameterBlock,
    UiSeparator,
    UiTab,
)

APP = "M-00FA_A-0003-01-0000"


def _parameter(number: int, value: str = "0") -> str:
    return (
        f'<Parameter Id="{APP}_P-{number}" Name="P{number}" ParameterType="{APP}_PT-N" '
        f'Text="Parameter {number}" Value="{value}" />'
    )


def _xml(channel_when: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">'
        '<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>'
        f'<ApplicationProgram Id="{APP}" ApplicationNumber="3" ApplicationVersion="1" '
        'ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Loose" '
        'LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US" '
        'DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">'
        "<Static><ParameterTypes>"
        f'<ParameterType Id="{APP}_PT-N" Name="N">'
        '<TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />'
        "</ParameterType></ParameterTypes><Parameters>"
        + "".join(_parameter(n) for n in (1, 2, 3, 4))
        + "</Parameters><ParameterRefs>"
        + "".join(
            f'<ParameterRef Id="{APP}_P-{n}_R-{n}" RefId="{APP}_P-{n}" />'
            for n in (1, 2, 3, 4)
        )
        + "</ParameterRefs></Static><Dynamic>"
        f'<Channel Id="{APP}_CH-1" Name="Channel" Text="Channel" Number="1">'
        f'<ParameterBlock Id="{APP}_PB-1" Text="Main">'
        f'<ParameterRefRef RefId="{APP}_P-1_R-1" /></ParameterBlock>'
        f'<choose ParamRefId="{APP}_P-1_R-1"><when test="1">{channel_when}</when></choose>'
        "</Channel></Dynamic></ApplicationProgram>"
        "</ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"
    ).encode()


LOOSE = (
    f'<ParameterRefRef RefId="{APP}_P-2_R-2" />'
    f'<ParameterSeparator Id="{APP}_PS-1" Text="loose-separator" />'
    f'<ParameterBlock Id="{APP}_PB-2" Text="Second">'
    f'<ParameterRefRef RefId="{APP}_P-4_R-4" /></ParameterBlock>'
    f'<choose ParamRefId="{APP}_P-2_R-2"><when test="1">'
    f'<ParameterRefRef RefId="{APP}_P-3_R-3" /></when></choose>'
)


def _ui(xml: bytes) -> DynamicUI:
    return DynamicUI(parse_application_xml(xml, "M-00FA")[0].program)


def _shown(nodes: tuple[UiNode, ...] | list[UiNode]) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, UiParameter):
            out.add(node.ref_id)
        elif isinstance(node, UiSeparator) and node.text:
            out.add(node.text)
        elif isinstance(node, (UiTab, UiParameterBlock)):
            out |= _shown(node.children)
    return out


def test_the_items_are_left_out_and_the_rest_is_kept() -> None:
    (app,) = parse_application_xml(_xml(LOOSE), "M-00FA")
    assert app.program.dynamic is not None
    channel = app.program.dynamic.choice[0]
    assert isinstance(channel, ApplicationProgramChannel)
    choose = channel.choice[1]
    assert isinstance(choose, ChannelChoose)
    items = list(choose.when[0].choice)
    assert [type(item) for item in items] == [ComObjectParameterBlock, ChannelChoose]
    assert isinstance(items[0], ComObjectParameterBlock)
    assert items[0].id == f"{APP}_PB-2"
    assert not any(
        isinstance(item, (ParameterRefRef, ParameterSeparator)) for item in items
    )


def test_the_items_never_show_while_the_rest_follows_its_chooses() -> None:
    ui = _ui(_xml(LOOSE))
    assert f"{APP}_P-4_R-4" not in ui.active_parameter_ref_ids()
    ui.set_parameter_ref(f"{APP}_P-1_R-1", "1")
    active = ui.active_parameter_ref_ids()
    # The page in the when shows; the items placed straight in it do not.
    assert f"{APP}_P-4_R-4" in active
    assert f"{APP}_P-2_R-2" not in active
    assert "loose-separator" not in _shown(ui.ui())
    assert f"{APP}_P-2_R-2" not in _shown(ui.ui())


def test_a_file_without_such_items_is_not_rewritten() -> None:
    assert (
        drop_channel_when_items(
            _xml(f'<ParameterBlock Id="{APP}_PB-2" Text="Second" />')
        )
        is None
    )

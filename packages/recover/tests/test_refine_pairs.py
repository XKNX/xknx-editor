"""Coupled selectors can improve a recovery across a worse intermediate layout."""

from __future__ import annotations

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0008-01-0000"
_FIRST = f"{_APP}_P-1_R-1"
_SECOND = f"{_APP}_P-2_R-2"
_SEGMENT = f"{_APP}_AS-0000"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="8" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Coupled selectors"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-Selector" Name="Selector"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_APP}_PT-Selector_EN-0" />
<Enumeration Text="On" Value="1" Id="{_APP}_PT-Selector_EN-1" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-N" Name="Value"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Function" ParameterType="{_APP}_PT-Selector" Text="Function" Value="0" />
<Parameter Id="{_APP}_P-2" Name="Subtype" ParameterType="{_APP}_PT-Selector" Text="Subtype" Value="0" />
<Parameter Id="{_APP}_P-3" Name="FirstValue" ParameterType="{_APP}_PT-N" Text="First value" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-4" Name="SecondValue" ParameterType="{_APP}_PT-N" Text="Second value" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_FIRST}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_SECOND}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_APP}_P-3_R-3" RefId="{_APP}_P-3" />
<ParameterRef Id="{_APP}_P-4_R-4" RefId="{_APP}_P-4" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Settings">
<ParameterRefRef RefId="{_FIRST}" />
<choose ParamRefId="{_FIRST}">
<when test="0"><ParameterRefRef RefId="{_APP}_P-3_R-3" /></when>
<when test="1"><ParameterRefRef RefId="{_SECOND}" />
<choose ParamRefId="{_SECOND}"><when test="1">
<ParameterRefRef RefId="{_APP}_P-3_R-3" /><ParameterRefRef RefId="{_APP}_P-4_R-4" />
</when></choose></when></choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


@pytest.fixture
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_default_search_finds_pair_across_worse_intermediate_layout(
    application: Application,
) -> None:
    segments = {_SEGMENT: b"\x07\x09\x00"}
    # The fast search finds it behind the selector that fits worse alone (its deeper search).
    fast = recover_parameters(application, segments, effort=RecoveryEffort.FAST)
    assert fast.complete
    medium = recover_parameters(application, segments)
    assert medium.complete
    assert medium.values[_FIRST] == medium.values[_SECOND] == "1"
    assert medium.values[f"{_APP}_P-3_R-3"] == "7"
    assert medium.values[f"{_APP}_P-4_R-4"] == "9"


@pytest.mark.parametrize("stop", ["cancel", "budget"])
def test_an_interrupted_pair_keeps_the_previous_result(
    application: Application, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    segments = {_SEGMENT: b"\x07\x09\x00"}
    # A result the pair search starts from: the fast search without its deeper search.
    with monkeypatch.context() as patch:
        patch.setattr(parameters, "_DEEP_STARTS", 0)
        original = recover_parameters(application, segments, effort=RecoveryEffort.FAST)
    assert original.differing_bytes == 1
    if stop == "budget":
        monkeypatch.setattr(parameters, "_PAIR_TRIES", 1)
    recovered = parameters._refine_pairs(
        application,
        segments,
        {},
        None,
        set(),
        {},
        original,
        1,
        cancel=(lambda: True) if stop == "cancel" else None,
    )
    assert recovered.values == original.values
    assert recovered.differing_bytes == 1
    assert recovered.limited


def test_a_stored_selector_is_not_overridden() -> None:
    xml = _XML.replace(
        'Text="Function" Value="0" />',
        f'Text="Function" Value="0"><Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" /></Parameter>',
    )
    application = parse_application_xml(xml.encode(), "M-00FA")[0]
    recovered = recover_parameters(application, {_SEGMENT: b"\x07\x09\x00"})
    assert recovered.values[_FIRST] == "0"
    assert recovered.differing_bytes == 1


def test_a_calculated_selector_is_not_guessed() -> None:
    calculation = f"""<ParameterCalculations>
<ParameterCalculation Id="{_APP}_PC-1" Name="Stored mode" Language="JavaScript"
    LRTransformationFunc="mode" RLTransformationFunc="noop">
<LParameters><ParameterRefRef RefId="{_APP}_P-3_R-3" AliasName="stored" /></LParameters>
<RParameters><ParameterRefRef RefId="{_FIRST}" AliasName="mode" /></RParameters>
</ParameterCalculation></ParameterCalculations>
<Script>function mode(input, output) {{ output.mode = input.stored &gt; 10 ? 1 : 0; }}
function noop(input, output) {{}}</Script>"""
    application = parse_application_xml(
        _XML.replace("</Static>", calculation + "</Static>").encode(), "M-00FA"
    )[0]
    recovered = recover_parameters(application, {_SEGMENT: b"\x07\x09\x00"})
    ui = seed_dynamic_ui(application, recovered.values)
    assert ui is not None
    assert ui.get_value(_FIRST) == "0"
    assert recovered.differing_bytes == 1


def test_local_search_accepts_a_single_selector_without_children() -> None:
    dynamic = f"""<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Settings">
<ParameterRefRef RefId="{_SECOND}" />
<choose ParamRefId="{_SECOND}"><when test="1">
<ParameterRefRef RefId="{_APP}_P-3_R-3" /><ParameterRefRef RefId="{_APP}_P-4_R-4" />
</when></choose></ParameterBlock></ChannelIndependentBlock></Dynamic>"""
    before, rest = _XML.split("<Dynamic>")
    _, after = rest.split("</Dynamic>")
    application = parse_application_xml((before + dynamic + after).encode(), "M-00FA")[
        0
    ]
    segments = {_SEGMENT: b"\x07\x09\x00"}
    values, unknown = parameters._decode(application, segments, {}, None, {})
    recovered = parameters._refine_pairs(
        application,
        segments,
        {},
        None,
        set(),
        {},
        parameters.RecoveredParameters(values, unknown, differing_bytes=2),
        1,
    )
    assert recovered.complete
    assert recovered.values[_SECOND] == "1"
    assert recovered.values[f"{_APP}_P-3_R-3"] == "7"
    assert recovered.values[f"{_APP}_P-4_R-4"] == "9"


def test_dependent_pair_resolves_unlinked_object_sizes() -> None:
    objects = f"""<ComObjectTable>
<ComObject Id="{_APP}_O-0" Name="Value" Text="Value" Number="0" ObjectSize="1 Bit"
 FunctionText="" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
 TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" />
<ComObject Id="{_APP}_O-1" Name="Status" Text="Status" Number="1" ObjectSize="1 Bit"
 FunctionText="" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
 TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" />
</ComObjectTable><ComObjectRefs>
<ComObjectRef Id="{_APP}_O-0_R-0" RefId="{_APP}_O-0" />
<ComObjectRef Id="{_APP}_O-0_R-1" RefId="{_APP}_O-0" ObjectSize="2 Bytes" />
<ComObjectRef Id="{_APP}_O-1_R-0" RefId="{_APP}_O-1" />
<ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" ObjectSize="2 Bytes" />
</ComObjectRefs>"""
    dynamic = f"""<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Settings">
<ParameterRefRef RefId="{_FIRST}" />
<choose ParamRefId="{_FIRST}">
<when test="0"><ComObjectRefRef RefId="{_APP}_O-0_R-0" /></when>
<when test="1"><ParameterRefRef RefId="{_SECOND}" />
<choose ParamRefId="{_SECOND}">
<when test="0"><ComObjectRefRef RefId="{_APP}_O-0_R-0" /><ComObjectRefRef RefId="{_APP}_O-1_R-0" /></when>
<when test="1"><ComObjectRefRef RefId="{_APP}_O-0_R-1" /><ComObjectRefRef RefId="{_APP}_O-1_R-1" /></when>
</choose></when></choose></ParameterBlock></ChannelIndependentBlock></Dynamic>"""
    before, rest = _XML.replace("</Static>", objects + "</Static>").split("<Dynamic>")
    _, after = rest.split("</Dynamic>")
    application = parse_application_xml((before + dynamic + after).encode(), "M-00FA")[
        0
    ]
    recovered = parameters._refine_pairs(
        application,
        {_SEGMENT: b"\x00\x00\x00"},
        {},
        None,
        set(),
        {0: "2 Bytes", 1: "2 Bytes"},
        parameters.RecoveredParameters({}, [], differing_objects=1),
        1,
    )
    assert recovered.complete
    assert recovered.values[_FIRST] == recovered.values[_SECOND] == "1"


@pytest.mark.parametrize(
    ("effort", "complete", "fixed", "after_fixed"),
    [
        (RecoveryEffort.MEDIUM, False, {"R-hidden": "1"}, True),
        (RecoveryEffort.FAST, False, {"R-hidden": "1"}, False),
        (RecoveryEffort.MEDIUM, True, {"R-hidden": "1"}, False),
        (RecoveryEffort.MEDIUM, False, {}, False),
    ],
)
def test_pairs_are_searched_again_with_the_fixed_values(
    application: Application,
    monkeypatch: pytest.MonkeyPatch,
    effort: RecoveryEffort,
    complete: bool,
    fixed: dict[str, str],
    after_fixed: bool,
) -> None:
    # The searches before the fixed values are restored can fit only by decoding the memory
    # into hidden settings the product fixes; with those kept, the selector of the device's
    # branch may need a setting it shows changed too, so the pairs are searched once more.
    end = parameters.RecoveredParameters(
        values={}, unknown=[], differing_bytes=0 if complete else 1
    )
    calls: list[object] = []

    def pairs(*args: object, fixed: object = None) -> parameters.RecoveredParameters:
        calls.append(fixed)
        return end

    monkeypatch.setattr(parameters, "_fixed_values", lambda _app, _ui: dict(fixed))
    monkeypatch.setattr(parameters, "_refine", lambda *args: end)
    monkeypatch.setattr(parameters, "_refine_fixed", lambda *args: end)
    monkeypatch.setattr(parameters, "_refine_pairs", pairs)
    recover_parameters(application, {_SEGMENT: bytes(3)}, effort=effort)
    # The first search of pairs decodes without the fixed values (fixed={}).
    assert (len(calls) == 2 and calls[1] == fixed) is after_fixed

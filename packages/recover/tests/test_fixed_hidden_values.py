"""Settings the device keeps no copy of are told apart by the hidden settings they come with.

Several values of such a setting can show the same linked group objects: the datapoint types
of a status value of the same size, or one function for a pair of buttons and one for each of
them. The application then gives a hidden setting the device stores a fixed value per choice (the
datapoint type, the kind of the function), and the value read from the device picks the choice
(issue #207: a status value of DPT 9.001 came back as 7.012, a two-button function as two
single-button ones).
"""

from __future__ import annotations

from typing import Any

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.errors import RecoverError
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _decode,
    _gates_of,
    _objects_by_number,
    _scope_of,
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0005-01-0000"
_STATUS = f"{_APP}_P-1_R-1"
_FUNCTION = f"{_APP}_P-3_R-3"
_PAIR = f"{_APP}_MD-1_M-1_MI-1_"
_SINGLE = f"{_APP}_MD-2_M-2_MI-1_"


def _object(object_id: str, number: int, size: str, base: str = "") -> str:
    base_number = f' BaseNumber="{base}"' if base else ""
    return (
        f'<ComObject Id="{object_id}" Name="O{number}" Text="Object {number}" Number="{number}"'
        f' FunctionText="" ObjectSize="{size}" ReadFlag="Disabled" WriteFlag="Enabled"'
        ' CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled"'
        f' ReadOnInitFlag="Disabled"{base_number} />'
    )


def _module(definition: int, number: int) -> str:
    return (
        f'<Module Id="{_APP}_MD-{definition}_M-{number}" RefId="{_APP}_MD-{definition}"'
        f' Name="Buttons {number}">'
        f'<NumericArg RefId="{_APP}_MD-{definition}_A-1" Value="2" />'
        f'<NumericArg RefId="{_APP}_MD-{definition}_A-2" Value="50" />'
        "</Module>"
    )


def _enumeration(type_id: str, values: dict[int, str]) -> str:
    return (
        f'<ParameterType Id="{_APP}_PT-{type_id}" Name="{type_id}">'
        '<TypeRestriction Base="Value" SizeInBit="8">'
        + "".join(
            f'<Enumeration Text="{text}" Value="{value}"'
            f' Id="{_APP}_PT-{type_id}_EN-{value}" />'
            for value, text in values.items()
        )
        + "</TypeRestriction></ParameterType>"
    )


_TYPES = (
    _enumeration("Status", {0: "not active", 4: "DPT 7.012", 6: "DPT 9.001"})
    + _enumeration("Function", {0: "not active", 1: "pair", 2: "single"})
    + _enumeration("Pair", {0: "not active", 6: "HSV", 8: "tunable white"})
    + _enumeration("Single", {0: "not active", 5: "colour"})
    + f"""<ParameterType Id="{_APP}_PT-N" Name="N">
        <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
      </ParameterType>"""
)

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="5" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Fixed hidden values"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>{_TYPES}</ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Status" ParameterType="{_APP}_PT-Status" Text="Status value" Value="0" />
              <Parameter Id="{_APP}_P-2" Name="StatusType" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-3" Name="Function" ParameterType="{_APP}_PT-Function" Text="Buttons" Value="0" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_STATUS}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_APP}_P-2_R-21" RefId="{_APP}_P-2" Value="7" />
              <ParameterRef Id="{_APP}_P-2_R-22" RefId="{_APP}_P-2" Value="9" />
              <ParameterRef Id="{_FUNCTION}" RefId="{_APP}_P-3" />
            </ParameterRefs>
            <ComObjectTable>
              {_object(f"{_APP}_O-40", 40, "2 Bytes")}
            </ComObjectTable>
            <ComObjectRefs>
              <ComObjectRef Id="{_APP}_O-40_R-41" RefId="{_APP}_O-40" DatapointType="DPST-7-12" />
              <ComObjectRef Id="{_APP}_O-40_R-42" RefId="{_APP}_O-40" DatapointType="DPST-9-1" />
            </ComObjectRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Pair">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Offset" />
                <Argument Id="{_APP}_MD-1_A-2" Name="ObjectBase" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-1" Name="Kind" ParameterType="{_APP}_PT-Pair" Text="Two-button function" Value="0" />
                  <Union SizeInBit="8">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" />
                    <Parameter Id="{_APP}_MD-1_UP-2" Name="KindType" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0" Offset="0" BitOffset="0" />
                    <Parameter Id="{_APP}_MD-1_UP-3" Name="Duration" ParameterType="{_APP}_PT-N" Text="Duration" Value="0" Offset="0" BitOffset="0" />
                  </Union>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" />
                  <ParameterRef Id="{_APP}_MD-1_UP-2_R-2" RefId="{_APP}_MD-1_UP-2" Value="19" />
                  <ParameterRef Id="{_APP}_MD-1_UP-2_R-3" RefId="{_APP}_MD-1_UP-2" Value="18" />
                  <ParameterRef Id="{_APP}_MD-1_UP-3_R-4" RefId="{_APP}_MD-1_UP-3" />
                </ParameterRefs>
                <ComObjects>
                  {_object(f"{_APP}_MD-1_O-2-1", 1, "1 Byte", f"{_APP}_MD-1_A-2")}
                </ComObjects>
                <ComObjectRefs>
                  <ComObjectRef Id="{_APP}_MD-1_O-2-1_R-1" RefId="{_APP}_MD-1_O-2-1" />
                </ComObjectRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Pair">
                  <ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
                  <choose ParamRefId="{_APP}_MD-1_P-1_R-1">
                    <when test="0"><ParameterRefRef RefId="{_APP}_MD-1_UP-3_R-4" /></when>
                    <when test="6">
                      <ComObjectRefRef RefId="{_APP}_MD-1_O-2-1_R-1" />
                      <ParameterRefRef RefId="{_APP}_MD-1_UP-2_R-2" />
                    </when>
                    <when test="8">
                      <ComObjectRefRef RefId="{_APP}_MD-1_O-2-1_R-1" />
                      <ParameterRefRef RefId="{_APP}_MD-1_UP-2_R-3" />
                    </when>
                  </choose>
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
            <ModuleDef Id="{_APP}_MD-2" Name="Single">
              <Arguments>
                <Argument Id="{_APP}_MD-2_A-1" Name="Offset" />
                <Argument Id="{_APP}_MD-2_A-2" Name="ObjectBase" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-2_P-1" Name="Kind" ParameterType="{_APP}_PT-Single" Text="Single-button function" Value="0" />
                  <Parameter Id="{_APP}_MD-2_P-2" Name="KindType" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" BaseOffset="{_APP}_MD-2_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-2_P-1_R-1" RefId="{_APP}_MD-2_P-1" />
                  <ParameterRef Id="{_APP}_MD-2_P-2_R-2" RefId="{_APP}_MD-2_P-2" Value="29" />
                </ParameterRefs>
                <ComObjects>
                  {_object(f"{_APP}_MD-2_O-2-1", 1, "1 Byte", f"{_APP}_MD-2_A-2")}
                </ComObjects>
                <ComObjectRefs>
                  <ComObjectRef Id="{_APP}_MD-2_O-2-1_R-1" RefId="{_APP}_MD-2_O-2-1" />
                </ComObjectRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-2_PB-1" Text="Single">
                  <ParameterRefRef RefId="{_APP}_MD-2_P-1_R-1" />
                  <choose ParamRefId="{_APP}_MD-2_P-1_R-1">
                    <when test="5">
                      <ComObjectRefRef RefId="{_APP}_MD-2_O-2-1_R-1" />
                      <ParameterRefRef RefId="{_APP}_MD-2_P-2_R-2" />
                    </when>
                  </choose>
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_STATUS}" />
                <choose ParamRefId="{_STATUS}">
                  <when test="4">
                    <ComObjectRefRef RefId="{_APP}_O-40_R-41" />
                    <ParameterRefRef RefId="{_APP}_P-2_R-21" />
                  </when>
                  <when test="6">
                    <ComObjectRefRef RefId="{_APP}_O-40_R-42" />
                    <ParameterRefRef RefId="{_APP}_P-2_R-22" />
                  </when>
                </choose>
                <ParameterRefRef RefId="{_FUNCTION}" />
              </ParameterBlock>
            </ChannelIndependentBlock>
            <choose ParamRefId="{_FUNCTION}">
              <when test="1">{_module(1, 1)}</when>
              <when test="2">{_module(2, 2)}</when>
            </choose>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, *values: tuple[str, str]) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id, value), set in order."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values:
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


@pytest.mark.parametrize("datapoint_type", ["4", "6"])
def test_the_stored_type_picks_the_status_value(
    application: Application, datapoint_type: str
) -> None:
    # Either type shows object 40 in two bytes; the device keeps 7 or 9 for it.
    segments = _device(application, (_STATUS, datapoint_type))
    recovered = recover_parameters(
        application, segments, linked_objects={40}, object_sizes={40: "2 Bytes"}
    )
    assert recovered.values[_STATUS] == datapoint_type


@pytest.mark.parametrize("linked", [False, True])
def test_a_fixed_marker_corrects_an_already_active_wrong_branch(linked: bool) -> None:
    xml = _XML.replace('Text="Status value" Value="0"', 'Text="Status value" Value="4"')
    application = parse_application_xml(xml.encode(), "M-00FA")[0]
    segments = _device(application, (_STATUS, "6"))
    recovered = recover_parameters(
        application, segments, linked_objects={40} if linked else ()
    )
    assert recovered.complete
    assert recovered.values[_STATUS] == "6"
    ui = seed_dynamic_ui(application, recovered.values)
    assert ui is not None
    assert ui.get_value(f"{_APP}_P-2_R-21") == "7"
    assert ui.get_value(f"{_APP}_P-2_R-22") == "9"


def test_a_wrong_fixed_marker_is_not_made_to_match_by_decoding(
    application: Application,
) -> None:
    segments = _device(application, (_STATUS, "6"))
    values, _ = _decode(application, segments, {}, None, {_STATUS: "4"})
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    assert ui.encode_to_memory()[f"{_APP}_AS-0000"][0] == 7


def test_a_module_fixed_marker_keeps_its_branch_value(application: Application) -> None:
    segments = _device(application, (_FUNCTION, "1"), (f"{_PAIR}P-1_R-1", "8"))
    values, _ = _decode(
        application, segments, {}, None, {_FUNCTION: "1", f"{_PAIR}P-1_R-1": "6"}
    )
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    assert ui.encode_to_memory()[f"{_APP}_AS-0000"][2] == 19
    assert ui.get_value(f"{_PAIR}UP-2_R-2") == "19"
    assert ui.get_value(f"{_PAIR}UP-2_R-3") == "18"


@pytest.mark.parametrize("property_backed", [False, True])
def test_fixed_references_are_not_seeded_from_observed_or_inferred_values(
    property_backed: bool,
) -> None:
    xml = _XML
    if property_backed:
        xml = xml.replace(
            f'<Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />',
            '<Property ObjectIndex="4" PropertyId="80" Offset="0" BitOffset="0" />',
        )
    application = parse_application_xml(xml.encode(), "M-00FA")[0]
    marker = f"{_APP}_P-2_R-21"
    values, _ = _decode(
        application,
        {f"{_APP}_AS-0000": b"\x09\x00\x00\x00"},
        {(4, None, 80, 0): b"\x09"} if property_backed else {},
        None,
        {_STATUS: "4", marker: "9"},
    )
    assert marker not in values
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    assert ui.get_value(marker) == "7"


@pytest.mark.parametrize("property_backed", [False, True])
def test_an_editable_reference_to_a_hidden_parameter_is_decoded(
    property_backed: bool,
) -> None:
    xml = _XML.replace('Value="7" />', 'Value="7" Access="ReadWrite" />')
    if property_backed:
        xml = xml.replace(
            f'<Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />',
            '<Property ObjectIndex="4" PropertyId="80" Offset="0" BitOffset="0" />',
        )
    application = parse_application_xml(xml.encode(), "M-00FA")[0]
    values, _ = _decode(
        application,
        {f"{_APP}_AS-0000": b"\x2a\x00\x00\x00"},
        {(4, None, 80, 0): b"\x2a"} if property_backed else {},
        None,
        {_STATUS: "4"},
    )
    assert values[f"{_APP}_P-2_R-21"] == "42"
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    assert ui.get_value(f"{_APP}_P-2_R-22") == "9"


def test_custom_value_is_recovered_instead_of_overwriting_a_fixed_preset() -> None:
    xml = _XML.replace('Value="7" />', 'Value="7" Access="ReadWrite" />').replace(
        'Text="Status value" Value="0"', 'Text="Status value" Value="6"'
    )
    application = parse_application_xml(xml.encode(), "M-00FA")[0]
    custom = f"{_APP}_P-2_R-21"
    preset = f"{_APP}_P-2_R-22"
    segments = _device(application, (_STATUS, "4"), (custom, "42"))
    recovered = recover_parameters(application, segments, linked_objects={40})
    ui = seed_dynamic_ui(application, recovered.values)
    assert ui is not None
    assert recovered.complete
    assert ui.get_value(_STATUS) == "4"
    assert ui.get_value(custom) == "42"
    assert ui.get_value(preset) == "9"


def test_a_hidden_assignment_target_is_decoded() -> None:
    marker = f"{_APP}_P-2_R-21"
    xml = _XML.replace(
        '<when test="4">',
        f'<when test="4"><Assign TargetParamRefRef="{marker}" Value="11" />',
    )
    application = parse_application_xml(xml.encode(), "M-00FA")[0]
    segments = _device(application, (_STATUS, "4"))
    assert segments[f"{_APP}_AS-0000"][0] == 11
    values, _ = _decode(application, segments, {}, None, {_STATUS: "4"})
    assert values[marker] == "11"


def test_a_hidden_calculated_value_recovers_its_editable_input() -> None:
    marker = f"{_APP}_P-2_R-21"
    calculation = f"""<ParameterCalculations>
<ParameterCalculation Id="{_APP}_PC-1" Name="Status" Language="JavaScript">
<LRTransformation>stored = choice * 2;</LRTransformation>
<RLTransformation>choice = stored / 2;</RLTransformation>
<LParameters><ParameterRefRef RefId="{_STATUS}" AliasName="choice" /></LParameters>
<RParameters><ParameterRefRef RefId="{marker}" AliasName="stored" /></RParameters>
</ParameterCalculation></ParameterCalculations>"""
    xml = _XML.replace("</Static>", calculation + "</Static>", 1).replace(
        'Text="Status value" Value="0"', 'Text="Status value" Value="4"'
    )
    application = parse_application_xml(xml.encode(), "M-00FA")[0]
    values, _ = _decode(
        application, {f"{_APP}_AS-0000": b"\x0c\x00\x00\x00"}, {}, None, {}
    )
    assert values[marker] == "12"
    assert values[_STATUS] == "6"


@pytest.mark.parametrize("stop", ["cancel", "budget"])
def test_interrupted_fixed_validation_keeps_the_marker_conflict(
    application: Application, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    marker = f"{_APP}_P-2_R-21"
    candidate = parameters.RecoveredParameters({_STATUS: "4", marker: "9"}, [])
    ui = application.dynamic_ui()
    assert ui is not None
    if stop == "budget":
        monkeypatch.setattr(parameters, "_PAIR_TRIES", 0)
    result = parameters._refine_fixed(
        application,
        _device(application, (_STATUS, "6")),
        {},
        None,
        {40},
        {},
        candidate,
        parameters._fixed_values(application, ui),
        1,
        (lambda: True) if stop == "cancel" else None,
    )
    assert marker not in result.values
    assert result.values[_STATUS] == "4"
    assert result.differing_bytes == 1
    assert not result.complete
    assert result.limited


def test_a_cancelled_fixed_validation_decodes_nothing(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Once cancelled the validation does not decode the memory again (with the calculations
    # of a large application that takes as long as a step of the search): the result so far
    # with the marker at its own value, measured.
    marker = f"{_APP}_P-2_R-21"
    ui = application.dynamic_ui()
    assert ui is not None

    def no_decode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("decoded after the cancel")

    monkeypatch.setattr(parameters, "_decode", no_decode)
    result = parameters._refine_fixed(
        application,
        _device(application, (_STATUS, "4")),
        {},
        None,
        {40},
        {},
        parameters.RecoveredParameters({_STATUS: "4", marker: "9"}, []),
        parameters._fixed_values(application, ui),
        1,
        lambda: True,
    )
    assert result.values == {_STATUS: "4"}
    assert result.complete


def test_a_fixed_validation_cancelled_while_decoding_ends_with_the_result_so_far(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cancel given while the validation decodes is heard there too.
    marker = f"{_APP}_P-2_R-21"
    ui = application.dynamic_ui()
    assert ui is not None
    decode = parameters._decode  # pyright: ignore[reportPrivateUsage]
    cancelled: list[bool] = []
    finished: list[bool] = []

    def cancelling(*args: Any, **kwargs: Any) -> Any:
        cancelled.append(True)
        result = decode(*args, **kwargs)
        finished.append(True)
        return result

    monkeypatch.setattr(parameters, "_decode", cancelling)
    result = parameters._refine_fixed(
        application,
        _device(application, (_STATUS, "4")),
        {},
        None,
        {40},
        {},
        parameters.RecoveredParameters({_STATUS: "4", marker: "9"}, []),
        parameters._fixed_values(application, ui),
        1,
        lambda: bool(cancelled),
    )
    assert cancelled == [True]
    assert not finished
    assert result.values == {_STATUS: "4"}
    assert result.complete


def test_fixed_validation_can_correct_several_independent_markers(
    application: Application,
) -> None:
    candidate = parameters.RecoveredParameters(
        {
            _STATUS: "4",
            f"{_APP}_P-2_R-21": "9",
            _FUNCTION: "1",
            f"{_PAIR}P-1_R-1": "6",
            f"{_PAIR}UP-2_R-2": "18",
        },
        [],
    )
    ui = application.dynamic_ui()
    assert ui is not None
    result = parameters._refine_fixed(
        application,
        _device(
            application, (_STATUS, "6"), (_FUNCTION, "1"), (f"{_PAIR}P-1_R-1", "8")
        ),
        {},
        None,
        {40, 51},
        {},
        candidate,
        parameters._fixed_values(application, ui),
        1,
        None,
    )
    assert result.complete
    assert result.values[_STATUS] == "6"
    assert result.values[f"{_PAIR}P-1_R-1"] == "8"


def test_failed_fixed_validation_cannot_report_a_complete_candidate(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    monkeypatch.setattr(parameters, "_misfit", lambda *_args: None)
    with pytest.raises(RecoverError, match="Cannot validate recovered parameters"):
        parameters._refine_fixed(
            application,
            _device(application, (_STATUS, "6")),
            {},
            None,
            {40},
            {},
            parameters.RecoveredParameters({_STATUS: "4"}, []),
            parameters._fixed_values(application, ui),
            1,
            None,
        )


@pytest.mark.parametrize("kind", ["6", "8"])
def test_the_stored_kind_picks_the_function_of_a_pair(
    application: Application, kind: str
) -> None:
    # Both kinds show object 51; a member of a union in the module instance tells them.
    segments = _device(application, (_FUNCTION, "1"), (f"{_PAIR}P-1_R-1", kind))
    recovered = recover_parameters(application, segments, linked_objects={51})
    assert recovered.values[_FUNCTION] == "1"
    assert recovered.values[f"{_PAIR}P-1_R-1"] == kind


def test_the_stored_kind_tells_single_buttons_from_a_pair(
    application: Application,
) -> None:
    # A pair (tried first) shows object 51 as well, but would hold 18 or 19 where single
    # buttons hold 29.
    segments = _device(application, (_FUNCTION, "2"), (f"{_SINGLE}P-1_R-1", "5"))
    recovered = recover_parameters(application, segments, linked_objects={51})
    assert recovered.values[_FUNCTION] == "2"
    assert recovered.values[f"{_SINGLE}P-1_R-1"] == "5"


@pytest.fixture(scope="module")
def singles_first() -> Application:
    """The application with single buttons tried before a pair, and a single-button function
    that shows the object without a hidden setting of its own (as the BE-GT2Tx.01's)."""
    pair = f'<Enumeration Text="pair" Value="1" Id="{_APP}_PT-Function_EN-1" />'
    single = f'<Enumeration Text="single" Value="2" Id="{_APP}_PT-Function_EN-2" />'
    plain = f'<Enumeration Text="plain" Value="7" Id="{_APP}_PT-Single_EN-7" />'
    colour = f'<Enumeration Text="colour" Value="5" Id="{_APP}_PT-Single_EN-5" />'
    shows = f'<when test="7"><ComObjectRefRef RefId="{_APP}_MD-2_O-2-1_R-1" /></when>'
    xml = (
        _XML.replace(pair + single, single + pair)
        .replace(colour, plain + colour)
        .replace(
            "</choose>\n                </ParameterBlock>\n              </Dynamic>\n            </ModuleDef>\n          </ModuleDefs>",
            f"{shows}</choose>\n                </ParameterBlock>\n              </Dynamic>\n            </ModuleDef>\n          </ModuleDefs>",
        )
    )
    assert xml.count(shows) == 1 and single + pair in xml
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def test_the_hidden_kind_of_a_pair_wins_over_single_buttons_without_one(
    singles_first: Application,
) -> None:
    # Single buttons (tried first) show object 51 too, and write no hidden kind: they leave
    # the byte where the device holds the pair's kind at its initial value, which counts
    # against them.
    segments = _device(singles_first, (_FUNCTION, "1"), (f"{_PAIR}P-1_R-1", "8"))
    recovered = recover_parameters(singles_first, segments, linked_objects={51})
    assert recovered.values[_FUNCTION] == "1"
    assert recovered.values[f"{_PAIR}P-1_R-1"] == "8"


def test_the_layout_names_every_setting_stored_at_a_byte(
    application: Application,
) -> None:
    # Active or not: the status type's references at byte 0, and with a pair of buttons the
    # members of its union at byte 2 (the instance's offset), qualified with the instance.
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_FUNCTION, "1")
    cells = ui.stored_parameter_cells()
    segment = f"{_APP}_AS-0000"
    assert {f"{_APP}_P-2_R-21", f"{_APP}_P-2_R-22"} <= cells[(segment, 0)]
    assert {
        f"{_PAIR}UP-2_R-2",
        f"{_PAIR}UP-2_R-3",
        f"{_PAIR}UP-3_R-4",
    } <= cells[(segment, 2)]


def test_a_module_instance_object_is_found_by_its_number(
    application: Application,
) -> None:
    # The pair's object is number 1 of its definition, numbered from the instance's base (50).
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_FUNCTION, "1")
    objects = _objects_by_number(ui)
    assert objects[51] == [f"{_PAIR}O-2-1_R-1"]
    assert set(objects[40]) == {f"{_APP}_O-40_R-41", f"{_APP}_O-40_R-42"}
    # The definitions' own numbers belong to no object of the application.
    assert 1 not in objects


def test_the_selectors_of_a_module_instance_are_qualified_with_it(
    application: Application,
) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_FUNCTION, "1")
    # The pair's object shows behind the instance's own kind.
    assert _gates_of(ui, f"{_PAIR}O-2-1_R-1", ui.object_gates) == {f"{_PAIR}P-1_R-1"}
    # The kind shows wherever the instance is: behind the selector of its module.
    assert ui.module_gates(f"{_APP}_MD-1_M-1") == {_FUNCTION}
    assert _gates_of(ui, f"{_PAIR}P-1_R-1") == {_FUNCTION}
    assert _scope_of(f"{_PAIR}P-1_R-1") == f"{_APP}_MD-1_M-1_MI-1"
    assert _scope_of(_FUNCTION) == ""


def test_a_result_that_fits_the_device_is_complete(application: Application) -> None:
    segments = _device(application, (_FUNCTION, "1"), (f"{_PAIR}P-1_R-1", "8"))
    # A byte no setting is stored at holds data of the device's own (runtime): not counted.
    segment = f"{_APP}_AS-0000"
    segments[segment] = segments[segment][:3] + b"\xff"
    recovered = recover_parameters(application, segments, linked_objects={51})
    assert recovered.complete
    assert (recovered.differing_bytes, recovered.differing_objects) == (0, 0)
    assert not recovered.limited


def test_a_result_that_cannot_show_a_linked_object_is_incomplete(
    application: Application,
) -> None:
    # No configuration shows object 99: the result says how far it is off.
    segments = _device(application, (_FUNCTION, "1"), (f"{_PAIR}P-1_R-1", "8"))
    recovered = recover_parameters(application, segments, linked_objects={51, 99})
    assert not recovered.complete
    assert recovered.differing_objects == 1


def test_a_shown_object_the_device_does_not_link_is_no_misfit(
    application: Application,
) -> None:
    # The status value shows object 40 too, but only the pair's object is linked: a device
    # shows more objects than it links.
    segments = _device(
        application, (_STATUS, "4"), (_FUNCTION, "1"), (f"{_PAIR}P-1_R-1", "8")
    )
    recovered = recover_parameters(application, segments, linked_objects={51})
    assert recovered.differing_objects == 0
    assert recovered.complete


_SHARED_APP = "M-00FA_A-0C02-01-0000"
_SHARED_STATUS = f"{_SHARED_APP}_P-1_R-1"
_SHARED_VALUE = f"{_SHARED_APP}_P-3_R-30"
_SHARED_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_SHARED_APP}" ApplicationNumber="194" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Shared byte"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SHARED_APP}_AS-0000" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_SHARED_APP}_PT-Status" Name="Status">
                <TypeRestriction Base="Value" SizeInBit="2">
                  <Enumeration Text="on request" Value="1" Id="{_SHARED_APP}_PT-Status_EN-1" />
                  <Enumeration Text="on change" Value="2" Id="{_SHARED_APP}_PT-Status_EN-2" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_SHARED_APP}_PT-Six" Name="Six">
                <TypeNumber SizeInBit="6" Type="unsignedInt" minInclusive="0" maxInclusive="63" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_SHARED_APP}_P-1" Name="Status" ParameterType="{_SHARED_APP}_PT-Status" Text="Status" Value="2" />
              <Parameter Id="{_SHARED_APP}_P-2" Name="StatusA" ParameterType="{_SHARED_APP}_PT-Status" Text="" Access="None" Value="2">
                <Memory CodeSegment="{_SHARED_APP}_AS-0000" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_SHARED_APP}_P-3" Name="Value" ParameterType="{_SHARED_APP}_PT-Six" Text="Value" Value="0">
                <Memory CodeSegment="{_SHARED_APP}_AS-0000" Offset="0" BitOffset="2" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_SHARED_STATUS}" RefId="{_SHARED_APP}_P-1" />
              <ParameterRef Id="{_SHARED_APP}_P-2_R-21" RefId="{_SHARED_APP}_P-2" />
              <ParameterRef Id="{_SHARED_APP}_P-2_R-22" RefId="{_SHARED_APP}_P-2" Value="1" />
              <ParameterRef Id="{_SHARED_VALUE}" RefId="{_SHARED_APP}_P-3" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_SHARED_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_SHARED_STATUS}" />
                <choose ParamRefId="{_SHARED_STATUS}">
                  <when test="2"><ParameterRefRef RefId="{_SHARED_APP}_P-2_R-21" /></when>
                  <when test="1"><ParameterRefRef RefId="{_SHARED_APP}_P-2_R-22" /></when>
                </choose>
                <ParameterRefRef RefId="{_SHARED_VALUE}" />
              </ParameterBlock>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.mark.parametrize("effort", [RecoveryEffort.FAST, RecoveryEffort.MEDIUM])
def test_a_fixed_marker_sharing_its_byte_with_another_setting_picks_the_branch(
    effort: RecoveryEffort,
) -> None:
    # The hidden status of the chosen branch takes the top two bits of a byte whose other six
    # bits hold a shown value: the bits still tell which branch the device was given.
    application = parse_application_xml(_SHARED_XML.encode(), "M-00FA")[0]
    segments = _device(application, (_SHARED_STATUS, "1"), (_SHARED_VALUE, "5"))
    assert segments == {f"{_SHARED_APP}_AS-0000": bytes([0b01000101])}
    recovered = recover_parameters(application, segments, effort=effort)
    assert recovered.complete
    assert recovered.values[_SHARED_STATUS] == "1"
    assert recovered.values[_SHARED_VALUE] == "5"


def test_the_bytes_of_a_fixed_marker_include_those_it_shares() -> None:
    application = parse_application_xml(_SHARED_XML.encode(), "M-00FA")[0]
    ui = application.dynamic_ui()
    assert ui is not None
    fixed = parameters._fixed_values(application, ui)  # pyright: ignore[reportPrivateUsage]
    assert fixed == {
        f"{_SHARED_APP}_P-2_R-21": "2",
        f"{_SHARED_APP}_P-2_R-22": "1",
    }
    cells = parameters._fixed_cells(ui, fixed)  # pyright: ignore[reportPrivateUsage]
    assert cells == {(f"{_SHARED_APP}_AS-0000", 0)}
    # Nothing fixed is active: no bytes.
    assert not parameters._fixed_cells(ui, {})  # pyright: ignore[reportPrivateUsage]

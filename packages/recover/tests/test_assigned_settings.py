"""A setting the device keeps no copy of is read from the stored settings an Assign copies it into.

"Shown" has no memory: the application shows it and copies it into "First", "Second" and
"Third", which the device stores. "Second" gets it only with "Mode" 0; with "Mode" 1 it gets a
fixed value instead, so only the Assigns in effect count, and where the stored copies disagree
the value most of them hold is taken.
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F2-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_SHOWN = f"{_APP}_P-1_R-1"
_MODE = f"{_APP}_P-2_R-2"
_FIRST = f"{_APP}_P-3_R-3"
_SECOND = f"{_APP}_P-4_R-4"
_THIRD = f"{_APP}_P-5_R-5"


def _stored(number: int, name: str, offset: int) -> str:
    return (
        f'<Parameter Id="{_APP}_P-{number}" Name="{name}" ParameterType="{_APP}_PT-N"'
        f' Text="{name}" Value="0" Access="None">'
        f'<Memory CodeSegment="{_SEGMENT}" Offset="{offset}" BitOffset="0" /></Parameter>'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="242" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assigned settings"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Shown" ParameterType="{_APP}_PT-N" Text="Shown" Value="5" />
          <Parameter Id="{_APP}_P-2" Name="Mode" ParameterType="{_APP}_PT-B" Text="Mode" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" />
          </Parameter>
          {_stored(3, "First", 0)}
          {_stored(4, "Second", 1)}
          {_stored(5, "Third", 3)}
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_SHOWN}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_MODE}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_FIRST}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_SECOND}" RefId="{_APP}_P-4" />
          <ParameterRef Id="{_THIRD}" RefId="{_APP}_P-5" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_SHOWN}" />
            <ParameterRefRef RefId="{_MODE}" />
            <ParameterRefRef RefId="{_FIRST}" />
            <ParameterRefRef RefId="{_SECOND}" />
            <ParameterRefRef RefId="{_THIRD}" />
            <Assign TargetParamRefRef="{_FIRST}" SourceParamRefRef="{_SHOWN}" />
            <choose ParamRefId="{_MODE}">
              <when test="0"><Assign TargetParamRefRef="{_SECOND}" SourceParamRefRef="{_SHOWN}" /></when>
              <when test="1"><Assign TargetParamRefRef="{_SECOND}" Value="9" /></when>
            </choose>
            <Assign TargetParamRefRef="{_THIRD}" SourceParamRefRef="{_SHOWN}" />
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, **values: str) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id -> value)."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values.items():
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


def test_the_assigns_in_effect_are_recorded() -> None:
    ui = _application().dynamic_ui()
    assert ui is not None
    assert ui.assigned_from() == {_FIRST: _SHOWN, _SECOND: _SHOWN, _THIRD: _SHOWN}
    ui.set_parameter_ref(_MODE, "1")
    assert ui.assigned_from() == {_FIRST: _SHOWN, _THIRD: _SHOWN}


def test_an_unstored_setting_is_read_from_its_stored_copies() -> None:
    application = _application()
    segments = _device(application, **{_SHOWN: "7"})
    assert segments[_SEGMENT] == b"\x07\x07\x00\x07"
    recovered = recover_parameters(application, segments)
    assert recovered.values[_SHOWN] == "7"


def test_a_target_assigned_a_fixed_value_does_not_count() -> None:
    application = _application()
    segments = _device(application, **{_SHOWN: "7", _MODE: "1"})
    assert segments[_SEGMENT] == b"\x07\x09\x01\x07"
    recovered = recover_parameters(application, segments)
    assert recovered.values[_SHOWN] == "7"
    assert recovered.values[_MODE] == "1"


def test_most_stored_copies_decide() -> None:
    # A device whose copies disagree (one of them written by another version of the
    # application): two of three hold 7.
    recovered = recover_parameters(_application(), {_SEGMENT: b"\x07\x08\x00\x07"})
    assert recovered.values[_SHOWN] == "7"


def test_a_tie_infers_nothing() -> None:
    recovered = recover_parameters(_application(), {_SEGMENT: b"\x07\x09\x01\x08"})
    assert recovered.values.get(_SHOWN, "5") == "5"


def _overwritten_application() -> Application:
    """The product with "First" and "Second" copied from "Shown" and then given the fixed
    value 9 by a later Assign: only "Third" holds a copy."""
    xml = _XML.replace(
        f'<Assign TargetParamRefRef="{_FIRST}" SourceParamRefRef="{_SHOWN}" />',
        f'<Assign TargetParamRefRef="{_FIRST}" SourceParamRefRef="{_SHOWN}" />'
        f'<Assign TargetParamRefRef="{_FIRST}" Value="9" />'
        f'<Assign TargetParamRefRef="{_SECOND}" SourceParamRefRef="{_SHOWN}" />'
        f'<Assign TargetParamRefRef="{_SECOND}" Value="9" />',
    )
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def test_a_copy_written_over_by_a_fixed_value_is_not_recorded() -> None:
    ui = _overwritten_application().dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_MODE, "1")
    assert ui.assigned_from() == {_THIRD: _SHOWN}


def test_a_copy_written_over_by_a_fixed_value_does_not_count() -> None:
    application = _overwritten_application()
    # Calculated from its values anew, every Assign writes in turn: the fixed values come last.
    # (Edited one value after the other, a change of "Shown" writes only its copies.)
    program, tree_builder = application.resolved()
    ui = DynamicUI(
        program,
        tree_builder=tree_builder,
        parameter_values={_SHOWN: "7", _MODE: "1"},
    )
    segments = ui.encode_to_memory()
    assert segments[_SEGMENT] == b"\x09\x09\x01\x07"
    assert _device(application, **{_SHOWN: "7", _MODE: "1"})[_SEGMENT] == (
        b"\x07\x09\x01\x07"
    )
    recovered = recover_parameters(application, segments)
    assert recovered.values[_SHOWN] == "7"

"""Reading a device back: a module instance's union member shown behind another setting of the
instance is decoded once that setting is, and a read-only setting the device keeps no copy of
keeps the value the product gives it."""

from __future__ import annotations

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F2-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_BUTTONS = f"{_APP}_P-1_R-1"
_EXTRA = f"{_APP}_P-2_R-2"
# The instance's refs.
_I = f"{_MD}_M-1_MI-1_"
_SWITCH = f"{_I}P-1_R-1"
_FIRST = f"{_I}UP-2_R-2"
_SECOND = f"{_I}UP-3_R-3"
_J = f"{_MD}_M-2_MI-1_"
_SWITCH_2 = f"{_J}P-1_R-1"
_FIRST_2 = f"{_J}UP-2_R-2"
_SECOND_2 = f"{_J}UP-3_R-3"


def _object(number: int) -> str:
    return (
        f'<ComObject Id="{_APP}_O-{number}" Name="O{number}" Text="Object {number}"'
        f' Number="{number}" FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled"'
        ' WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled"'
        ' UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" />'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="242" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Module union rounds"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="6"><Data>AAAAAAAA</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Buttons" ParameterType="{_APP}_PT-B" Text="Buttons" Access="Read" Value="1" />
          <Parameter Id="{_APP}_P-2" Name="Extra" ParameterType="{_APP}_PT-B" Text="Extra" Value="0" />
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_BUTTONS}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_EXTRA}" RefId="{_APP}_P-2" />
        </ParameterRefs>
        <ComObjectTable>{_object(1)}{_object(2)}</ComObjectTable>
        <ComObjectRefs>
          <ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" />
          <ComObjectRef Id="{_APP}_O-2_R-2" RefId="{_APP}_O-2" />
        </ComObjectRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments><Argument Id="{_MD}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Switch" ParameterType="{_APP}_PT-N" Text="Switch" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Union SizeInBit="16">
                <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" BaseOffset="{_MD}_A-1" />
                <Parameter Id="{_MD}_UP-2" Name="First" ParameterType="{_APP}_PT-N" Offset="0" BitOffset="0" Text="First" Value="0" DefaultUnionParameter="true" />
                <Parameter Id="{_MD}_UP-3" Name="Second" ParameterType="{_APP}_PT-N" Offset="1" BitOffset="0" Text="Second" Value="0" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
              <ParameterRef Id="{_MD}_UP-2_R-2" RefId="{_MD}_UP-2" />
              <ParameterRef Id="{_MD}_UP-3_R-3" RefId="{_MD}_UP-3" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <ParameterRefRef RefId="{_MD}_UP-2_R-2" />
              <choose ParamRefId="{_MD}_P-1_R-1">
                <when test="1"><ParameterRefRef RefId="{_MD}_UP-3_R-3" /></when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_BUTTONS}" />
            <choose ParamRefId="{_BUTTONS}">
              <when test="1"><ComObjectRefRef RefId="{_APP}_O-1_R-1" /></when>
              <when test="0"><ComObjectRefRef RefId="{_APP}_O-2_R-2" /></when>
            </choose>
            <ParameterRefRef RefId="{_EXTRA}" />
            <choose ParamRefId="{_EXTRA}">
              <when test="1"><ComObjectRefRef RefId="{_APP}_O-2_R-2" /></when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
        <Module Id="{_MD}_M-1" RefId="{_MD}" Name="Channel 1">
          <NumericArg RefId="{_MD}_A-1" Value="0" />
        </Module>
        <Module Id="{_MD}_M-2" RefId="{_MD}" Name="Channel 2">
          <NumericArg RefId="{_MD}_A-1" Value="3" />
        </Module>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, **values: str) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id -> value)."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values.items():
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


def test_a_member_shown_behind_a_setting_of_its_instance_is_decoded(
    application: Application,
) -> None:
    # The switch of the first channel shows its second member, that of the second channel
    # does not: a decode seeded with one value for both switches misses the member.
    segments = _device(
        application, **{_SWITCH: "1", _FIRST: "5", _SECOND: "7", _FIRST_2: "4"}
    )
    assert segments[_SEGMENT] == b"\x01\x05\x07\x00\x04\x00"

    values, unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}
    )

    assert (values[_SWITCH], values[_FIRST], values[_SECOND]) == ("1", "5", "7")
    assert (values[_SWITCH_2], values[_FIRST_2]) == ("0", "4")
    assert _SECOND_2 not in values
    assert not unknown


def test_a_member_shown_behind_a_setting_of_its_instance_is_recovered(
    application: Application,
) -> None:
    segments = _device(
        application, **{_SWITCH: "1", _FIRST: "5", _SECOND: "7", _FIRST_2: "4"}
    )

    recovered = recover_parameters(application, segments, linked_objects={1})

    assert recovered.values[_SECOND] == "7"
    assert recovered.values[_FIRST_2] == "4"
    assert recovered.complete


def test_the_member_stays_hidden_while_its_setting_is_off(
    application: Application,
) -> None:
    segments = _device(application, **{_FIRST: "5"})

    values, _unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}
    )

    assert (values[_SWITCH], values[_FIRST]) == ("0", "5")
    assert _SECOND not in values


def test_a_read_only_setting_without_a_copy_keeps_its_value(
    application: Application,
) -> None:
    # Object 2 is linked. Switching the read-only "Buttons" off would show it and hide the
    # unlinked object 1, but nobody can switch it off: "Extra" shows it.
    segments = _device(application, **{_EXTRA: "1"})

    recovered = recover_parameters(application, segments, linked_objects={2})

    assert recovered.values.get(_BUTTONS, "1") == "1"
    assert recovered.values[_EXTRA] == "1"

"""Two settings stored in one byte, each behind a selector of its own the device keeps no copy
of, both behind a feedback switch the device keeps no copy of either.

"Feedback" shows the two feedback selectors, "Position" and "Movement", and a group object of
its own. Each selector shows a delay, a bit of the same byte, and a group object. None of the
objects is linked: "Feedback" alone, and either selector alone, fits worse than the defaults
(the byte still differs, and more objects show that are not linked). Only with both selectors
the byte the device holds is written.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0D08-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_FEEDBACK = f"{_APP}_P-1_R-1"
_POSITION = f"{_APP}_P-2_R-2"
_MOVEMENT = f"{_APP}_P-3_R-3"
_POSITION_DELAY = f"{_APP}_P-4_R-4"
_MOVEMENT_DELAY = f"{_APP}_P-5_R-5"


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
    <ApplicationProgram Id="{_APP}" ApplicationNumber="14" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Shared byte"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
          </ParameterType>
          <ParameterType Id="{_APP}_PT-F" Name="F">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="off" Value="0" Id="{_APP}_PT-F_EN-0" />
              <Enumeration Text="active" Value="1" Id="{_APP}_PT-F_EN-1" />
              <Enumeration Text="passive" Value="2" Id="{_APP}_PT-F_EN-2" />
            </TypeRestriction>
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Feedback" ParameterType="{_APP}_PT-B" Text="Feedback" Value="0" />
          <Parameter Id="{_APP}_P-2" Name="Position" ParameterType="{_APP}_PT-F" Text="Position" Value="0" />
          <Parameter Id="{_APP}_P-3" Name="Movement" ParameterType="{_APP}_PT-F" Text="Movement" Value="0" />
          <Parameter Id="{_APP}_P-4" Name="PositionDelay" ParameterType="{_APP}_PT-B" Text="Delay" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="4" />
          </Parameter>
          <Parameter Id="{_APP}_P-5" Name="MovementDelay" ParameterType="{_APP}_PT-B" Text="Delay" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="7" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_FEEDBACK}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_POSITION}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_MOVEMENT}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_POSITION_DELAY}" RefId="{_APP}_P-4" />
          <ParameterRef Id="{_MOVEMENT_DELAY}" RefId="{_APP}_P-5" />
        </ParameterRefs>
        <ComObjectTable>
          {_object(0)}
          {_object(1)}
          {_object(2)}
          {_object(3)}
        </ComObjectTable>
        <ComObjectRefs>
          <ComObjectRef Id="{_APP}_O-0_R-0" RefId="{_APP}_O-0" />
          <ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" />
          <ComObjectRef Id="{_APP}_O-2_R-2" RefId="{_APP}_O-2" />
          <ComObjectRef Id="{_APP}_O-3_R-3" RefId="{_APP}_O-3" />
        </ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="Feedback">
            <ComObjectRefRef RefId="{_APP}_O-0_R-0" />
            <ParameterRefRef RefId="{_FEEDBACK}" />
            <choose ParamRefId="{_FEEDBACK}">
              <when test="1">
                <ComObjectRefRef RefId="{_APP}_O-1_R-1" />
                <ParameterRefRef RefId="{_POSITION}" />
                <choose ParamRefId="{_POSITION}">
                  <when test="1">
                    <ComObjectRefRef RefId="{_APP}_O-2_R-2" />
                    <ParameterRefRef RefId="{_POSITION_DELAY}" />
                  </when>
                </choose>
                <ParameterRefRef RefId="{_MOVEMENT}" />
                <choose ParamRefId="{_MOVEMENT}">
                  <when test="1">
                    <ComObjectRefRef RefId="{_APP}_O-3_R-3" />
                    <ParameterRefRef RefId="{_MOVEMENT_DELAY}" />
                  </when>
                </choose>
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""

_CONFIGURED = {
    _FEEDBACK: "1",
    _POSITION: "1",
    _MOVEMENT: "1",
    _POSITION_DELAY: "1",
    _MOVEMENT_DELAY: "1",
}


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, values: dict[str, str]) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_both_delays_share_the_first_byte(application: Application) -> None:
    assert _device(application, _CONFIGURED)[_SEGMENT][0] == 0x09
    # One delay alone leaves the byte differing from the device in the other bit.
    one = {**_CONFIGURED, _MOVEMENT: "0"}
    assert _device(application, one)[_SEGMENT][0] == 0x08


def test_both_selectors_are_found_behind_the_switch(application: Application) -> None:
    recovered = recover_parameters(
        application,
        _device(application, _CONFIGURED),
        linked_objects={0},
        effort=RecoveryEffort.MEDIUM,
    )
    assert recovered.complete
    assert {key: recovered.values.get(key) for key in _CONFIGURED} == _CONFIGURED

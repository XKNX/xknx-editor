"""Linked group objects behind nested chooses on several settings the device keeps no copy of,
each shown on its own, take the values of all of them together.

"Energy", "Gas" and "Boiler" have no memory and are shown in the general block. A choose on
"Energy" holds a choose on "Gas", which holds a choose on "Boiler"; only all three on show
objects 10 and 11. None of them alone shows an object or another setting. "Water" (also
without memory) shows object 20 on its own.
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters

_APP = "M-00FA_A-00F8-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_ENERGY = f"{_APP}_P-1_R-1"
_GAS = f"{_APP}_P-2_R-2"
_BOILER = f"{_APP}_P-3_R-3"
_WATER = f"{_APP}_P-4_R-4"
_DELAY = f"{_APP}_P-5_R-5"


def _object(number: int) -> str:
    return (
        f'<ComObject Id="{_APP}_O-{number}" Name="O{number}" Text="Object {number}"'
        f' Number="{number}" FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled"'
        ' WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled"'
        ' UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" />'
    )


def _switch(number: int, name: str) -> str:
    return (
        f'<Parameter Id="{_APP}_P-{number}" Name="{name}" ParameterType="{_APP}_PT-B"'
        f' Text="{name}" Value="0" />'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="248" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Nested selectors"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          {_switch(1, "Energy")}
          {_switch(2, "Gas")}
          {_switch(3, "Boiler")}
          {_switch(4, "Water")}
          <Parameter Id="{_APP}_P-5" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="10">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_ENERGY}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_GAS}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_BOILER}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_WATER}" RefId="{_APP}_P-4" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-5" />
        </ParameterRefs>
        <ComObjectTable>
          {_object(10)}
          {_object(11)}
          {_object(20)}
        </ComObjectTable>
        <ComObjectRefs>
          <ComObjectRef Id="{_APP}_O-10_R-10" RefId="{_APP}_O-10" />
          <ComObjectRef Id="{_APP}_O-11_R-11" RefId="{_APP}_O-11" />
          <ComObjectRef Id="{_APP}_O-20_R-20" RefId="{_APP}_O-20" />
        </ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_DELAY}" />
            <ParameterRefRef RefId="{_ENERGY}" />
            <ParameterRefRef RefId="{_GAS}" />
            <ParameterRefRef RefId="{_BOILER}" />
            <ParameterRefRef RefId="{_WATER}" />
          </ParameterBlock>
          <choose ParamRefId="{_ENERGY}">
            <when test="1">
              <choose ParamRefId="{_GAS}">
                <when test="1">
                  <choose ParamRefId="{_BOILER}">
                    <when test="1">
                      <ComObjectRefRef RefId="{_APP}_O-10_R-10" />
                      <ComObjectRefRef RefId="{_APP}_O-11_R-11" />
                    </when>
                  </choose>
                </when>
              </choose>
            </when>
          </choose>
          <choose ParamRefId="{_WATER}">
            <when test="1">
              <ComObjectRefRef RefId="{_APP}_O-20_R-20" />
            </when>
          </choose>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _recover(linked: set[int], effort: RecoveryEffort) -> dict[str, str]:
    recovered = recover_parameters(
        _application(),
        {_SEGMENT: bytes([33])},
        linked_objects=linked,
        effort=effort,
    )
    assert recovered.complete
    return recovered.values


def test_all_nested_selectors_take_the_values_that_show_the_objects() -> None:
    for effort in (RecoveryEffort.FAST, RecoveryEffort.MEDIUM):
        values = _recover({10, 11}, effort)
        assert values[_ENERGY] == "1"
        assert values[_GAS] == "1"
        assert values[_BOILER] == "1"
        assert values.get(_WATER, "0") == "0"
        assert values[_DELAY] == "33"


def test_nested_selectors_are_found_beside_a_setting_shown_alone() -> None:
    values = _recover({10, 11, 20}, RecoveryEffort.FAST)
    assert values[_ENERGY] == values[_GAS] == values[_BOILER] == "1"
    assert values[_WATER] == "1"


def test_nothing_linked_behind_them_keeps_the_defaults() -> None:
    values = _recover({20}, RecoveryEffort.FAST)
    assert all(values.get(ref, "0") == "0" for ref in (_ENERGY, _GAS, _BOILER))
    assert values[_WATER] == "1"

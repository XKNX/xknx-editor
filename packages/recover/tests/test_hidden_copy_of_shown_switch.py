"""Reading a device back: a shown switch the device keeps no copy of, whose hidden copy a
calculation computes from it, while the same calculation computes the hidden copy of a shown
threshold that the device keeps. The calculation runs from the threshold read, so the switch
found from the group object its hidden copy shows has to give that copy its value first."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _with_calculated,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)

_APP = "M-00FA_A-0C02-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_SWITCH = f"{_APP}_P-1_R-1"
_SWITCH_COPY = f"{_APP}_P-1_R-2"
_STORED = f"{_APP}_P-3_R-3"
_THRESHOLD = f"{_APP}_P-4_R-4"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="194" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Hidden copy of a switch"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-OnOff" Name="OnOff">
                <TypeRestriction Base="Value" SizeInBit="8">
                  <Enumeration Text="Off" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
                  <Enumeration Text="On" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Byte" Name="Byte">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Brightness" ParameterType="{_APP}_PT-OnOff" Text="Brightness" Value="0" />
              <Parameter Id="{_APP}_P-3" Name="Stored" ParameterType="{_APP}_PT-Byte" Text="" Access="None" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-4" Name="Threshold" ParameterType="{_APP}_PT-Byte" Text="Threshold" Value="0" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_SWITCH}" RefId="{_APP}_P-1" Access="ReadWrite" />
              <ParameterRef Id="{_SWITCH_COPY}" RefId="{_APP}_P-1" Access="None" />
              <ParameterRef Id="{_STORED}" RefId="{_APP}_P-3" />
              <ParameterRef Id="{_THRESHOLD}" RefId="{_APP}_P-4" />
            </ParameterRefs>
            <ParameterCalculations>
              <ParameterCalculation Id="{_APP}_PC-1" Name="Copies" Language="JavaScript">
                <RLTransformation>s = h; u = t;</RLTransformation>
                <LRTransformation>h = s; t = u;</LRTransformation>
                <LParameters>
                  <ParameterRefRef RefId="{_SWITCH}" AliasName="s" />
                  <ParameterRefRef RefId="{_THRESHOLD}" AliasName="u" />
                </LParameters>
                <RParameters>
                  <ParameterRefRef RefId="{_SWITCH_COPY}" AliasName="h" />
                  <ParameterRefRef RefId="{_STORED}" AliasName="t" />
                </RParameters>
              </ParameterCalculation>
            </ParameterCalculations>
            <ComObjectTable>
              <ComObject Id="{_APP}_O-1" Name="Brightness" Text="Brightness" Number="1" FunctionText=""
                  ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
                  TransmitFlag="Disabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" />
            </ComObjectTable>
            <ComObjectRefs>
              <ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" />
            </ComObjectRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_SWITCH}" />
                <ParameterRefRef RefId="{_SWITCH_COPY}" />
                <ParameterRefRef RefId="{_THRESHOLD}" />
                <ParameterRefRef RefId="{_STORED}" />
                <choose ParamRefId="{_SWITCH_COPY}">
                  <when test="1"><ComObjectRefRef RefId="{_APP}_O-1_R-1" /></when>
                </choose>
              </ParameterBlock>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.fixture
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, *values: tuple[str, str]) -> dict[str, bytes]:
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values:
        ui.edit_parameter(ref_id, value)
    return ui.encode_to_memory()


def test_an_edit_of_the_switch_gives_its_hidden_copy_the_value(
    application: Application,
) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    ui.edit_parameter(_SWITCH, "1")
    ui.edit_parameter(_THRESHOLD, "5")
    assert ui.get_value(_SWITCH_COPY) == "1"
    assert ui.encode_to_memory()[_SEGMENT][0] == 5


def test_a_given_switch_computes_its_hidden_copy_beside_the_value_read(
    application: Application,
) -> None:
    computed = _with_calculated(application, {_STORED: "5", _SWITCH: "1"}, None)
    assert computed[_SWITCH_COPY] == "1"
    assert computed[_THRESHOLD] == "5"
    assert computed[_SWITCH] == "1"


def test_without_a_given_switch_its_copy_keeps_its_default(
    application: Application,
) -> None:
    computed = _with_calculated(application, {_STORED: "5"}, None)
    assert computed.get(_SWITCH_COPY, "0") == "0"
    assert computed[_THRESHOLD] == "5"


def test_the_switch_whose_object_is_linked_is_found(application: Application) -> None:
    memory = _device(application, (_SWITCH, "1"), (_THRESHOLD, "5"))

    result = recover_parameters(
        application,
        memory,
        linked_objects={1},
        object_sizes={1: "1 Bit"},
        effort=RecoveryEffort.FAST,
    )

    assert result.values.get(_SWITCH) == "1"
    assert result.values.get(_SWITCH_COPY) == "1"
    assert result.values.get(_THRESHOLD) == "5"
    assert result.complete


def test_without_a_linked_object_the_switch_stays_off(application: Application) -> None:
    memory = _device(application, (_THRESHOLD, "5"))

    result = recover_parameters(application, memory, linked_objects=set())

    assert result.values.get(_SWITCH, "0") == "0"
    assert result.values.get(_THRESHOLD) == "5"
    assert result.complete

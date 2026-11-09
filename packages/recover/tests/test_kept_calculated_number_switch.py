"""Reading a device back: a hidden switch of a large number range (0 to 255) that a calculation
computes from a function the configuration no longer shows keeps the value computed before, and
shows a group object the device links only at the value its choose tests for. Only the linked
object tells it: the search tries the values the chooses on the switch test for, not only the
ends and neighbours of the range."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _kept_values_to_try,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F5-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_MODE = f"{_APP}_P-1_R-1"
_LEAD = f"{_APP}_P-2_R-2"
_USE = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="245" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Kept number switch"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
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
          <Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-OnOff" Text="Slave" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-2" Name="Lead" ParameterType="{_APP}_PT-OnOff" Text="Lead temperature" Value="0" />
          <Parameter Id="{_APP}_P-3" Name="Use" ParameterType="{_APP}_PT-Byte" Text="" Access="None" Value="0" />
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_MODE}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_LEAD}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_USE}" RefId="{_APP}_P-3" Access="None" />
        </ParameterRefs>
        <ParameterCalculations>
          <ParameterCalculation Id="{_APP}_PC-1" Name="Use" Language="JavaScript">
            <RLTransformation>if (use == 0) {{ lead = 0; }}</RLTransformation>
            <LRTransformation>use = lead ? 2 : 0;</LRTransformation>
            <LParameters><ParameterRefRef RefId="{_LEAD}" AliasName="lead" /></LParameters>
            <RParameters><ParameterRefRef RefId="{_USE}" AliasName="use" /></RParameters>
          </ParameterCalculation>
        </ParameterCalculations>
        <ComObjectTable>
          <ComObject Id="{_APP}_O-1" Name="Temperature" Text="Temperature" Number="1" FunctionText=""
              ObjectSize="2 Bytes" ReadFlag="Enabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
              TransmitFlag="Disabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-9-1" />
        </ComObjectTable>
        <ComObjectRefs>
          <ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" />
        </ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_MODE}" />
            <choose ParamRefId="{_MODE}">
              <when test="0"><ParameterRefRef RefId="{_LEAD}" /></when>
            </choose>
            <ParameterRefRef RefId="{_USE}" />
            <choose ParamRefId="{_USE}">
              <when test="2"><ComObjectRefRef RefId="{_APP}_O-1_R-1" /></when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_the_kept_switch_is_found_at_the_value_its_choose_tests_for(
    application: Application,
) -> None:
    # The lead temperature switched on makes the switch 2, which shows the object; the slave
    # mode then hides the lead function, the switch keeps its value and the object stays.
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_LEAD, "1")
    ui.set_parameter_ref(_MODE, "1")
    assert ui.get_value(_USE) == "2"
    memory = ui.encode_to_memory()

    result = recover_parameters(
        application,
        memory,
        linked_objects={1},
        object_sizes={1: "2 Bytes"},
        effort=RecoveryEffort.FAST,
    )

    assert result.values.get(_MODE) == "1"
    assert result.values.get(_USE) == "2"
    assert result.complete


def test_the_tested_value_is_tried_first(application: Application) -> None:
    hidden = seed_dynamic_ui(application, {_MODE: "1"}, None)
    assert hidden is not None
    assert _kept_values_to_try(hidden, _USE, ()) == ["2", "1", "255"]

"""A module's hidden copy of an application setting (an Assign in the module copies it) that
picks what the module writes: the application setting, kept nowhere on the device, is found from
the memory the module instance holds.

The application's "LED illumination (all buttons)" (no copy on the device) is copied into each
button's hidden "LED illumination" selector; "Disabled" makes the button write 5 into its LED
byte, every other value 0. A 5 in the memory is only "Disabled".
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0006-01-0000"
_ALL = f"{_APP}_P-1_R-1"
_BUTTON = f"{_APP}_MD-1_M-1_MI-1"
_COPY = f"{_BUTTON}_P-2_R-2"
_LED = f"{_BUTTON}_P-3_R-3"
_SHOWN = f"{_BUTTON}_P-4_R-4"
_SEGMENT = f"{_APP}_AS-0000"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="6" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assigned module selector"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-L" Name="L">
                <TypeRestriction Base="Value" SizeInBit="8">
                  <Enumeration Text="Disabled" Value="5" Id="{_APP}_PT-L_EN-5" />
                  <Enumeration Text="Regular" Value="0" Id="{_APP}_PT-L_EN-0" />
                  <Enumeration Text="Separately" Value="3" Id="{_APP}_PT-L_EN-3" />
                </TypeRestriction>
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="AllLeds" ParameterType="{_APP}_PT-L" Text="LED illumination (all buttons)" Value="3" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_ALL}" RefId="{_APP}_P-1" />
            </ParameterRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Button">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Offset" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-2" Name="AllLeds" ParameterType="{_APP}_PT-L" Text="LED illumination (all buttons)" Access="None" Value="3" />
                  <Parameter Id="{_APP}_MD-1_P-4" Name="Shown" ParameterType="{_APP}_PT-L" Text="Shown as" Access="Read" Value="3" />
                  <Parameter Id="{_APP}_MD-1_P-3" Name="Led" ParameterType="{_APP}_PT-L" Text="LED illumination" Access="None" Value="0">
                    <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-2_R-2" RefId="{_APP}_MD-1_P-2" />
                  <ParameterRef Id="{_APP}_MD-1_P-3_R-3" RefId="{_APP}_MD-1_P-3" />
                  <ParameterRef Id="{_APP}_MD-1_P-4_R-4" RefId="{_APP}_MD-1_P-4" />
                </ParameterRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Button">
                  <Assign TargetParamRefRef="{_APP}_MD-1_P-2_R-2" SourceParamRefRef="{_ALL}" />
                  <Assign TargetParamRefRef="{_APP}_MD-1_P-4_R-4" SourceParamRefRef="{_APP}_MD-1_P-2_R-2" />
                  <ParameterRefRef RefId="{_APP}_MD-1_P-4_R-4" />
                  <choose ParamRefId="{_APP}_MD-1_P-2_R-2">
                    <when test="5">
                      <Assign TargetParamRefRef="{_APP}_MD-1_P-3_R-3" Value="5" />
                    </when>
                    <when default="true">
                      <Assign TargetParamRefRef="{_APP}_MD-1_P-3_R-3" Value="0" />
                    </when>
                  </choose>
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_ALL}" />
              </ParameterBlock>
              <Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="2" /></Module>
            </ChannelIndependentBlock>
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


def _device(application: Application, all_leds: str) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, {_ALL: all_leds})
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_button_writes_what_the_copied_setting_picks(
    application: Application,
) -> None:
    assert _device(application, "5")[_SEGMENT][2] == 5
    assert _device(application, "3")[_SEGMENT][2] == 0


def test_the_copy_names_the_application_setting_it_is_copied_from(
    application: Application,
) -> None:
    ui = seed_dynamic_ui(application, {_ALL: "5"})
    assert ui is not None
    assert ui.assigned_source(_COPY) == _ALL
    # One copied from a setting of the same instance names it with the instance.
    assert ui.assigned_source(_SHOWN) == _COPY
    assert ui.get_value(_LED) == "5"
    # A constant assigned has no source, nor has the application setting itself.
    assert ui.assigned_source(_LED) is None
    assert ui.assigned_source(_ALL) is None


@pytest.mark.parametrize("all_leds", ["5", "3"])
def test_the_memory_of_the_button_tells_the_application_setting(
    application: Application, all_leds: str
) -> None:
    recovered = recover_parameters(application, _device(application, all_leds))
    assert recovered.complete
    ui = seed_dynamic_ui(application, recovered.values)
    assert ui is not None
    assert ui.encode_to_memory()[_SEGMENT][2] == (5 if all_leds == "5" else 0)
    if all_leds == "5":
        assert recovered.values[_ALL] == "5"

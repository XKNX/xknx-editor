"""A module definition without a Dynamic section (a placeholder that only reserves room) still
takes what its arguments allocate: the modules after it keep their addresses, also when a choose
swaps the placeholder for a module with the same allocation."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml

_APP = "M-00FA_A-00F3-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_SCENE = f"{_APP}_P-1_R-1"
_REAL = f"{_APP}_MD-1"
_PLACEHOLDER = f"{_APP}_MD-2"
_BUTTON = f"{_APP}_MD-3"
_BUTTON_VALUE = f"{_BUTTON}_M-3_MI-1_P-1_R-1"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="243" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Placeholder module"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="8"><Data>AAAAAAAAAAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Scene" ParameterType="{_APP}_PT-N" Text="Scene" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs><ParameterRef Id="{_SCENE}" RefId="{_APP}_P-1" /></ParameterRefs>
        <Allocators><Allocator Id="{_APP}_L-1" Name="Address" Start="1" maxInclusive="7" /></Allocators>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_REAL}" Name="Scene">
          <Arguments><Argument Id="{_REAL}_A-1" Name="Offset" Allocates="3" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_REAL}_P-1" Name="Value" ParameterType="{_APP}_PT-N" Text="Value" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_REAL}_A-1" />
              </Parameter>
            </Parameters>
            <ParameterRefs><ParameterRef Id="{_REAL}_P-1_R-1" RefId="{_REAL}_P-1" /></ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_REAL}_PB-1" Text="Scene"><ParameterRefRef RefId="{_REAL}_P-1_R-1" /></ParameterBlock>
          </Dynamic>
        </ModuleDef>
        <ModuleDef Id="{_PLACEHOLDER}" Name="Scene placeholder">
          <Arguments><Argument Id="{_PLACEHOLDER}_A-1" Name="Offset" Allocates="3" /></Arguments>
          <Static />
        </ModuleDef>
        <ModuleDef Id="{_BUTTON}" Name="Button">
          <Arguments><Argument Id="{_BUTTON}_A-1" Name="Offset" Allocates="1" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_BUTTON}_P-1" Name="Value" ParameterType="{_APP}_PT-N" Text="Value" Value="9">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_BUTTON}_A-1" />
              </Parameter>
            </Parameters>
            <ParameterRefs><ParameterRef Id="{_BUTTON}_P-1_R-1" RefId="{_BUTTON}_P-1" /></ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_BUTTON}_PB-1" Text="Button"><ParameterRefRef RefId="{_BUTTON}_P-1_R-1" /></ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General"><ParameterRefRef RefId="{_SCENE}" /></ParameterBlock>
        </ChannelIndependentBlock>
        <choose ParamRefId="{_SCENE}">
          <when test="1">
            <Module Id="{_REAL}_M-1" RefId="{_REAL}"><NumericArg RefId="{_REAL}_A-1" AllocatorRefId="{_APP}_L-1" /></Module>
          </when>
          <when test="0">
            <Module Id="{_PLACEHOLDER}_M-2" RefId="{_PLACEHOLDER}"><NumericArg RefId="{_PLACEHOLDER}_A-1" AllocatorRefId="{_APP}_L-1" /></Module>
          </when>
        </choose>
        <Module Id="{_BUTTON}_M-3" RefId="{_BUTTON}"><NumericArg RefId="{_BUTTON}_A-1" AllocatorRefId="{_APP}_L-1" /></Module>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


@pytest.mark.parametrize("scene", ["0", "1"])
def test_the_module_after_a_placeholder_keeps_its_address(
    application: Application, scene: str
) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_SCENE, scene)
    ui.set_parameter_ref(_BUTTON_VALUE, "7")
    memory = ui.encode_to_memory()[_SEGMENT]
    # The scene takes 1..3 whether it is the real module or the placeholder: the button is at 4.
    assert memory[4] == 7
    assert memory[1:4] == b"\x00\x00\x00"


def test_a_placeholder_shows_nothing(application: Application) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    instances = {ref for _instance, ref in ui.get_module_instances()}
    assert instances == {_PLACEHOLDER, _BUTTON}

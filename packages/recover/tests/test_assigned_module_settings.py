"""A setting of a module instance the device keeps no copy of is read from the stored setting of
the instance an Assign copies it into.

Each "Output" instance shows "Behaviour" (no memory, initially 1) and copies it into its hidden
"Sending" setting, which the device stores at the instance's offset (as a weather station's
outputs do). Without the Assign read back, the copy would get the default of
"Behaviour" again and write another byte than the device holds.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0007-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_FIRST = f"{_APP}_MD-1_M-1_MI-1"
_SECOND = f"{_APP}_MD-1_M-2_MI-1"
_BEHAVIOUR = "P-1_R-1"
_SENDING = "P-2_R-2"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assigned module settings"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-S" Name="S">
                <TypeRestriction Base="Value" SizeInBit="2">
                  <Enumeration Text="No telegram" Value="0" Id="{_APP}_PT-S_EN-0" />
                  <Enumeration Text="Once" Value="1" Id="{_APP}_PT-S_EN-1" />
                  <Enumeration Text="Cyclically" Value="2" Id="{_APP}_PT-S_EN-2" />
                </TypeRestriction>
              </ParameterType>
            </ParameterTypes>
            <Parameters />
            <ParameterRefs />
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Output">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Offset" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-1" Name="Behaviour" ParameterType="{_APP}_PT-S" Text="If the condition is not met" Value="1" />
                  <Parameter Id="{_APP}_MD-1_P-2" Name="Sending" ParameterType="{_APP}_PT-S" Text="Sending" Access="None" Value="1">
                    <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" />
                  <ParameterRef Id="{_APP}_MD-1_P-2_R-2" RefId="{_APP}_MD-1_P-2" Access="None" />
                </ParameterRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Output">
                  <ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
                  <Assign TargetParamRefRef="{_APP}_MD-1_P-2_R-2" SourceParamRefRef="{_APP}_MD-1_P-1_R-1" />
                  <ParameterRefRef RefId="{_APP}_MD-1_P-2_R-2" />
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="0" /></Module>
              <Module Id="{_APP}_MD-1_M-2" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="1" /></Module>
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


def _device(application: Application, first: str, second: str) -> dict[str, bytes]:
    ui = seed_dynamic_ui(
        application,
        {f"{_FIRST}_{_BEHAVIOUR}": first, f"{_SECOND}_{_BEHAVIOUR}": second},
    )
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_assigns_of_the_instances_are_recorded(application: Application) -> None:
    ui = seed_dynamic_ui(application, {})
    assert ui is not None
    assert ui.assigned_from() == {
        f"{_FIRST}_{_SENDING}": f"{_FIRST}_{_BEHAVIOUR}",
        f"{_SECOND}_{_SENDING}": f"{_SECOND}_{_BEHAVIOUR}",
    }


@pytest.mark.parametrize(("first", "second"), [("0", "2"), ("2", "0"), ("1", "1")])
def test_the_setting_of_each_instance_is_read_from_its_copy(
    application: Application, first: str, second: str
) -> None:
    segments = _device(application, first, second)
    assert segments[_SEGMENT] == bytes([int(first) << 6, int(second) << 6])
    recovered = recover_parameters(application, segments)
    assert recovered.complete
    assert recovered.values.get(f"{_FIRST}_{_BEHAVIOUR}", "1") == first
    assert recovered.values.get(f"{_SECOND}_{_BEHAVIOUR}", "1") == second
    ui = seed_dynamic_ui(application, recovered.values)
    assert ui is not None
    assert ui.encode_to_memory()[_SEGMENT] == segments[_SEGMENT]

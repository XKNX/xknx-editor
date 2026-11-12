"""A setting of the application the device keeps no copy of is read from the stored setting of a
module instance an Assign copies it into.

The application shows "Delay" (no memory, initially 17); its "Output" module copies it into the
instance's hidden "Stored delay" setting, which the device stores at the instance's offset.
Without the Assign read back across the scopes, "Delay" would keep its default, and the copy
would write another byte than the device holds.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0008-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_INSTANCE = f"{_APP}_MD-1_M-1_MI-1"
_DELAY = f"{_APP}_P-1_R-1"
_STORED = f"{_INSTANCE}_P-2_R-2"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="8" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assigned application setting"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-D" Name="D">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="59" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Delay" ParameterType="{_APP}_PT-D" Text="Delay" Value="17" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-1" />
            </ParameterRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Output">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Offset" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-2" Name="Stored delay" ParameterType="{_APP}_PT-D" Text="Delay" Access="None" Value="17">
                    <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-2_R-2" RefId="{_APP}_MD-1_P-2" Access="None" />
                </ParameterRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Output">
                  <ParameterRefRef RefId="{_APP}_MD-1_P-2_R-2" />
                  <Assign TargetParamRefRef="{_APP}_MD-1_P-2_R-2" SourceParamRefRef="{_DELAY}" />
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_DELAY}" />
              </ParameterBlock>
              <Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="1" /></Module>
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


def test_the_assign_of_the_instance_copies_the_application_setting(
    application: Application,
) -> None:
    ui = seed_dynamic_ui(application, {})
    assert ui is not None
    assert ui.assigned_from() == {_STORED: _DELAY}


@pytest.mark.parametrize("delay", ["0", "51", "17"])
def test_the_application_setting_is_read_from_its_copy(
    application: Application, delay: str
) -> None:
    ui = seed_dynamic_ui(application, {_DELAY: delay})
    assert ui is not None
    segments = {key: bytes(data) for key, data in ui.encode_to_memory().items()}
    assert segments[_SEGMENT] == bytes([0, int(delay)])
    recovered = recover_parameters(application, segments)
    assert recovered.complete
    assert recovered.values.get(_DELAY, "17") == delay
    again = seed_dynamic_ui(application, recovered.values)
    assert again is not None
    assert again.encode_to_memory()[_SEGMENT] == segments[_SEGMENT]

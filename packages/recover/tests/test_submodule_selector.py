"""A setting the device keeps no copy of that decides which reference of a submodule's
parameter is active: the value the memory holds tells it.

The submodule instance is named after its enclosing instance
(``..._MD-1_M-1_MI-1_SM-1_M-1_MI-1`` inside ``..._MD-1_M-1_MI-1``). The application's "on/off function"
switch and the submodule's own "show the setting" switch (neither kept on the device) pick the
shown reference of the submodule's "when switching off" parameter, or a hidden one fixed at 0: a
1 in the memory is only the shown one's.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    _definition_of,  # pyright: ignore[reportPrivateUsage]
    _instance_of,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0005-01-0000"
_SWITCH = f"{_APP}_P-1_R-1"
_DISPLAY = f"{_APP}_MD-1_M-1_MI-1_SM-1_M-1_MI-1"
_OFF = f"{_DISPLAY}_SM-1_P-1_R-2"
_SHOW = f"{_DISPLAY}_SM-1_P-2_R-4"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="5" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Submodule selector"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="8"><Data>AAAAAAAAAAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Switch" ParameterType="{_APP}_PT-B" Text="On/off function" Value="0" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_SWITCH}" RefId="{_APP}_P-1" />
            </ParameterRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Controller">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Offset" />
              </Arguments>
              <Static />
              <SubModuleDefs>
                <ModuleDef Id="{_APP}_MD-1_SM-1" Name="Display">
                  <Arguments>
                    <Argument Id="{_APP}_MD-1_SM-1_A-1" Name="Offset" />
                  </Arguments>
                  <Static>
                    <Parameters>
                      <Parameter Id="{_APP}_MD-1_SM-1_P-1" Name="Off" ParameterType="{_APP}_PT-B" Text="When switching off" Value="1">
                        <Memory CodeSegment="{_APP}_AS-0000" Offset="2" BitOffset="0" BaseOffset="{_APP}_MD-1_SM-1_A-1" />
                      </Parameter>
                      <Parameter Id="{_APP}_MD-1_SM-1_P-2" Name="Show" ParameterType="{_APP}_PT-B" Text="Show the setting" Value="0" />
                    </Parameters>
                    <ParameterRefs>
                      <ParameterRef Id="{_APP}_MD-1_SM-1_P-2_R-4" RefId="{_APP}_MD-1_SM-1_P-2" />
                      <ParameterRef Id="{_APP}_MD-1_SM-1_P-1_R-3" RefId="{_APP}_MD-1_SM-1_P-1" Access="None" Value="0" />
                      <ParameterRef Id="{_APP}_MD-1_SM-1_P-1_R-2" RefId="{_APP}_MD-1_SM-1_P-1" />
                    </ParameterRefs>
                  </Static>
                  <Dynamic>
                    <ParameterBlock Id="{_APP}_MD-1_SM-1_PB-1" Text="Display">
                      <choose ParamRefId="{_SWITCH}">
                        <when test="0">
                          <ParameterRefRef RefId="{_APP}_MD-1_SM-1_P-1_R-3" />
                        </when>
                        <when test="1">
                          <ParameterRefRef RefId="{_APP}_MD-1_SM-1_P-2_R-4" />
                          <choose ParamRefId="{_APP}_MD-1_SM-1_P-2_R-4">
                            <when test="0">
                              <ParameterRefRef RefId="{_APP}_MD-1_SM-1_P-1_R-3" />
                            </when>
                            <when test="1">
                              <ParameterRefRef RefId="{_APP}_MD-1_SM-1_P-1_R-2" />
                            </when>
                          </choose>
                        </when>
                      </choose>
                    </ParameterBlock>
                  </Dynamic>
                </ModuleDef>
              </SubModuleDefs>
              <Dynamic>
                <Module Id="{_APP}_MD-1_SM-1_M-1" RefId="{_APP}_MD-1_SM-1">
                  <NumericArg RefId="{_APP}_MD-1_SM-1_A-1" Value="1" BaseValue="{_APP}_MD-1_A-1" />
                </Module>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_SWITCH}" />
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


def _device(application: Application, switch: str, show: str = "1") -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, {_SWITCH: switch, _SHOW: show})
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_submodule_parameter_is_placed_behind_its_enclosing_instance(
    application: Application,
) -> None:
    # Controller at 2, its display at 2 + 1, the parameter at 3 + 2.
    assert _device(application, "1")[f"{_APP}_AS-0000"][5] == 1
    assert _device(application, "0")[f"{_APP}_AS-0000"][5] == 0
    assert _device(application, "1", show="0")[f"{_APP}_AS-0000"][5] == 0


def test_the_memory_tells_the_setting_that_shows_the_submodule_reference(
    application: Application,
) -> None:
    recovered = recover_parameters(application, _device(application, "1"))
    assert recovered.complete
    assert recovered.values[_SWITCH] == "1"
    # The submodule's own switch too, a selector inside the submodule instance.
    assert recovered.values[_SHOW] == "1"
    ui = seed_dynamic_ui(application, recovered.values)
    assert ui is not None
    assert ui.get_value(_OFF) == "1"


def test_the_hidden_reference_keeps_the_setting_off(application: Application) -> None:
    recovered = recover_parameters(application, _device(application, "0"))
    assert recovered.complete
    assert recovered.values.get(_SWITCH, "0") == "0"


def test_a_submodule_reference_belongs_to_the_submodule_definition() -> None:
    local = f"{_APP}_MD-1_SM-1_P-1_R-2"
    assert _definition_of(local) == f"{_APP}_MD-1_SM-1"
    assert _definition_of(f"{_APP}_MD-1_P-4_R-4") == f"{_APP}_MD-1"
    assert _instance_of(_OFF, local) == (f"{_DISPLAY}_SM-1", f"{_APP}_MD-1_SM-1")

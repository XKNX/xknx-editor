"""Reading a device back: a byte only a submodule instance writes that the tree does not make
yet (the function of a channel the device keeps no copy of places it). The setting of the
application stored at the same byte, shown otherwise, cannot hold the value, so the byte has
no active setting: the selectors on the way to the absent submodule are tried."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _absent_module_gates,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F5-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_CHANNEL = f"{_MD}_M-1_MI-1"
_FUNCTION = f"{_CHANNEL}_P-1_R-1"
_VALUE = f"{_CHANNEL}_SM-2_M-1_MI-1_SM-2_P-1_R-1"
_OLD = f"{_APP}_P-1_R-1"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="245" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Byte of an absent submodule"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Old" ParameterType="{_APP}_PT-B" Text="Old layout" Value="0" />
          <Parameter Id="{_APP}_P-2" Name="Legacy" ParameterType="{_APP}_PT-B" Text="Legacy" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_OLD}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_APP}_P-2_R-2" RefId="{_APP}_P-2" />
        </ParameterRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments><Argument Id="{_MD}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Function" ParameterType="{_APP}_PT-B" Text="Function" Value="0" />
            </Parameters>
            <ParameterRefs><ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" /></ParameterRefs>
          </Static>
          <SubModuleDefs>
            <ModuleDef Id="{_MD}_SM-1" Name="Switch">
              <Arguments><Argument Id="{_MD}_SM-1_A-1" Name="Offset" /></Arguments>
              <Static />
              <Dynamic><ParameterBlock Id="{_MD}_SM-1_PB-1" Text="Switch" /></Dynamic>
            </ModuleDef>
            <ModuleDef Id="{_MD}_SM-2" Name="Value">
              <Arguments><Argument Id="{_MD}_SM-2_A-1" Name="Offset" /></Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_MD}_SM-2_P-1" Name="Value" ParameterType="{_APP}_PT-N" Text="Value" Value="0">
                    <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_SM-2_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs><ParameterRef Id="{_MD}_SM-2_P-1_R-1" RefId="{_MD}_SM-2_P-1" /></ParameterRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_MD}_SM-2_PB-1" Text="Value">
                  <ParameterRefRef RefId="{_MD}_SM-2_P-1_R-1" />
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </SubModuleDefs>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
            </ParameterBlock>
            <choose ParamRefId="{_MD}_P-1_R-1">
              <when test="0">
                <Module Id="{_MD}_SM-1_M-1" RefId="{_MD}_SM-1">
                  <NumericArg RefId="{_MD}_SM-1_A-1" Value="0" BaseValue="{_MD}_A-1" />
                </Module>
              </when>
              <when test="1">
                <Module Id="{_MD}_SM-2_M-1" RefId="{_MD}_SM-2">
                  <NumericArg RefId="{_MD}_SM-2_A-1" Value="0" BaseValue="{_MD}_A-1" />
                </Module>
              </when>
            </choose>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_OLD}" />
            <choose ParamRefId="{_OLD}">
              <when test="1"><ParameterRefRef RefId="{_APP}_P-2_R-2" /></when>
            </choose>
          </ParameterBlock>
          <Module Id="{_MD}_M-1" RefId="{_MD}"><NumericArg RefId="{_MD}_A-1" Value="1" /></Module>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, values: dict[str, str]) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_value_of_the_function_is_written_behind_the_channel(
    application: Application,
) -> None:
    assert _device(application, {_FUNCTION: "1", _VALUE: "9"})[_SEGMENT] == bytes(
        [0, 9]
    )


def test_the_selectors_of_absent_submodules_of_any_definition(
    application: Application,
) -> None:
    ui = seed_dynamic_ui(application, {})
    assert ui is not None
    # No group objects: only as owners of bytes.
    assert _absent_module_gates(ui) == set()
    assert _absent_module_gates(ui, objects=False) == {_FUNCTION}


def test_the_function_whose_submodule_writes_the_byte_is_found(
    application: Application,
) -> None:
    memory = _device(application, {_FUNCTION: "1", _VALUE: "9"})

    result = recover_parameters(application, memory, effort=RecoveryEffort.FAST)

    assert result.complete
    assert result.values[_FUNCTION] == "1"
    assert result.values[_VALUE] == "9"
    assert result.values.get(_OLD, "0") == "0"


def test_the_default_function_stays(application: Application) -> None:
    result = recover_parameters(
        application, _device(application, {}), effort=RecoveryEffort.FAST
    )

    assert result.complete
    assert result.values.get(_FUNCTION, "0") == "0"

"""Reading a device back: a linked group object of a submodule instance that only a setting of
the enclosing module instance the device keeps no copy of makes (a function chosen for a
channel), behind a stored setting of the submodule, is found by trying the selectors on the way
to the submodule, as settings of the enclosing instance."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _absent_module_gates,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F4-01-0000"
_MD = f"{_APP}_MD-1"
_SM = f"{_MD}_SM-1"
_SEGMENT = f"{_APP}_AS-0000"
_CHANNELS = (f"{_MD}_M-1_MI-1", f"{_MD}_M-2_MI-1")
_FUNCTION = tuple(f"{channel}_P-1_R-1" for channel in _CHANNELS)
_ENABLE = tuple(f"{channel}_SM-1_M-1_MI-1_SM-1_P-1_R-1" for channel in _CHANNELS)


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="244" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Absent submodule objects"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
        </ParameterTypes>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments>
            <Argument Id="{_MD}_A-1" Name="Offset" />
            <Argument Id="{_MD}_A-2" Name="ObjectBase" />
          </Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Function" ParameterType="{_APP}_PT-B" Text="Function" Value="0" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
            </ParameterRefs>
          </Static>
          <SubModuleDefs>
            <ModuleDef Id="{_SM}" Name="Output">
              <Arguments>
                <Argument Id="{_SM}_A-1" Name="Offset" />
                <Argument Id="{_SM}_A-2" Name="ObjectBase" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_SM}_P-1" Name="Enable" ParameterType="{_APP}_PT-B" Text="Output" Value="0">
                    <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_SM}_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_SM}_P-1_R-1" RefId="{_SM}_P-1" />
                </ParameterRefs>
                <ComObjects>
                  <ComObject Id="{_SM}_O-1" Name="Output" Text="Output" Number="1" FunctionText=""
                      ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
                      TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"
                      BaseNumber="{_SM}_A-2" />
                </ComObjects>
                <ComObjectRefs>
                  <ComObjectRef Id="{_SM}_O-1_R-1" RefId="{_SM}_O-1" />
                </ComObjectRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_SM}_PB-1" Text="Output">
                  <ParameterRefRef RefId="{_SM}_P-1_R-1" />
                  <choose ParamRefId="{_SM}_P-1_R-1">
                    <when test="1"><ComObjectRefRef RefId="{_SM}_O-1_R-1" /></when>
                  </choose>
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </SubModuleDefs>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
            </ParameterBlock>
            <choose ParamRefId="{_MD}_P-1_R-1">
              <when test="1">
                <Module Id="{_SM}_M-1" RefId="{_SM}">
                  <NumericArg RefId="{_SM}_A-1" Value="0" BaseValue="{_MD}_A-1" />
                  <NumericArg RefId="{_SM}_A-2" Value="0" BaseValue="{_MD}_A-2" />
                </Module>
              </when>
            </choose>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <Module Id="{_MD}_M-1" RefId="{_MD}">
            <NumericArg RefId="{_MD}_A-1" Value="1" />
            <NumericArg RefId="{_MD}_A-2" Value="10" />
          </Module>
          <Module Id="{_MD}_M-2" RefId="{_MD}">
            <NumericArg RefId="{_MD}_A-1" Value="2" />
            <NumericArg RefId="{_MD}_A-2" Value="20" />
          </Module>
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
    """The memory of a device configured with ``values`` (ref id -> value)."""
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_function_places_the_output_behind_the_channel(
    application: Application,
) -> None:
    memory = _device(application, {_FUNCTION[1]: "1", _ENABLE[1]: "1"})
    assert memory[_SEGMENT][2] == 1
    ui = seed_dynamic_ui(application, {_FUNCTION[1]: "1", _ENABLE[1]: "1"})
    assert ui is not None
    assert f"{_CHANNELS[1]}_SM-1_M-1_MI-1" in {i for i, _ in ui.get_module_instances()}


def test_the_selectors_of_absent_submodules_with_objects(
    application: Application,
) -> None:
    ui = seed_dynamic_ui(application, {})
    assert ui is not None
    # The function of each channel, as a setting of that channel.
    assert _absent_module_gates(ui) == set(_FUNCTION)

    shown = seed_dynamic_ui(application, {_FUNCTION[1]: "1"})
    assert shown is not None
    # Made in the second channel now: only the first one's function is left to switch on.
    assert _absent_module_gates(shown) == {_FUNCTION[0]}


def test_the_function_of_a_channel_whose_output_object_is_linked_is_found(
    application: Application,
) -> None:
    # The second channel's function is chosen and its output is on and linked: the submodule
    # instance is only there with the function, its object only with its own setting read too.
    memory = _device(application, {_FUNCTION[1]: "1", _ENABLE[1]: "1"})

    result = recover_parameters(
        application,
        memory,
        linked_objects={21},
        object_sizes={21: "1 Bit"},
        effort=RecoveryEffort.FAST,
    )

    assert result.values.get(_FUNCTION[1]) == "1"
    assert result.values.get(_ENABLE[1]) == "1"
    assert result.values.get(_FUNCTION[0], "0") == "0"
    assert result.complete

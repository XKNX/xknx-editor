"""A shown setting of a module instance that picks which submodule the instance holds: the
memory of the submodule's settings tells it.

The "Mode" setting of the channel (kept nowhere on the device) places either the "Display"
submodule, whose setting takes only 0 or 1, or the "Wide" submodule, whose setting at the same
byte takes any value. A byte the "Display" one cannot hold is only the "Wide" one's.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    _enclosing_instance,  # pyright: ignore[reportPrivateUsage]
    _gates_of,  # pyright: ignore[reportPrivateUsage]
    _module_gates_of,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0006-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_CHANNEL = f"{_MD}_M-1_MI-1"
_MODE = f"{_CHANNEL}_P-1_R-1"
_DISPLAY = f"{_CHANNEL}_SM-1_M-1_MI-1"
_NARROW = f"{_DISPLAY}_SM-1_P-1_R-1"
_WIDE_INSTANCE = f"{_CHANNEL}_SM-2_M-1_MI-1"
_WIDE = f"{_WIDE_INSTANCE}_SM-2_P-1_R-1"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="6" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Enclosing selector"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="8"><Data>AAAAAAAAAAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_MD}" Name="Channel">
              <Arguments>
                <Argument Id="{_MD}_A-1" Name="Offset" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_MD}_P-1" Name="Mode" ParameterType="{_APP}_PT-B" Text="Mode" Value="0" />
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
                </ParameterRefs>
              </Static>
              <SubModuleDefs>
                <ModuleDef Id="{_MD}_SM-1" Name="Display">
                  <Arguments>
                    <Argument Id="{_MD}_SM-1_A-1" Name="Offset" />
                  </Arguments>
                  <Static>
                    <Parameters>
                      <Parameter Id="{_MD}_SM-1_P-1" Name="Narrow" ParameterType="{_APP}_PT-B" Text="Narrow" Value="0">
                        <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_SM-1_A-1" />
                      </Parameter>
                    </Parameters>
                    <ParameterRefs>
                      <ParameterRef Id="{_MD}_SM-1_P-1_R-1" RefId="{_MD}_SM-1_P-1" />
                    </ParameterRefs>
                  </Static>
                  <Dynamic>
                    <ParameterBlock Id="{_MD}_SM-1_PB-1" Text="Display">
                      <ParameterRefRef RefId="{_MD}_SM-1_P-1_R-1" />
                    </ParameterBlock>
                  </Dynamic>
                </ModuleDef>
                <ModuleDef Id="{_MD}_SM-2" Name="Wide">
                  <Arguments>
                    <Argument Id="{_MD}_SM-2_A-1" Name="Offset" />
                  </Arguments>
                  <Static>
                    <Parameters>
                      <Parameter Id="{_MD}_SM-2_P-1" Name="Wide" ParameterType="{_APP}_PT-N" Text="Wide" Value="0">
                        <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_SM-2_A-1" />
                      </Parameter>
                    </Parameters>
                    <ParameterRefs>
                      <ParameterRef Id="{_MD}_SM-2_P-1_R-1" RefId="{_MD}_SM-2_P-1" />
                    </ParameterRefs>
                  </Static>
                  <Dynamic>
                    <ParameterBlock Id="{_MD}_SM-2_PB-1" Text="Wide">
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
                      <NumericArg RefId="{_MD}_SM-1_A-1" Value="1" BaseValue="{_MD}_A-1" />
                    </Module>
                  </when>
                  <when test="1">
                    <Module Id="{_MD}_SM-2_M-1" RefId="{_MD}_SM-2">
                      <NumericArg RefId="{_MD}_SM-2_A-1" Value="1" BaseValue="{_MD}_A-1" />
                    </Module>
                  </when>
                </choose>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <Module Id="{_MD}_M-1" RefId="{_MD}"><NumericArg RefId="{_MD}_A-1" Value="2" /></Module>
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


def _device(application: Application, values: dict[str, str]) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_mode_places_the_submodule_setting_behind_the_channel(
    application: Application,
) -> None:
    # The channel at 2, its submodule at 2 + 1.
    assert _device(application, {_MODE: "1", _WIDE: "7"})[_SEGMENT][3] == 7
    assert _device(application, {_NARROW: "1"})[_SEGMENT][3] == 1


def test_the_selector_around_a_submodule_belongs_to_the_enclosing_instance(
    application: Application,
) -> None:
    ui = seed_dynamic_ui(application, {})
    assert ui is not None
    local = ui.local_ref_id(_NARROW)
    assert _gates_of(ui, _NARROW) == {_MODE}
    assert _module_gates_of(ui, (f"{_DISPLAY}_SM-1", local[: local.index("_P-")])) == {
        _MODE
    }


def test_the_memory_tells_the_mode_of_the_channel(application: Application) -> None:
    recovered = recover_parameters(
        application, _device(application, {_MODE: "1", _WIDE: "7"})
    )
    assert recovered.complete
    assert recovered.values[_MODE] == "1"
    assert recovered.values[_WIDE] == "7"


def test_a_byte_the_display_holds_keeps_the_default_mode(
    application: Application,
) -> None:
    recovered = recover_parameters(application, _device(application, {_NARROW: "1"}))
    assert recovered.complete
    assert recovered.values.get(_MODE, "0") == "0"
    assert recovered.values[_NARROW] == "1"


def test_the_instance_a_module_is_placed_in() -> None:
    # A module of the application, one of a submodule in a module instance, and one in another
    # submodule's instance.
    assert _enclosing_instance(f"{_MD}_M-1", _MD) is None
    assert _enclosing_instance(f"{_CHANNEL}_SM-1_M-1", f"{_MD}_SM-1") == (_CHANNEL, _MD)
    assert _enclosing_instance(
        f"{_CHANNEL}_SM-2_M-1_MI-1-1_SM-1_M-1", f"{_MD}_SM-1"
    ) == (
        f"{_CHANNEL}_SM-2_M-1_MI-1-1_SM-2",
        f"{_MD}_SM-2",
    )

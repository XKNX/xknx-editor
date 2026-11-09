"""A shown setting the device keeps no copy of that a calculation turns into the hidden count of
a repeated module: the group objects the tables link tell it, though no instance there is yet
has their numbers.

"Number of channels" (no copy on the device) is computed into the hidden repeat count of the
channel module, one group object per channel. With one channel, the linked objects of the
second and the third have no reference at all: the repeat count of the channel's module, and
the setting it is computed from, make them.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    _shown_objects,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0007-01-0000"
_CHANNELS = f"{_APP}_P-1_R-1"
_COUNT = f"{_APP}_P-2_R-2"
_CHANNEL = f"{_APP}_MD-1"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Calculated repeat count"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="1" maxInclusive="8" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Channels" ParameterType="{_APP}_PT-N" Text="Number of channels" Value="1" />
              <Parameter Id="{_APP}_P-2" Name="Count" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="1" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_CHANNELS}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_COUNT}" RefId="{_APP}_P-2" />
            </ParameterRefs>
            <ParameterCalculations>
              <ParameterCalculation Id="{_APP}_PC-1" Name="Count" Language="JavaScript">
                <LRTransformation>n = c;</LRTransformation>
                <LParameters><ParameterRefRef RefId="{_CHANNELS}" AliasName="c" /></LParameters>
                <RParameters><ParameterRefRef RefId="{_COUNT}" AliasName="n" /></RParameters>
              </ParameterCalculation>
            </ParameterCalculations>
            <Allocators>
              <Allocator Id="{_APP}_L-1" Name="Objects" Start="1" maxInclusive="9" />
            </Allocators>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_CHANNEL}" Name="Channel">
              <Arguments>
                <Argument Id="{_CHANNEL}_A-1" Name="ObjectBase" Allocates="1" />
              </Arguments>
              <Static>
                <ComObjects>
                  <ComObject Id="{_CHANNEL}_O-3-0" Name="Switch" Text="Switch" Number="0" FunctionText=""
                      ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
                      TransmitFlag="Disabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"
                      BaseNumber="{_CHANNEL}_A-1" />
                </ComObjects>
                <ComObjectRefs>
                  <ComObjectRef Id="{_CHANNEL}_O-3-0_R-1" RefId="{_CHANNEL}_O-3-0" />
                </ComObjectRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_CHANNEL}_PB-1" Text="Channel">
                  <ComObjectRefRef RefId="{_CHANNEL}_O-3-0_R-1" />
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_CHANNELS}" />
              </ParameterBlock>
              <Repeat Id="{_APP}_X-1" Name="Channels" ParameterRefId="{_COUNT}">
                <Module Id="{_CHANNEL}_M-1" RefId="{_CHANNEL}">
                  <NumericArg RefId="{_CHANNEL}_A-1" AllocatorRefId="{_APP}_L-1" />
                </Module>
              </Repeat>
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


def _objects(application: Application, channels: str) -> set[int]:
    ui = application.dynamic_ui()
    assert ui is not None
    ui.edit_parameter(_CHANNELS, channels)
    return _shown_objects(ui)


def test_each_channel_shows_its_object(application: Application) -> None:
    assert _objects(application, "1") == {1}
    assert _objects(application, "3") == {1, 2, 3}


def test_the_linked_objects_tell_the_number_of_channels(
    application: Application,
) -> None:
    recovered = recover_parameters(application, {}, linked_objects={1, 2, 3})
    assert recovered.complete
    assert recovered.values[_CHANNELS] == "3"
    ui = seed_dynamic_ui(application, recovered.values)
    assert ui is not None
    assert _shown_objects(ui) == {1, 2, 3}

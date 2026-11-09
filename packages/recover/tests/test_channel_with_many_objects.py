"""A channel the device keeps no switch of, told by its linked group objects however many
objects it shows without a link.

"Channel A" (kept nowhere) shows the channel's thirty group objects and its setting; the device
links two of the objects and holds a value at the setting's byte that the setting cannot take
(another setting the search has not found yet). Without the channel the result shows neither
linked object and writes nothing there, with it twenty-eight objects nobody linked and the
byte differs: a linked object the result does not show contradicts the device, objects shown
without a link do not.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0D09-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_CHANNEL = f"{_APP}_P-1_R-1"
_OBJECTS = range(1, 31)
_LINKED = {1, 3}


def _object(number: int) -> str:
    return (
        f'<ComObject Id="{_APP}_O-{number}" Name="O{number}" Text="Object {number}"'
        f' Number="{number}" FunctionText="" ObjectSize="2 Bytes" ReadFlag="Disabled"'
        ' WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled"'
        ' UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-9-1" />'
    )


_TABLE = "".join(_object(n) for n in _OBJECTS)
_REFS = "".join(
    f'<ComObjectRef Id="{_APP}_O-{n}_R-{n}" RefId="{_APP}_O-{n}" />' for n in _OBJECTS
)
_SHOWN = "".join(f'<ComObjectRefRef RefId="{_APP}_O-{n}_R-{n}" />' for n in _OBJECTS)
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="15" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Many objects"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code>
          <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment>
        </Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="ChannelA" ParameterType="{_APP}_PT-B" Text="Channel A" Value="0" />
          <Parameter Id="{_APP}_P-2" Name="Setting" ParameterType="{_APP}_PT-B" Text="Setting" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_CHANNEL}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_APP}_P-2_R-2" RefId="{_APP}_P-2" />
        </ParameterRefs>
        <ComObjectTable>{_TABLE}</ComObjectTable>
        <ComObjectRefs>{_REFS}</ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_CHANNEL}" />
            <choose ParamRefId="{_CHANNEL}">
              <when test="1"><ParameterRefRef RefId="{_APP}_P-2_R-2" />{_SHOWN}</when>
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


def _memory(application: Application) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, {_CHANNEL: "1"})
    assert ui is not None
    memory = {key: bytes(data) for key, data in ui.encode_to_memory().items()}
    memory[_SEGMENT] = b"\x05" + memory[_SEGMENT][1:]
    return memory


def test_the_linked_objects_keep_the_channel_on(application: Application) -> None:
    recovered = recover_parameters(
        application,
        _memory(application),
        linked_objects=_LINKED,
        object_sizes=dict.fromkeys(_OBJECTS, "2 Bytes"),
    )
    assert recovered.values[_CHANNEL] == "1"
    # Only the byte differs, every linked object shows.
    assert (recovered.differing_bytes, recovered.differing_objects) == (1, 0)


def test_without_links_the_channel_stays_off(application: Application) -> None:
    recovered = recover_parameters(
        application,
        _memory(application),
        linked_objects=set(),
        object_sizes=dict.fromkeys(_OBJECTS, "2 Bytes"),
    )
    assert recovered.differing_objects == 0
    assert recovered.values.get(_CHANNEL, "0") == "0"

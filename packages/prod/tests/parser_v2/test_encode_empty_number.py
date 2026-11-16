"""A number parameter declared without a value (hidden settings a manufacturer's plug-in fills
in) has no value to write: the memory keeps what the segment holds there, and the other
parameters are written as usual."""

from __future__ import annotations

import base64

from xknxeditor.prod.application import parse_application_xml

_APP = "M-00FA_A-00F5-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_DATA = base64.b64encode(bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])).decode()

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="245" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-0701" Name="Empty numbers"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="256" Size="6"><Data>{_DATA}</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N24" Name="N24"><TypeNumber SizeInBit="24" Type="signedInt" minInclusive="-8388608" maxInclusive="8388607" /></ParameterType>
          <ParameterType Id="{_APP}_PT-N8" Name="N8"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Start" ParameterType="{_APP}_PT-N24" Text="" Access="None" Value="">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-2" Name="Level" ParameterType="{_APP}_PT-N8" Text="Level" Value="7">
            <Memory CodeSegment="{_SEGMENT}" Offset="3" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_APP}_P-1_R-1" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_APP}_P-2_R-2" RefId="{_APP}_P-2" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_APP}_P-1_R-1" />
            <ParameterRefRef RefId="{_APP}_P-2_R-2" />
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def test_an_empty_number_keeps_the_segment_bytes() -> None:
    ui = parse_application_xml(_XML.encode(), "M-00FA")[0].dynamic_ui()
    assert ui is not None
    memory = ui.encode_to_memory()[_SEGMENT]
    assert memory[:3] == bytes([0x11, 0x22, 0x33])
    assert memory[3] == 7
    assert memory[4:6] == bytes([0x55, 0x66])

"""System B group communication tables whose application places them in relative segments: they
are built in the tables' relative memory, as without a code segment."""

from __future__ import annotations

from xknx.profile.const import ResourceObjectType

from xknxeditor.download.image import GroupCommunication, build_image
from xknxeditor.download.project_data import GroupObjectLink
from xknxeditor.prod.application import Application, parse_application_xml

_APP = "M-00FA_A-00F1-01-0000"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/11" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="241" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Relative tables"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="4.0">
      <Static>
        <Code>
          <RelativeSegment Id="{_APP}_RS-01-00000" Offset="0" Size="65536" LoadStateMachine="1" />
          <RelativeSegment Id="{_APP}_RS-02-00000" Offset="0" Size="65536" LoadStateMachine="2" />
          <RelativeSegment Id="{_APP}_RS-04-00000" Offset="0" Size="16" LoadStateMachine="4" />
        </Code>
        <AddressTable CodeSegment="{_APP}_RS-01-00000" Offset="0" MaxEntries="1023" />
        <AssociationTable CodeSegment="{_APP}_RS-02-00000" Offset="0" MaxEntries="1023" />
        <ComObjectTable>
          <ComObject Id="{_APP}_O-1" Name="O1" Text="Object 1" Number="1" FunctionText=""
              ObjectSize="1 Bit" Priority="Low" ReadFlag="Disabled" WriteFlag="Enabled"
              CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled"
              ReadOnInitFlag="Disabled" />
        </ComObjectTable>
        <ComObjectRefs><ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" /></ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ComObjectRefRef RefId="{_APP}_O-1_R-1" />
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_tables_in_relative_segments_are_built_in_relative_memory() -> None:
    image = build_image(
        _application(),
        group_communication=GroupCommunication(
            device_address=0x1101,
            links=(
                GroupObjectLink(
                    com_object_ref_id=f"{_APP}_O-1_R-1",
                    group_address=0x0801,
                    sending=True,
                ),
            ),
        ),
    )
    tables = {segment.object_type: segment.data for segment in image.relative_segments}
    # One group address.
    assert tables[ResourceObjectType.OBJECT_ADDRESS_TABLE] == bytes.fromhex("00010801")
    # One association: group address index 1, object 1.
    assert tables[ResourceObjectType.OBJECT_ASSOCIATION_TABLE] == bytes.fromhex(
        "00010101"
    )
    assert ResourceObjectType.OBJECT_GROUP_OBJECT_TABLE in tables
    # Nothing of the tables lands in standard memory.
    assert not image.segments

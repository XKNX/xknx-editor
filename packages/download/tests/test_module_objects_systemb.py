"""System B group communication tables of an application whose module instances number their
group objects after its own: the tables cover the instances' objects too."""

from __future__ import annotations

from xknx.profile.const import ResourceObjectType

from xknxeditor.download.image import GroupCommunication, build_image
from xknxeditor.download.project_data import GroupObjectLink
from xknxeditor.prod.application import Application, parse_application_xml

_APP = "M-00FA_A-00F0-01-0000"
# The instance's object 1, numbered from the instance's base 300.
_INSTANCE_OBJECT = f"{_APP}_MD-1_M-1_MI-1_O-2-1_R-1"


def _object(object_id: str, number: int, base: str = "") -> str:
    base_number = f' BaseNumber="{base}"' if base else ""
    return (
        f'<ComObject Id="{object_id}" Name="O{number}" Text="Object {number}" Number="{number}"'
        ' FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled"'
        ' CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled"'
        f' ReadOnInitFlag="Disabled"{base_number} />'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="240" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Module objects"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
        <ComObjectTable>{_object(f"{_APP}_O-1", 1)}</ComObjectTable>
        <ComObjectRefs><ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" /></ComObjectRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_APP}_MD-1" Name="Channel">
          <Arguments><Argument Id="{_APP}_MD-1_A-1" Name="ObjectBase" /></Arguments>
          <Static>
            <ComObjects>{_object(f"{_APP}_MD-1_O-2-1", 1, f"{_APP}_MD-1_A-1")}</ComObjects>
            <ComObjectRefs>
              <ComObjectRef Id="{_APP}_MD-1_O-2-1_R-1" RefId="{_APP}_MD-1_O-2-1" />
            </ComObjectRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Channel">
              <ComObjectRefRef RefId="{_APP}_MD-1_O-2-1_R-1" />
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ComObjectRefRef RefId="{_APP}_O-1_R-1" />
          </ParameterBlock>
        </ChannelIndependentBlock>
        <Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1" Name="Channel 1">
          <NumericArg RefId="{_APP}_MD-1_A-1" Value="300" />
        </Module>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _tables() -> dict[int, bytes]:
    image = build_image(
        _application(),
        group_communication=GroupCommunication(
            device_address=0x1101,
            links=(
                GroupObjectLink(
                    com_object_ref_id=_INSTANCE_OBJECT,
                    group_address=0x0801,
                    sending=True,
                ),
            ),
        ),
    )
    return {segment.object_type: segment.data for segment in image.relative_segments}


def test_a_module_instance_object_above_255_is_linked_in_the_wide_format() -> None:
    association = _tables()[ResourceObjectType.OBJECT_ASSOCIATION_TABLE]
    # One entry, two octets per field: group address index 1, object 301.
    assert association == bytes.fromhex("0001") + bytes.fromhex("0001012d")


def test_the_group_object_table_reaches_the_module_instance_objects() -> None:
    table = _tables()[ResourceObjectType.OBJECT_GROUP_OBJECT_TABLE]
    assert int.from_bytes(table[:2], "big") == 301
    # The instance's object has its record (flags, size code), the numbers between are empty.
    assert table[2 + 2 * 300 : 2 + 2 * 301] != b"\x00\x00"
    assert table[2 + 2 * 1 : 2 + 2 * 300] == bytes(2 * 299)

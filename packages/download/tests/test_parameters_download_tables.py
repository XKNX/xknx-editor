"""A partial download writes its part only: the parameters or the group communication.

The tables are formatted only for a download that loads the group communication: one of
the parameters alone leaves them as the device holds them, the object count of the group
object table too, which a full download writes from the product data and a download of
the group communication as the number of objects (issue #283: Eelectron BCU2 devices
whose product data counts more records than the application has objects). The
parameters are applied only for a download that loads them: one of the group
communication alone writes no parameter octet.
"""

from __future__ import annotations

import base64

import pytest

from xknxeditor.download.image import DownloadImage, GroupCommunication, build_image
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.project_data import GroupObjectLink
from xknxeditor.download.scope import DownloadScope
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml

from .conftest import FakeDevice

_APP = "M-00FA_A-2000-10-0001"
# The group object table's segment: the count (5 in the product data, 3 objects), the RAM
# flags pointer, five 3-octet records and, at offset 18, a parameter.
_TABLE = 0x0156
_PRODUCT_COUNT = 5
_OBJECTS = 3
_PARAMETER = _TABLE + 18
_TABLE_DATA = bytes.fromhex("0540" + "50df0751df0752df0753df0754df07000000")


def _com_object(number: int) -> str:
    return (
        f'<ComObject Id="{_APP}_O-{number}" Name="Object {number}" Text="Object {number}" '
        f'Number="{number}" FunctionText="Switch" ObjectSize="1 Bit" ReadFlag="Disabled" '
        'WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Disabled" '
        'UpdateFlag="Disabled" ReadOnInitFlag="Disabled" Priority="Low" />'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="8192" ApplicationVersion="16"
            ProgramType="ApplicationProgram" MaskVersion="MV-0020" Name="BCU2 Sample"
            LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0100" Address="256" Size="22">
                <Data>{base64.b64encode(bytes(22)).decode()}</Data>
              </AbsoluteSegment>
              <AbsoluteSegment Id="{_APP}_AS-0116" Address="278" Size="32" />
              <AbsoluteSegment Id="{_APP}_AS-0136" Address="310" Size="32" />
              <AbsoluteSegment Id="{_APP}_AS-0156" Address="{_TABLE}" Size="20">
                <Data>{
    base64.b64encode(_TABLE_DATA + bytes(20 - len(_TABLE_DATA))).decode()
}</Data>
              </AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-Byte" Name="Byte">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Value" ParameterType="{_APP}_PT-Byte"
                  Text="Value" Access="ReadWrite" Value="42">
                <Memory CodeSegment="{_APP}_AS-0156" Offset="18" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_APP}_P-1_R-1" RefId="{_APP}_P-1" />
            </ParameterRefs>
            <ComObjectTable CodeSegment="{_APP}_AS-0156" Offset="0">
              {"".join(_com_object(number) for number in range(_OBJECTS))}
            </ComObjectTable>
            <ComObjectRefs>
              {
    "".join(
        f'<ComObjectRef Id="{_APP}_O-{n}_R-1" RefId="{_APP}_O-{n}" />'
        for n in range(_OBJECTS)
    )
}
            </ComObjectRefs>
            <AddressTable CodeSegment="{_APP}_AS-0116" Offset="0" MaxEntries="10" />
            <AssociationTable CodeSegment="{_APP}_AS-0136" Offset="0" MaxEntries="10" />
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Name="General" Text="General">
                <ParameterRefRef RefId="{_APP}_P-1_R-1" />
                {
    "".join(f'<ComObjectRefRef RefId="{_APP}_O-{n}_R-1" />' for n in range(_OBJECTS))
}
              </ParameterBlock>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _image(application: Application) -> DownloadImage:
    return build_image(
        application,
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


def _written(scope: DownloadScope) -> dict[int, int]:
    application = _application()
    runner = LoadProcedureRunner(
        application,
        _image(application),
        DeviceProgrammer(FakeDevice()),
        controls=[],
        scope=scope,
    )
    runs = runner._memory_runs(_TABLE, 20) or []
    return {
        address + offset: value
        for address, data in runs
        for offset, value in enumerate(data)
    }


def test_the_group_object_table_is_a_group_table() -> None:
    # The code segment with the parameter, and the table built over it.
    image = _image(_application())
    code, table = (segment for segment in image.segments if segment.address == _TABLE)
    assert not code.group_table
    assert table.group_table
    assert table.partial_octets == ((0, _OBJECTS),)


@pytest.mark.parametrize(
    ("scope", "count", "parameter"),
    [
        (DownloadScope.FULL, _PRODUCT_COUNT, True),
        (DownloadScope.APPLICATION, _PRODUCT_COUNT, True),
        (DownloadScope.GROUP_COMMUNICATION, _OBJECTS, False),
        (DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION, _OBJECTS, True),
    ],
)
def test_a_download_with_the_group_communication_writes_the_table(
    scope: DownloadScope, count: int, parameter: bool
) -> None:
    # A download of the group communication alone writes no parameter.
    written = _written(scope)
    assert written[_TABLE] == count
    # Object 1 is linked: its flag byte has the communication bit.
    assert written[_TABLE + 2 + 3 + 1] & 0x04
    assert (_PARAMETER in written) is parameter


def test_a_parameters_download_writes_the_parameters_only() -> None:
    written = _written(DownloadScope.PARAMETERS)
    assert written == {_PARAMETER: 42}

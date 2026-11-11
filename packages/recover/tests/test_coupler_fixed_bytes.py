"""A line coupler's segment at 0100h gets fixed bytes with its data where no setting writes
them (at 010Dh and from 0112h on). A union member stored there that is hidden leaves the fixed
byte on the device: the read fits a result that hides it, and one shown there is still read.
"""

from __future__ import annotations

import pytest

from xknxeditor.download.image import build_image
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.read_config import read_parameter_memory
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0011-01-0000"
_SEGMENT = f"{_APP}_AS-0100"
_MODE = f"{_APP}_P-1_R-1"
_VALUE = f"{_APP}_UP-2_R-2"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="17" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-0911" Name="Coupler"
            LoadProcedureStyle="DefaultProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="256" Size="256" />
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-B" Text="Mode" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
              </Parameter>
              <Union SizeInBit="8">
                <Memory CodeSegment="{_SEGMENT}" Offset="18" BitOffset="0" />
                <Parameter Id="{_APP}_UP-2" Name="Value" ParameterType="{_APP}_PT-B" Offset="0" BitOffset="0" Text="Value" Value="0" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MODE}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_VALUE}" RefId="{_APP}_UP-2" />
            </ParameterRefs>
            <Options DownloadInvisibleParameters="None" />
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_MODE}" />
                <choose ParamRefId="{_MODE}">
                  <when test="1">
                    <ParameterRefRef RefId="{_VALUE}" />
                  </when>
                </choose>
              </ParameterBlock>
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
    """The segment a full download of ``values`` leaves on the device."""
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    (memory,) = build_image(application, ui=ui).segments
    return {_SEGMENT: bytes(memory.data)}


def test_the_fixed_byte_where_a_hidden_setting_is_stored_fits(
    application: Application,
) -> None:
    device = _device(application, {_MODE: "0"})
    assert device[_SEGMENT][0x12] == 0xC3
    recovered = recover_parameters(application, device)
    assert recovered.complete
    assert recovered.differing_bytes == 0
    assert recovered.values[_MODE] == "0"


def test_a_shown_setting_there_is_read(application: Application) -> None:
    device = _device(application, {_MODE: "1", _VALUE: "18"})
    recovered = recover_parameters(application, device)
    assert recovered.complete
    assert recovered.values[_MODE] == "1"
    assert recovered.values[_VALUE] == "18"


def test_another_value_there_still_differs(application: Application) -> None:
    device = _device(application, {_MODE: "0"})
    device[_SEGMENT] = device[_SEGMENT][:0x12] + bytes([0x44]) + device[_SEGMENT][0x13:]
    recovered = recover_parameters(application, device)
    # Neither the fixed byte nor a value the hidden setting is written with: the mode the
    # device holds hides it all the same, and the byte still differs.
    assert recovered.values[_MODE] == "0"
    assert not recovered.complete
    assert recovered.differing_bytes == 1


class _Programmer:
    def __init__(self, memory: bytes) -> None:
        self.memory = memory

    async def read_memory(
        self, address: int, size: int, *, user: bool = False
    ) -> bytes:
        assert not user
        return self.memory[address - 0x100 : address - 0x100 + size]


async def test_a_coupler_without_an_address_table_has_its_segment_read(
    application: Application,
) -> None:
    device = _device(application, {_MODE: "1", _VALUE: "18"})
    read = await read_parameter_memory(
        _Programmer(device[_SEGMENT]),  # type: ignore[arg-type]
        application,
    )
    assert read == device

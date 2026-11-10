"""A setting stored in a property that is hidden: a download writes the property's base there
(zeros, as the device has no factory contents), not the setting's default. A device holding
zeros there fits a result that hides the setting; one holding another value than zeros or the
default still tells the setting was shown.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.encode import TypedPropertyKey
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0009-01-0000"
_CHANNEL = f"{_APP}_P-1_R-1"
_DELAY = f"{_APP}_P-2_R-2"
_KEY: TypedPropertyKey = (6, None, 52, 0)
_SHOW = f"{_APP}_P-3_R-3"
_LEVEL = f"{_APP}_P-4_R-4"
_FLAG = f"{_APP}_P-5_R-5"
_SHARED: TypedPropertyKey = (6, None, 53, 0)
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="9" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-0701" Name="Property base"
            LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="4.0">
          <Static>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-D" Name="D">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="60" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-L" Name="L">
                <TypeNumber SizeInBit="7" Type="unsignedInt" minInclusive="0" maxInclusive="100" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-F" Name="F">
                <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Channel" ParameterType="{_APP}_PT-B" Text="Channel" Value="0">
                <Property ObjectIndex="6" PropertyId="52" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-2" Name="Delay" ParameterType="{_APP}_PT-D" Text="Delay" Value="5">
                <Property ObjectIndex="6" PropertyId="52" Offset="1" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-3" Name="ShowFlag" ParameterType="{_APP}_PT-B" Text="Use the flag" Value="0" />
              <Parameter Id="{_APP}_P-4" Name="Level" ParameterType="{_APP}_PT-L" Text="Level" Value="10">
                <Property ObjectIndex="6" PropertyId="53" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-5" Name="Flag" ParameterType="{_APP}_PT-F" Text="Flag" Value="0">
                <Property ObjectIndex="6" PropertyId="53" Offset="0" BitOffset="7" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_CHANNEL}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-2" />
              <ParameterRef Id="{_SHOW}" RefId="{_APP}_P-3" />
              <ParameterRef Id="{_LEVEL}" RefId="{_APP}_P-4" />
              <ParameterRef Id="{_FLAG}" RefId="{_APP}_P-5" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_CHANNEL}" />
                <choose ParamRefId="{_CHANNEL}">
                  <when test="1">
                    <ParameterRefRef RefId="{_DELAY}" />
                  </when>
                </choose>
                <ParameterRefRef RefId="{_LEVEL}" />
                <ParameterRefRef RefId="{_SHOW}" />
                <choose ParamRefId="{_SHOW}">
                  <when test="1">
                    <ParameterRefRef RefId="{_FLAG}" />
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


def _device(application: Application, values: dict[str, str]) -> bytes:
    """The property a device holds after a download of ``values``: the bytes the parameters
    cover are written, those only inactive parameters cover with the base (zeros)."""
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    written = ui.encode_to_typed_properties_masked()[_KEY]
    return bytes(
        (data & mask) if covered else 0
        for data, mask, covered in zip(
            written.data, written.mask, written.covered, strict=True
        )
    )


def test_the_hidden_setting_is_downloaded_as_the_base(
    application: Application,
) -> None:
    assert _device(application, {}) == b"\x00\x00"
    assert _device(application, {_CHANNEL: "1", _DELAY: "7"}) == b"\x01\x07"


def test_the_base_where_a_hidden_setting_is_stored_fits(
    application: Application,
) -> None:
    recovered = recover_parameters(application, {}, {_KEY: _device(application, {})})
    assert recovered.complete
    assert recovered.differing_bytes == 0
    assert recovered.values.get(_CHANNEL, "0") == "0"


def test_the_default_where_a_hidden_setting_is_stored_fits_too(
    application: Application,
) -> None:
    # A device that holds its default there (a property no download wrote yet).
    recovered = recover_parameters(application, {}, {_KEY: b"\x00\x05"})
    assert recovered.complete
    assert recovered.values.get(_CHANNEL, "0") == "0"


def test_a_shown_setting_is_read(application: Application) -> None:
    device = _device(application, {_CHANNEL: "1", _DELAY: "7"})
    recovered = recover_parameters(application, {}, {_KEY: device})
    assert recovered.complete
    assert recovered.values[_CHANNEL] == "1"
    assert recovered.values[_DELAY] == "7"


def test_another_value_where_a_hidden_setting_is_stored_differs(
    application: Application,
) -> None:
    # Neither the base nor the default: no result that hides the delay fits, and the switch
    # is stored as off.
    recovered = recover_parameters(application, {}, {_KEY: b"\x00\x07"})
    assert not recovered.complete
    assert recovered.differing_bytes == 1


def test_a_hidden_setting_beside_a_shown_one_in_its_byte_is_compared(
    application: Application,
) -> None:
    # The level takes the high bits of the byte, the flag its lowest bit: with the flag shown
    # and on, the level alone does not give the byte back.
    ui = seed_dynamic_ui(application, {_SHOW: "1", _LEVEL: "20", _FLAG: "1"})
    assert ui is not None
    written = ui.encode_to_typed_properties_masked()[_SHARED]
    assert written.data == bytes([41])
    recovered = recover_parameters(application, {}, {_SHARED: bytes([41])})
    assert recovered.complete
    assert recovered.values[_LEVEL] == "20"
    assert recovered.values[_SHOW] == "1"
    assert recovered.values[_FLAG] == "1"


def test_the_lowest_bit_at_its_default_leaves_the_flag_hidden(
    application: Application,
) -> None:
    recovered = recover_parameters(application, {}, {_SHARED: bytes([40])})
    assert recovered.complete
    assert recovered.values[_LEVEL] == "20"
    assert recovered.values.get(_SHOW, "0") == "0"

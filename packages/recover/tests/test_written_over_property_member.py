"""A union member stored in a property that a later member writes over in part is read from the
property the device holds: "Level" takes the whole byte, "Flag" (written after it) its top
bit. Only a value whose bytes, written with the flag the device holds, are those of the
property is taken, not the first value tried.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.encode import TypedPropertyKey
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0009-01-0000"
_LEVEL = f"{_APP}_UP-1_R-1"
_FLAG = f"{_APP}_UP-2_R-2"
_KEY: TypedPropertyKey = (None, 60001, 57, 0)
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="9" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-57B0" Name="Property union"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-L" Name="L">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="10" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-F" Name="F">
                <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Union SizeInBit="8">
                <Property ObjectType="60001" PropertyId="57" Offset="0" BitOffset="0" />
                <Parameter Id="{_APP}_UP-1" Name="Level" ParameterType="{_APP}_PT-L" Text="Level" Value="0" Offset="0" BitOffset="0" />
                <Parameter Id="{_APP}_UP-2" Name="Flag" ParameterType="{_APP}_PT-F" Text="Flag" Value="0" Offset="0" BitOffset="0" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_LEVEL}" RefId="{_APP}_UP-1" />
              <ParameterRef Id="{_FLAG}" RefId="{_APP}_UP-2" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_LEVEL}" />
                <ParameterRefRef RefId="{_FLAG}" />
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


def _properties(
    application: Application, values: dict[str, str]
) -> dict[TypedPropertyKey, bytes]:
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    return ui.encode_to_typed_properties()


def test_the_device_holds_both_members(application: Application) -> None:
    assert _properties(application, {_LEVEL: "5", _FLAG: "1"})[_KEY] == b"\x85"


def test_the_member_written_over_is_read_from_the_property(
    application: Application,
) -> None:
    device = _properties(application, {_LEVEL: "5", _FLAG: "1"})
    recovered = recover_parameters(application, {}, property_values=device)
    assert recovered.values[_FLAG] == "1"
    assert recovered.values[_LEVEL] == "5"
    assert _properties(application, dict(recovered.values))[_KEY] == device[_KEY]

"""Hidden settings stored in properties the load procedure writes: the download writes the
property's base (zeros, or the factory contents with the product's factory image) where a
hidden setting is stored, not the setting's default. A device holding the base there fits a
result that hides the setting; one holding the default does not: the setting was shown.

"Delay" (property 52, byte 1) is shown when "Channel" (byte 0) is 1; "Zone" (property 53) is
shown by "Overwrite", a switch the device keeps no copy of.
"""

from __future__ import annotations

from xknxeditor.download.factory_image import (
    FactoryImage,
    FactoryLocation,
    FactorySegment,
)
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.encode import TypedPropertyKey
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-000A-01-0000"
_CHANNEL = f"{_APP}_P-1_R-1"
_DELAY = f"{_APP}_P-2_R-2"
_OVERWRITE = f"{_APP}_P-3_R-3"
_ZONE = f"{_APP}_P-4_R-4"
_DELAYS: TypedPropertyKey = (6, None, 52, 0)
_ZONES: TypedPropertyKey = (6, None, 53, 0)
# The time zone's default, 26757.
_ZONE_DEFAULT = (26757).to_bytes(2, "big")
_WRITES = (
    '<LdCtrlWriteProp ObjIdx="6" PropId="52" StartElement="1" Count="1" Verify="false" />'
    '<LdCtrlWriteProp ObjIdx="6" PropId="53" StartElement="1" Count="1" Verify="false" />'
)


def _application(writes: str = _WRITES) -> Application:
    """The application, with ``writes`` in its load procedure."""
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="10" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-0701" Name="Written property base"
        LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="4.0">
      <Static>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
          <ParameterType Id="{_APP}_PT-D" Name="D"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="60" /></ParameterType>
          <ParameterType Id="{_APP}_PT-Z" Name="Z"><TypeNumber SizeInBit="16" Type="unsignedInt" minInclusive="0" maxInclusive="65535" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Channel" ParameterType="{_APP}_PT-B" Text="Channel" Value="0">
            <Property ObjectIndex="6" PropertyId="52" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-2" Name="Delay" ParameterType="{_APP}_PT-D" Text="Delay" Value="5">
            <Property ObjectIndex="6" PropertyId="52" Offset="1" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-3" Name="Overwrite" ParameterType="{_APP}_PT-B" Text="Overwrite" Value="0" />
          <Parameter Id="{_APP}_P-4" Name="Zone" ParameterType="{_APP}_PT-Z" Text="Time zone" Value="26757">
            <Property ObjectIndex="6" PropertyId="53" Offset="0" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_CHANNEL}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_OVERWRITE}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_ZONE}" RefId="{_APP}_P-4" />
        </ParameterRefs>
        <LoadProcedures>
          <LoadProcedure>
            <LdCtrlConnect />
            {writes}
            <LdCtrlRestart />
          </LoadProcedure>
        </LoadProcedures>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_CHANNEL}" />
            <choose ParamRefId="{_CHANNEL}">
              <when test="1"><ParameterRefRef RefId="{_DELAY}" /></when>
            </choose>
            <ParameterRefRef RefId="{_OVERWRITE}" />
            <choose ParamRefId="{_OVERWRITE}">
              <when test="1"><ParameterRefRef RefId="{_ZONE}" /></when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def _factory(data: bytes) -> FactoryImage:
    """A factory image holding ``data`` in property 52 of object 6 (the first application
    object after the five system objects of the mask)."""
    return FactoryImage(
        segments=(
            FactorySegment(
                location=FactoryLocation.APP_PROPERTY,
                object_ref=1,
                property_id=_DELAYS[2],
                address=0,
                size=len(data),
                data=data,
                mask=None,
            ),
        )
    )


def test_the_base_where_a_hidden_setting_is_stored_fits() -> None:
    recovered = recover_parameters(
        _application(), {}, {_DELAYS: b"\x00\x00", _ZONES: b"\x00\x00"}
    )
    assert recovered.complete
    assert recovered.values.get(_CHANNEL, "0") == "0"
    assert recovered.values.get(_OVERWRITE, "0") == "0"


def test_the_default_where_a_hidden_setting_is_stored_differs() -> None:
    # The channel as the device holds it hides the delay: the download writes zeros there.
    recovered = recover_parameters(
        _application(), {}, {_DELAYS: b"\x00\x05", _ZONES: b"\x00\x00"}
    )
    assert not recovered.complete
    assert recovered.differing_bytes == 1


def test_the_default_tells_the_switch_that_showed_the_setting() -> None:
    recovered = recover_parameters(
        _application(), {}, {_DELAYS: b"\x00\x00", _ZONES: _ZONE_DEFAULT}
    )
    assert recovered.complete
    assert recovered.values[_OVERWRITE] == "1"
    assert recovered.values[_ZONE] == "26757"


def test_the_factory_contents_are_the_base() -> None:
    application = _application()
    factory = _factory(b"\x00\x09")
    fits = recover_parameters(
        application,
        {},
        {_DELAYS: b"\x00\x09", _ZONES: b"\x00\x00"},
        factory_image=factory,
    )
    assert fits.complete
    assert fits.values.get(_CHANNEL, "0") == "0"
    zeros = recover_parameters(
        application,
        {},
        {_DELAYS: b"\x00\x00", _ZONES: b"\x00\x00"},
        factory_image=factory,
    )
    assert not zeros.complete
    assert zeros.differing_bytes == 1


def test_a_property_no_control_writes_keeps_the_default_too() -> None:
    # Not written, the device keeps its own value: the default fits a hidden setting.
    recovered = recover_parameters(
        _application(""), {}, {_DELAYS: b"\x00\x05", _ZONES: _ZONE_DEFAULT}
    )
    assert recovered.complete
    assert recovered.values.get(_CHANNEL, "0") == "0"

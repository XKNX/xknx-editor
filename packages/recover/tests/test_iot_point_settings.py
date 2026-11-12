"""Settings a device keeps as IoT points are inferred like settings it keeps no copy of.

A parameter with an ``IoTPoint`` has no memory and no property: a memory and property read does
not give it. Read back, it would stay at its default; with the group objects the device's tables
link, it takes the value that shows them.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    _is_unstored,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)

_APP = "M-00FA_A-0005-01-0000"
_OUTPUT = f"{_APP}_P-1_R-1"
_PERIOD = f"{_APP}_P-2_R-2"
_DELAY = f"{_APP}_P-3_R-3"


def _object(number: int) -> str:
    return (
        f'<ComObject Id="{_APP}_O-{number}" Name="O{number}" Text="Object {number}"'
        f' Number="{number}" FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled"'
        ' WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled"'
        ' UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" />'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/23" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="5" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="IoT points"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="6.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-O" Name="O">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="1" maxInclusive="3" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="16" Type="unsignedInt" minInclusive="0" maxInclusive="65535" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Output" ParameterType="{_APP}_PT-O" Text="Output" Value="1">
                <IoTPoint PointReference="/p/1" />
              </Parameter>
              <Parameter Id="{_APP}_P-2" Name="Period" ParameterType="{_APP}_PT-N" Text="Period" Value="900">
                <IoTPoint PointReference="/p/2" />
              </Parameter>
              <Parameter Id="{_APP}_P-3" Name="Delay" ParameterType="{_APP}_PT-O" Text="Delay" Value="1">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_OUTPUT}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_PERIOD}" RefId="{_APP}_P-2" />
              <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-3" />
            </ParameterRefs>
            <ComObjectTable>
              {_object(1)}
              {_object(2)}
              {_object(3)}
            </ComObjectTable>
            <ComObjectRefs>
              <ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" />
              <ComObjectRef Id="{_APP}_O-2_R-2" RefId="{_APP}_O-2" />
              <ComObjectRef Id="{_APP}_O-3_R-3" RefId="{_APP}_O-3" />
            </ComObjectRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_PERIOD}" />
                <ParameterRefRef RefId="{_DELAY}" />
                <ParameterRefRef RefId="{_OUTPUT}" />
                <choose ParamRefId="{_OUTPUT}">
                  <when test="1"><ComObjectRefRef RefId="{_APP}_O-1_R-1" /></when>
                  <when test="2"><ComObjectRefRef RefId="{_APP}_O-2_R-2" /></when>
                  <when test="3"><ComObjectRefRef RefId="{_APP}_O-3_R-3" /></when>
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


def _device(application: Application, **values: str) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id -> value)."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values.items():
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


def test_a_setting_kept_as_an_iot_point_is_unstored(application: Application) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    assert _is_unstored(ui, _OUTPUT)
    assert _is_unstored(ui, _PERIOD)
    assert not _is_unstored(ui, _DELAY)


def test_linked_objects_pick_a_setting_kept_as_an_iot_point(
    application: Application,
) -> None:
    segments = _device(application, **{_OUTPUT: "3", _DELAY: "2"})
    recovered = recover_parameters(application, segments, linked_objects={3})
    assert recovered.values[_OUTPUT] == "3"
    assert recovered.values[_DELAY] == "2"
    assert recovered.complete
    # Without a link nothing tells the output, and it keeps its default.
    plain = recover_parameters(application, segments)
    assert plain.values.get(_OUTPUT, "1") == "1"

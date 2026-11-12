"""A property no control of the load procedure writes keeps the device's own value (its
hardware type): whatever a download writes there, the device holds what it came with. Its
bytes do not count as differing, so a result that fits the memory is complete, however the
device's own value differs; between results that fit alike, the one matching it wins.

"Variant" is a setting the device keeps no copy of that assigns the hardware type in byte 0
of property 78 of the device object, "Mode" one in its memory that assigns a hidden setting
in byte 1 of it.
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.encode import TypedPropertyKey
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F7-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_VARIANT = f"{_APP}_P-1_R-1"
_MODE = f"{_APP}_P-2_R-2"
_TYPE = f"{_APP}_P-3_R-3"
_HIDDEN = f"{_APP}_P-4_R-4"
_KEY: TypedPropertyKey = (0, None, 78, 0)
# A control that writes another property of the device object.
_OTHER_WRITE = (
    '<LdCtrlWriteProp ObjIdx="0" PropId="60" Verify="false" InlineData="01" />'
)


def _xml(writes: str) -> str:
    """The application, with ``writes`` in its load procedure."""

    def assigns(target: str, values: tuple[int, ...]) -> str:
        return "".join(
            f'<when test="{n}"><Assign TargetParamRefRef="{target}" Value="{value}" /></when>'
            for n, value in enumerate(values)
        )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="247" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Unwritten property"
        LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
          <ParameterType Id="{_APP}_PT-V" Name="V"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="2" /></ParameterType>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Variant" ParameterType="{_APP}_PT-V" Text="Variant" Value="0" />
          <Parameter Id="{_APP}_P-2" Name="Mode" ParameterType="{_APP}_PT-B" Text="Mode" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-3" Name="Type" ParameterType="{_APP}_PT-N" Text="Type" Value="16" Access="None">
            <Property ObjectIndex="0" PropertyId="78" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-4" Name="Hidden" ParameterType="{_APP}_PT-N" Text="Hidden" Value="1" Access="None">
            <Property ObjectIndex="0" PropertyId="78" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_VARIANT}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_MODE}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_TYPE}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_HIDDEN}" RefId="{_APP}_P-4" />
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
            <ParameterRefRef RefId="{_VARIANT}" />
            <ParameterRefRef RefId="{_MODE}" />
            <ParameterRefRef RefId="{_TYPE}" />
            <ParameterRefRef RefId="{_HIDDEN}" />
            <choose ParamRefId="{_VARIANT}">{assigns(_TYPE, (16, 32, 48))}</choose>
            <choose ParamRefId="{_MODE}">{assigns(_HIDDEN, (1, 2))}</choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application(writes: str = _OTHER_WRITE) -> Application:
    return parse_application_xml(_xml(writes).encode(), "M-00FA")[0]


def test_the_layout_keeps_both_settings_in_the_property() -> None:
    ui = seed_dynamic_ui(_application(), {_VARIANT: "2", _MODE: "1"})
    assert ui is not None
    written = ui.encode_to_typed_properties_masked()[_KEY]
    assert written.data == b"\x30\x02"
    assert ui.encode_to_memory()[_SEGMENT] == b"\x01"


def test_a_device_holding_its_own_value_where_no_download_writes_is_complete() -> None:
    # Mode 1 assigns 2 to the hidden setting, but the device keeps its own 1 there: no result
    # gives both the memory and the property.
    recovered = recover_parameters(
        _application(), {_SEGMENT: b"\x01"}, {_KEY: b"\x10\x01"}
    )
    assert recovered.values[_MODE] == "1"
    assert recovered.complete
    assert recovered.differing_bytes == 0


def test_the_variant_the_unwritten_property_holds_is_chosen() -> None:
    # Every variant fits the memory alike; the hardware type the device holds tells it.
    recovered = recover_parameters(
        _application(), {_SEGMENT: b"\x01"}, {_KEY: b"\x30\x01"}
    )
    assert recovered.values[_VARIANT] == "2"
    assert recovered.values[_MODE] == "1"
    assert recovered.complete


def test_a_property_a_load_control_writes_still_differs() -> None:
    application = _application(
        '<LdCtrlWriteProp ObjIdx="0" PropId="78" StartElement="1" Count="1" Verify="false" />'
    )
    recovered = recover_parameters(
        application, {_SEGMENT: b"\x01"}, {_KEY: b"\x10\x01"}
    )
    assert recovered.values[_MODE] == "1"
    assert not recovered.complete
    assert recovered.differing_bytes == 1


def test_a_procedure_that_compares_the_property_counts_as_writing_it() -> None:
    application = _application(
        '<LdCtrlCompareProp ObjIdx="0" PropId="78" StartElement="1" Count="1"'
        ' InlineData="0000" />'
    )
    recovered = recover_parameters(
        application, {_SEGMENT: b"\x01"}, {_KEY: b"\x10\x01"}
    )
    assert not recovered.complete
    assert recovered.differing_bytes == 1

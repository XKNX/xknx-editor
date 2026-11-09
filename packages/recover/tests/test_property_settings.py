"""Settings only the device's interface object properties tell: a switch the device keeps no
copy of shows a time zone stored in a property, and in a module a filter the device keeps no
copy of assigns the value of a property. The properties read count like the memory: a result
that writes another value there, or leaves the default where the device holds another one,
differs from the device, and the search tries the switches that change it.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.encode import TypedPropertyKey
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0008-01-0000"
_OVERWRITE = f"{_APP}_P-6_R-6"
_ZONE = f"{_APP}_P-5_R-5"
_INSTANCE = f"{_APP}_MD-1_M-1_MI-1"
_FILTER = f"{_INSTANCE}_P-4_R-4"
_ASSIGNED = f"{_INSTANCE}_UP-6_R-6"
_ZONE_KEY: TypedPropertyKey = (None, 60001, 57, 0)
_FILTER_KEY: TypedPropertyKey = (None, 50001, 51, 0)
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="8" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-57B0" Name="Property settings"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-F" Name="F">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="2" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Z" Name="Z">
                <TypeNumber SizeInBit="16" Type="unsignedInt" minInclusive="0" maxInclusive="65535" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-5" Name="Zone" ParameterType="{_APP}_PT-Z" Text="Time zone" Value="26757">
                <Property ObjectType="60001" PropertyId="57" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-6" Name="Overwrite" ParameterType="{_APP}_PT-B" Text="Overwrite the settings of the device website" Value="0" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_ZONE}" RefId="{_APP}_P-5" />
              <ParameterRef Id="{_OVERWRITE}" RefId="{_APP}_P-6" />
            </ParameterRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Notification">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Occurrence" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-4" Name="Filter" ParameterType="{_APP}_PT-F" Text="Filter" Value="0" />
                  <Union SizeInBit="8">
                    <Property ObjectType="50001" PropertyId="51" Offset="0" BitOffset="0" BaseOccurrence="{_APP}_MD-1_A-1" />
                    <Parameter Id="{_APP}_MD-1_UP-6" Name="Filter value" ParameterType="{_APP}_PT-F" Text="Filter" Value="0" Offset="0" BitOffset="0" />
                  </Union>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-4_R-4" RefId="{_APP}_MD-1_P-4" />
                  <ParameterRef Id="{_APP}_MD-1_UP-6_R-6" RefId="{_APP}_MD-1_UP-6" />
                </ParameterRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Notification">
                  <ParameterRefRef RefId="{_APP}_MD-1_P-4_R-4" />
                  <choose ParamRefId="{_APP}_MD-1_P-4_R-4">
                    <when test="0">
                      <Assign TargetParamRefRef="{_APP}_MD-1_UP-6_R-6" Value="0" />
                    </when>
                    <when test="1">
                      <Assign TargetParamRefRef="{_APP}_MD-1_UP-6_R-6" Value="1" />
                    </when>
                    <when test="2">
                      <Assign TargetParamRefRef="{_APP}_MD-1_UP-6_R-6" Value="2" />
                    </when>
                  </choose>
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_OVERWRITE}" />
                <choose ParamRefId="{_OVERWRITE}">
                  <when test="1">
                    <ParameterRefRef RefId="{_ZONE}" />
                  </when>
                </choose>
              </ParameterBlock>
              <Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="0" /></Module>
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


def _device(
    application: Application, values: dict[str, str]
) -> dict[TypedPropertyKey, bytes]:
    """The properties a device holds after a download of ``values``: the time zone keeps its
    default unless the switch shows it (the bytes its active parameters write, the others as
    the device held them)."""
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    device = {_ZONE_KEY: (26757).to_bytes(2, "big"), _FILTER_KEY: b"\x00"}
    for key, written in ui.encode_to_typed_properties_masked().items():
        held = device[key]
        device[key] = bytes(
            (data & mask) | (old & ~mask & 0xFF)
            for data, mask, old in zip(written.data, written.mask, held, strict=True)
        )
    return device


def test_the_device_is_set_up_as_downloaded(application: Application) -> None:
    device = _device(application, {_OVERWRITE: "1", _ZONE: "26952", _FILTER: "1"})
    assert device[_ZONE_KEY] == (26952).to_bytes(2, "big")
    assert device[_FILTER_KEY] == b"\x01"
    assert _device(application, {})[_FILTER_KEY] == b"\x00"


def test_the_layout_holds_the_settings_shown_or_not(application: Application) -> None:
    ui = seed_dynamic_ui(application, {})
    assert ui is not None
    layout = ui.stored_property_layout()
    # The time zone is hidden, its bytes are still its own; the module's union member is
    # qualified with its instance.
    assert layout.cells[(_ZONE_KEY, 0)] == {_ZONE}
    assert layout.cells[(_ZONE_KEY, 1)] == {_ZONE}
    assert layout.cells[(_FILTER_KEY, 0)] == {_ASSIGNED}
    # Defaults of the parameters that are no member of a union only.
    assert layout.defaults[_ZONE_KEY].data == (26757).to_bytes(2, "big")
    assert _FILTER_KEY not in layout.defaults


def test_a_property_value_other_than_its_default_shows_its_setting(
    application: Application,
) -> None:
    device = _device(application, {_OVERWRITE: "1", _ZONE: "26952"})
    recovered = recover_parameters(application, {}, device)
    assert recovered.complete
    assert recovered.values[_OVERWRITE] == "1"
    assert recovered.values[_ZONE] == "26952"


def test_a_property_at_its_default_leaves_the_switch_off(
    application: Application,
) -> None:
    recovered = recover_parameters(application, {}, _device(application, {}))
    assert recovered.complete
    assert recovered.values.get(_OVERWRITE, "0") == "0"


def test_the_setting_that_assigns_a_property_value_is_searched(
    application: Application,
) -> None:
    device = _device(application, {_FILTER: "2"})
    recovered = recover_parameters(application, {}, device)
    assert recovered.complete
    assert recovered.values[_FILTER] == "2"


def test_a_property_written_otherwise_differs_from_the_device(
    application: Application,
) -> None:
    # No value of the filter assigns 3: the result cannot write the device's property.
    device = {**_device(application, {}), _FILTER_KEY: b"\x03"}
    recovered = recover_parameters(application, {}, device)
    assert not recovered.complete
    assert recovered.differing_bytes == 1

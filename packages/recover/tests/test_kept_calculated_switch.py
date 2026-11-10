"""Reading a device back: a hidden switch a calculation computes from a function the
configuration no longer shows keeps the value computed before (the function was switched on,
then another mode hid it), and shows a group object the device links. Only the linked object
tells it: the search tries its values."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _kept_values_to_try,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F4-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_MODE = f"{_APP}_P-1_R-1"
_LEAD = f"{_APP}_P-2_R-2"
_SHOW = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="244" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Kept calculated switch"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-OnOff" Name="OnOff">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Off" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
              <Enumeration Text="On" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
            </TypeRestriction>
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-OnOff" Text="Slave" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-2" Name="Lead" ParameterType="{_APP}_PT-OnOff" Text="Lead temperature" Value="0" />
          <Parameter Id="{_APP}_P-3" Name="Show" ParameterType="{_APP}_PT-OnOff" Text="" Access="None" Value="0" />
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_MODE}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_LEAD}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_SHOW}" RefId="{_APP}_P-3" />
        </ParameterRefs>
        <ParameterCalculations>
          <ParameterCalculation Id="{_APP}_PC-1" Name="Show" Language="JavaScript">
            <RLTransformation>if (show == 0) {{ lead = 0; }}</RLTransformation>
            <LRTransformation>show = lead ? 1 : 0;</LRTransformation>
            <LParameters><ParameterRefRef RefId="{_LEAD}" AliasName="lead" /></LParameters>
            <RParameters><ParameterRefRef RefId="{_SHOW}" AliasName="show" /></RParameters>
          </ParameterCalculation>
        </ParameterCalculations>
        <ComObjectTable>
          <ComObject Id="{_APP}_O-1" Name="Temperature" Text="Temperature" Number="1" FunctionText=""
              ObjectSize="2 Bytes" ReadFlag="Enabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
              TransmitFlag="Disabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-9-1" />
        </ComObjectTable>
        <ComObjectRefs>
          <ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" />
        </ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_MODE}" />
            <choose ParamRefId="{_MODE}">
              <when test="0"><ParameterRefRef RefId="{_LEAD}" /></when>
            </choose>
            <ParameterRefRef RefId="{_SHOW}" />
            <choose ParamRefId="{_SHOW}">
              <when test="1"><ComObjectRefRef RefId="{_APP}_O-1_R-1" /></when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_the_kept_switch_is_found_from_the_linked_object(
    application: Application,
) -> None:
    # The lead temperature switched on shows the object; the slave mode then hides the lead
    # function, the switch keeps its value and the object stays.
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_LEAD, "1")
    assert ui.get_value(_SHOW) == "1"
    ui.set_parameter_ref(_MODE, "1")
    assert ui.get_value(_SHOW) == "1"
    memory = ui.encode_to_memory()

    result = recover_parameters(
        application,
        memory,
        linked_objects={1},
        object_sizes={1: "2 Bytes"},
        effort=RecoveryEffort.FAST,
    )

    assert result.values.get(_MODE) == "1"
    assert result.values.get(_SHOW) == "1"
    assert result.complete


def test_values_of_the_switch_are_tried_only_while_its_input_is_hidden(
    application: Application,
) -> None:
    hidden = seed_dynamic_ui(application, {_MODE: "1"}, None)
    assert hidden is not None
    assert _kept_values_to_try(hidden, _SHOW, ()) == ["1"]
    # A setting that is no calculation's result is not tried this way.
    assert _kept_values_to_try(hidden, _LEAD, ()) == []

    shown = seed_dynamic_ui(application, {_MODE: "0"}, None)
    assert shown is not None
    # The lead function is shown: its own values are tried, which compute the switch.
    assert _kept_values_to_try(shown, _SHOW, {_LEAD}) == []


# The same application with the lead function stored (hidden, the download leaves its byte
# as it is) and a hidden marker of a fixed value the device stores (the kind of the function),
# so the result is checked once more with the fixed values kept.
_MARKER = f"{_APP}_P-4_R-4"
_MARKED_XML = (
    _XML.replace('Size="2"><Data>AAA=</Data>', 'Size="3"><Data>AAAA</Data>')
    .replace(
        'Text="Lead temperature" Value="0" />',
        'Text="Lead temperature" Value="0">\n'
        f'            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />\n'
        "          </Parameter>",
    )
    .replace(
        "        </Parameters>",
        f'          <Parameter Id="{_APP}_P-4" Name="Kind" ParameterType="{_APP}_PT-OnOff"'
        ' Text="" Access="None" Value="0">\n'
        f'            <Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" />\n'
        "          </Parameter>\n"
        "        </Parameters>",
    )
    .replace(
        f'<ParameterRef Id="{_SHOW}" RefId="{_APP}_P-3" />',
        f'<ParameterRef Id="{_SHOW}" RefId="{_APP}_P-3" />\n'
        f'          <ParameterRef Id="{_MARKER}" RefId="{_APP}_P-4" Value="1" />',
    )
    .replace(
        f'<ParameterRefRef RefId="{_MODE}" />',
        f'<ParameterRefRef RefId="{_MODE}" />\n'
        f'            <ParameterRefRef RefId="{_MARKER}" />',
    )
)


@pytest.mark.parametrize("effort", list(RecoveryEffort))
def test_the_kept_switch_stays_with_a_fixed_marker(effort: RecoveryEffort) -> None:
    application = parse_application_xml(_MARKED_XML.encode(), "M-00FA")[0]
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_LEAD, "1")
    ui.set_parameter_ref(_MODE, "1")
    assert ui.get_value(_SHOW) == "1"
    memory = ui.encode_to_memory()
    # The hidden lead function keeps the byte the download leaves.
    assert memory[_SEGMENT] == b"\x01\x00\x01"

    result = recover_parameters(
        application,
        memory,
        linked_objects={1},
        object_sizes={1: "2 Bytes"},
        effort=effort,
    )

    # Only the linked object tells the switch: checked with the marker's fixed value, it
    # keeps the value the object needs, not the one the lead function read computes.
    assert result.values.get(_MODE) == "1"
    assert result.values.get(_SHOW) == "1"
    assert result.complete

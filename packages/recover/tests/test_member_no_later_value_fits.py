"""A union member that later members write over in full tries the values its chooses test only
while one of them can show a later member that holds the bits read.

"Role" (values 0, 1, 2), "Type" (values 2, 3, 4) and "Extra" (values 7, 8, 9) share byte 0,
"Type" and "Extra" written after "Role". "Role" 2 shows "Type"; "Extra" is shown by "Mode"
(byte 1), in the plain layout beside "Role", in the nested one only with "Role" 1.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00FC-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_ROLE = f"{_APP}_UP-1_R-1"
_TYPE = f"{_APP}_UP-2_R-2"
_EXTRA = f"{_APP}_UP-5_R-5"
_DELAY = f"{_APP}_P-3_R-3"
_MODE = f"{_APP}_P-4_R-4"
_WIDE = f"{_APP}_PT-W"

_EXTRA_BESIDE = f"""
            <choose ParamRefId="{_MODE}">
              <when test="1">
                <ParameterRefRef RefId="{_EXTRA}" />
              </when>
            </choose>"""


def _xml(*, nested: bool) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="252" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Member no later value fits"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-R" Name="R">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Master" Value="0" Id="{_APP}_PT-R_EN-0" />
              <Enumeration Text="Alone" Value="1" Id="{_APP}_PT-R_EN-1" />
              <Enumeration Text="Slave" Value="2" Id="{_APP}_PT-R_EN-2" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-T" Name="T">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="A" Value="2" Id="{_APP}_PT-T_EN-2" />
              <Enumeration Text="B" Value="3" Id="{_APP}_PT-T_EN-3" />
              <Enumeration Text="C" Value="4" Id="{_APP}_PT-T_EN-4" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-E" Name="E">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="X" Value="7" Id="{_APP}_PT-E_EN-7" />
              <Enumeration Text="Y" Value="8" Id="{_APP}_PT-E_EN-8" />
              <Enumeration Text="Z" Value="9" Id="{_APP}_PT-E_EN-9" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-M" Name="M">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Off" Value="0" Id="{_APP}_PT-M_EN-0" />
              <Enumeration Text="On" Value="1" Id="{_APP}_PT-M_EN-1" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_WIDE}" Name="W">
            <TypeRestriction Base="Value" SizeInBit="16">
              <Enumeration Text="P" Value="4660" Id="{_WIDE}_EN-4660" />
              <Enumeration Text="Q" Value="255" Id="{_WIDE}_EN-255" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
            <Parameter Id="{_APP}_UP-1" Name="Role" ParameterType="{_APP}_PT-R" Offset="0" BitOffset="0" Text="Role" Value="0" DefaultUnionParameter="true" />
            <Parameter Id="{_APP}_UP-2" Name="Type" ParameterType="{_APP}_PT-T" Offset="0" BitOffset="0" Text="Type" Value="2" />
            <Parameter Id="{_APP}_UP-5" Name="Extra" ParameterType="{_APP}_PT-E" Offset="0" BitOffset="0" Text="Extra" Value="7" />
          </Union>
          <Parameter Id="{_APP}_P-4" Name="Mode" ParameterType="{_APP}_PT-M" Text="Mode" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-3" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="10">
            <Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_ROLE}" RefId="{_APP}_UP-1" />
          <ParameterRef Id="{_TYPE}" RefId="{_APP}_UP-2" />
          <ParameterRef Id="{_EXTRA}" RefId="{_APP}_UP-5" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_MODE}" RefId="{_APP}_P-4" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_MODE}" />
            <ParameterRefRef RefId="{_ROLE}" />
            <choose ParamRefId="{_ROLE}">
              <when test="0">
                <ParameterRefRef RefId="{_DELAY}" />
              </when>
              <when test="1">{_EXTRA_BESIDE if nested else ""}
              </when>
              <when test="2">
                <ParameterRefRef RefId="{_TYPE}" />
              </when>
            </choose>{"" if nested else _EXTRA_BESIDE}
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application(*, nested: bool = False) -> Application:
    return parse_application_xml(_xml(nested=nested).encode(), "M-00FA")[0]


def _segments(application: Application, values: dict[str, str]) -> dict[str, bytes]:
    device = application.dynamic_ui()
    assert device is not None
    for ref_id, value in values.items():
        device.set_parameter_ref(ref_id, value)
    return device.encode_to_memory()


@pytest.fixture
def tried(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The union members a value was tried of with a later member active (see
    ``_later_members_read``)."""
    found: list[str] = []
    read: Callable[..., object] = parameters._later_members_read  # pyright: ignore[reportPrivateUsage]

    def recorded(ui: object, ref_id: str, *args: object) -> object:
        found.append(ref_id)
        return read(ui, ref_id, *args)

    monkeypatch.setattr(parameters, "_later_members_read", recorded)
    return found


def test_no_value_is_tried_when_no_later_member_holds_the_bits(
    tried: list[str],
) -> None:
    application = _application()
    segments = _segments(application, {_DELAY: "7"})
    # 9 is a value of "Extra" only, which "Mode" (Off) hides whatever "Role" is.
    segments[_SEGMENT] = bytes([9, 0, 7])

    recovered = recover_parameters(application, segments)
    assert _ROLE not in tried
    assert recovered.values.get(_ROLE, "0") != "2"
    assert recovered.values[_DELAY] == "7"
    assert not recovered.complete


def test_the_values_are_tried_when_a_later_member_holds_the_bits(
    tried: list[str],
) -> None:
    application = _application()
    segments = _segments(application, {_ROLE: "2", _TYPE: "3"})
    assert segments[_SEGMENT][0] == 3

    recovered = recover_parameters(application, segments)
    assert _ROLE in tried
    assert recovered.values[_ROLE] == "2"
    assert recovered.values[_TYPE] == "3"
    assert recovered.complete


def test_the_values_are_tried_when_the_member_decides_a_later_member_holding_them(
    tried: list[str],
) -> None:
    application = _application(nested=True)
    segments = _segments(application, {_ROLE: "1", _MODE: "1", _EXTRA: "9"})
    assert segments[_SEGMENT][:2] == bytes([9, 1])

    recovered = recover_parameters(application, segments)
    assert _ROLE in tried
    assert recovered.values[_ROLE] == "1"
    assert recovered.values[_EXTRA] == "9"
    assert recovered.complete


@pytest.mark.parametrize(
    ("at", "raw", "held"),
    [
        (0, 0x12, True),
        (8, 0x34, True),
        (0, 0x00, True),
        (8, 0xFF, True),
        (0, 0x13, False),
        (8, 0x12, False),
        (4, 0x23, True),
        (4, 0x0F, True),
        (4, 0x24, False),
    ],
)
def test_a_choice_holds_the_bits_with_any_bits_around_them(
    at: int, raw: int, held: bool
) -> None:
    application = _application()
    ui = application.dynamic_ui()
    assert ui is not None
    assert (
        parameters._choice_with_bits(  # pyright: ignore[reportPrivateUsage]
            application, ui.indexer, _WIDE, 16, at, 8, raw
        )
        is held
    )

"""A shown union member that a hidden member written after it over all of its bits is assigned
from is read from that member: the memory holds the hidden member's value, which is its own.

"Mode" (4 bits, 4 or 5) is shown; "Kind" (the same 4 bits, 0 to 7) is hidden and copied from
"Mode" by an Assign, and a download writes it after "Mode". "Same" shows the case of a member
of the hidden member's own type. "Count" (byte 1) is stored on its own.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F8-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_MODE = f"{_APP}_UP-1_R-1"
_KIND = f"{_APP}_UP-2_R-2"
_COUNT = f"{_APP}_P-3_R-3"


def _xml(mode_type: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="248" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Member assigned"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-M" Name="M">
            <TypeRestriction Base="Value" SizeInBit="4">
              <Enumeration Text="Single values" Value="4" Id="{_APP}_PT-M_EN-4" />
              <Enumeration Text="Constant" Value="5" Id="{_APP}_PT-M_EN-5" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-K" Name="K">
            <TypeRestriction Base="Value" SizeInBit="4">
              {"".join(f'<Enumeration Text="Kind {n}" Value="{n}" Id="{_APP}_PT-K_EN-{n}" />' for n in range(8))}
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
            <Parameter Id="{_APP}_UP-2" Name="Kind" ParameterType="{_APP}_PT-K" Offset="0" BitOffset="0" Text="Kind" Value="0" Access="None" />
            <Parameter Id="{_APP}_UP-1" Name="Mode" ParameterType="{_APP}_PT-{mode_type}" Offset="0" BitOffset="0" Text="Mode" Value="4" />
          </Union>
          <Parameter Id="{_APP}_P-3" Name="Count" ParameterType="{_APP}_PT-N" Text="Count" Value="1">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_MODE}" RefId="{_APP}_UP-1" />
          <ParameterRef Id="{_KIND}" RefId="{_APP}_UP-2" />
          <ParameterRef Id="{_COUNT}" RefId="{_APP}_P-3" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_KIND}" />
            <ParameterRefRef RefId="{_MODE}" />
            <Assign TargetParamRefRef="{_KIND}" SourceParamRefRef="{_MODE}" />
            <ParameterRefRef RefId="{_COUNT}" />
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application(mode_type: str = "M") -> Application:
    return parse_application_xml(_xml(mode_type).encode(), "M-00FA")[0]


def _device(application: Application, mode: str) -> dict[str, bytes]:
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_MODE, mode)
    ui.set_parameter_ref(_COUNT, "9")
    return ui.encode_to_memory()


def test_the_hidden_member_holds_the_bits() -> None:
    application = _application()
    ui = application.dynamic_ui()
    assert ui is not None
    assert ui.later_union_members(_MODE) == {_KIND}
    assert _device(application, "5")[_SEGMENT] == b"\x50\x09"


@pytest.mark.parametrize("mode_type", ["M", "K"])
def test_the_shown_member_is_read_from_the_member_it_is_assigned_into(
    mode_type: str,
) -> None:
    application = _application(mode_type)
    segments = _device(application, "5")
    recovered = recover_parameters(application, segments)
    assert recovered.values[_MODE] == "5"
    assert recovered.values[_COUNT] == "9"
    assert recovered.complete
    assert recovered.differing_bytes == 0


def test_a_value_the_shown_member_does_not_have_is_not_taken() -> None:
    # A device holding 7, which only the hidden member has (written by another version of
    # the application): the shown member keeps its own value.
    recovered = recover_parameters(_application(), {_SEGMENT: b"\x70\x09"})
    assert recovered.values.get(_MODE, "4") != "7"
    assert recovered.values[_COUNT] == "9"

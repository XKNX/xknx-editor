"""Members of different unions (and plain parameters) over the same bits: a download applies
the active ParameterRefs of the application, and of each module instance, one after the other
in ParameterRef order, so the parameter whose last active ref comes last holds the bits, not
the one whose union comes last in the static section."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import parse_application_xml

APP = "M-00FA_A-00F5-01-0000"
MD = f"{APP}_MD-1"
SEGMENT = f"{APP}_AS-0000"


def _union(prefix: str, number: int, value: int, base: str = "") -> str:
    return f"""
              <Union SizeInBit="8">
                <Memory CodeSegment="{SEGMENT}" Offset="0" BitOffset="0"{base} />
                <Parameter Id="{prefix}_UP-{number}" Name="U{number}" ParameterType="{APP}_PT-N"
                    Offset="0" BitOffset="0" Text="U{number}" Value="{value}" />
              </Union>"""


def _xml(static: str, refs: str, block: str, modules: str = "") -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{APP}" ApplicationNumber="245" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Union order"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{SEGMENT}" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>{static}
        </Parameters>
        <ParameterRefs>{refs}
        </ParameterRefs>
      </Static>{modules}
      <Dynamic>
        <ChannelIndependentBlock><ParameterBlock Id="{APP}_PB-1" Text="General">{block}</ParameterBlock></ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
""".encode()


def _encode(xml: bytes) -> int:
    application = parse_application_xml(xml, "M-00FA")[0]
    ui = application.dynamic_ui()
    assert ui is not None
    image = ui.encode_to_memory()[SEGMENT]
    masked = ui.encode_to_memory_masked()[SEGMENT][0]
    # The downloaded image is built in the same order.
    assert masked == image
    return image[0]


@pytest.mark.parametrize(("tag_1", "tag_2", "held"), [(20, 10, 1), (10, 20, 2)])
def test_members_of_two_unions_over_the_same_bits(
    tag_1: int, tag_2: int, held: int
) -> None:
    """The first union's member with the later ref holds the byte, although the second
    union comes later in the static section."""
    xml = _xml(
        _union(APP, 1, 1) + _union(APP, 2, 2),
        f"""
          <ParameterRef Id="{APP}_UP-1_R-1" RefId="{APP}_UP-1" Tag="{tag_1}" />
          <ParameterRef Id="{APP}_UP-2_R-2" RefId="{APP}_UP-2" Tag="{tag_2}" />""",
        f"""<ParameterRefRef RefId="{APP}_UP-2_R-2" /><ParameterRefRef RefId="{APP}_UP-1_R-1" />""",
    )
    assert _encode(xml) == held


def test_a_parameter_with_several_refs_writes_at_its_last_active_one() -> None:
    """A plain parameter shown through two refs, one before and one after a union member over
    the same byte: its last ref comes after the member's, so it holds the byte."""
    xml = _xml(
        f"""
          <Parameter Id="{APP}_P-3" Name="P3" ParameterType="{APP}_PT-N" Text="P3" Value="3">
            <Memory CodeSegment="{SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>"""
        + _union(APP, 1, 1),
        f"""
          <ParameterRef Id="{APP}_P-3_R-3" RefId="{APP}_P-3" Tag="5" />
          <ParameterRef Id="{APP}_P-3_R-4" RefId="{APP}_P-3" Tag="30" />
          <ParameterRef Id="{APP}_UP-1_R-1" RefId="{APP}_UP-1" Tag="20" />""",
        f"""<ParameterRefRef RefId="{APP}_P-3_R-3" /><ParameterRefRef RefId="{APP}_UP-1_R-1" />
            <ParameterRefRef RefId="{APP}_P-3_R-4" />""",
    )
    assert _encode(xml) == 3


@pytest.mark.parametrize(("ref_1", "ref_2", "held"), [(2, 1, 1), (1, 2, 2)])
def test_members_of_two_unions_of_a_module_instance(
    ref_1: int, ref_2: int, held: int
) -> None:
    """In a module instance the instance's own refs give the order (here by ref number)."""
    base = f' BaseOffset="{MD}_A-1"'
    modules = f"""
      <ModuleDefs>
        <ModuleDef Id="{MD}" Name="Channel">
          <Arguments><Argument Id="{MD}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>{_union(MD, 1, 1, base)}{_union(MD, 2, 2, base)}
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{MD}_UP-1_R-{ref_1}" RefId="{MD}_UP-1" />
              <ParameterRef Id="{MD}_UP-2_R-{ref_2}" RefId="{MD}_UP-2" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{MD}_UP-2_R-{ref_2}" />
              <ParameterRefRef RefId="{MD}_UP-1_R-{ref_1}" />
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>"""
    xml = _xml("", "", "", modules).replace(
        b"</ChannelIndependentBlock>",
        f"""</ChannelIndependentBlock>
        <Module Id="{MD}_M-1" RefId="{MD}" Name="Channel 1"><NumericArg RefId="{MD}_A-1" Value="0" /></Module>""".encode(),
    )
    assert _encode(xml) == held

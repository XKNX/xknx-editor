"""The values of a setting the device keeps no copy of that the memory is read with end at the
first one with which every union member it shows fits the memory: no value after it can fit
better."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml

_APP = "M-00FA_A-0007-01-0000"
_SELECTOR = f"{_APP}_P-1_R-1"
_FIRST = f"{_APP}_UP-2_R-2"
_SECOND = f"{_APP}_UP-3_R-3"
_THIRD = f"{_APP}_UP-4_R-4"
_SEGMENT = f"{_APP}_AS-0000"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Union by selector"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-Kind" Name="Kind">
                <TypeRestriction Base="Value" SizeInBit="8">
                  <Enumeration Text="First" Value="0" Id="{_APP}_PT-Kind_EN-0" />
                  <Enumeration Text="Second" Value="1" Id="{_APP}_PT-Kind_EN-1" />
                  <Enumeration Text="Third" Value="2" Id="{_APP}_PT-Kind_EN-2" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Low" Name="Low">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="9" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-High" Name="High">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="10" maxInclusive="99" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Kind" ParameterType="{_APP}_PT-Kind" Text="Kind" Value="0" />
              <Union SizeInBit="8">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
                <Parameter Id="{_APP}_UP-2" Name="First" ParameterType="{_APP}_PT-Low" Text="First" Value="0" Offset="0" BitOffset="0" />
                <Parameter Id="{_APP}_UP-3" Name="Second" ParameterType="{_APP}_PT-High" Text="Second" Value="10" Offset="0" BitOffset="0" />
                <Parameter Id="{_APP}_UP-4" Name="Third" ParameterType="{_APP}_PT-High" Text="Third" Value="10" Offset="0" BitOffset="0" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_SELECTOR}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_FIRST}" RefId="{_APP}_UP-2" />
              <ParameterRef Id="{_SECOND}" RefId="{_APP}_UP-3" />
              <ParameterRef Id="{_THIRD}" RefId="{_APP}_UP-4" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_SELECTOR}" />
                <choose ParamRefId="{_SELECTOR}">
                  <when test="0"><ParameterRefRef RefId="{_FIRST}" /></when>
                  <when test="1"><ParameterRefRef RefId="{_SECOND}" /></when>
                  <when test="2"><ParameterRefRef RefId="{_THIRD}" /></when>
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


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str | None]]:
    """The selector values of the UIs seeded from now on."""
    seen: list[str | None] = []
    seed = parameters._seeded  # pyright: ignore[reportPrivateUsage]

    def recorded(
        application: Application, values: Mapping[str, str], *args: object, **kw: object
    ) -> object:
        seen.append(values.get(_SELECTOR))
        return seed(application, values, *args, **kw)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(parameters, "_seeded", recorded)
    yield seen


def test_the_values_end_at_the_first_with_which_every_member_fits(
    application: Application, seeded: list[str | None]
) -> None:
    # 42 is no value of the first member, and a value of the second and the third: with the
    # second every member shown fits, the third is not tried.
    segments = {_SEGMENT: bytes([42, 0])}
    values, unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}
    )
    assert _FIRST in unknown or f"{_APP}_UP-2" in unknown
    seeded.clear()
    chosen = parameters._infer_from_memory(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, values, unknown, set(), {}
    )
    assert chosen == {_SELECTOR: "1"}
    assert "2" not in seeded


def test_without_a_fit_every_value_is_tried(
    application: Application, seeded: list[str | None]
) -> None:
    # 200 is no value of any member: every other value of the selector is tried, none taken.
    segments = {_SEGMENT: bytes([200, 0])}
    values, unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}
    )
    seeded.clear()
    chosen = parameters._infer_from_memory(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, values, unknown, set(), {}
    )
    assert chosen == {}
    assert {"1", "2"} <= set(seeded)

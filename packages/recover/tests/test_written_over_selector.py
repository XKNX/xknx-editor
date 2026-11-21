"""A union member a later active member writes over in part is searched like a setting the
device keeps no copy of: the memory holds several of its values alike.

"Type" (3 bits) shares its union with the hidden "Shadow" (3 bits, one bit further), which is
written after it: only the first bit of "Type" survives, so 0, 2 and 3 read the same. "Type" 2
shows "Delay" and its group object; the memory of "Delay" and the linked object tell it apart.
"""

from __future__ import annotations

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F3-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_TYPE = f"{_APP}_UP-1_R-1"
_SHADOW = f"{_APP}_UP-2_R-2"
_DELAY = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="243" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Written over"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-T" Name="T">
            <TypeRestriction Base="Value" SizeInBit="3">
              <Enumeration Text="None" Value="0" Id="{_APP}_PT-T_EN-0" />
              <Enumeration Text="Normal" Value="2" Id="{_APP}_PT-T_EN-2" />
              <Enumeration Text="Inverted" Value="3" Id="{_APP}_PT-T_EN-3" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
            <Parameter Id="{_APP}_UP-1" Name="Type" ParameterType="{_APP}_PT-T" Offset="0" BitOffset="0" Text="Type" Value="0" DefaultUnionParameter="true" />
            <Parameter Id="{_APP}_UP-2" Name="Shadow" ParameterType="{_APP}_PT-T" Offset="0" BitOffset="1" Text="Shadow" Access="None" Value="0" />
          </Union>
          <Parameter Id="{_APP}_P-3" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="10">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_TYPE}" RefId="{_APP}_UP-1" />
          <ParameterRef Id="{_SHADOW}" RefId="{_APP}_UP-2" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-3" />
        </ParameterRefs>
        <ComObjectTable>
          <ComObject Id="{_APP}_O-1" Name="O1" Text="Alarm" Number="1" FunctionText="" ObjectSize="1 Bit"
              ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled"
              UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" />
        </ComObjectTable>
        <ComObjectRefs><ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" /></ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_TYPE}" />
            <ParameterRefRef RefId="{_SHADOW}" />
            <choose ParamRefId="{_TYPE}">
              <when test="2">
                <ParameterRefRef RefId="{_DELAY}" />
                <ComObjectRefRef RefId="{_APP}_O-1_R-1" />
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_the_value_behind_the_bits_left_is_found() -> None:
    application = _application()
    device = application.dynamic_ui()
    assert device is not None
    device.set_parameter_ref(_TYPE, "2")
    device.set_parameter_ref(_DELAY, "99")
    segments = device.encode_to_memory()
    # "Shadow" wrote over all but the first bit of "Type".
    assert segments[_SEGMENT] == bytes([0, 99])

    recovered = recover_parameters(application, segments, linked_objects={1})
    assert recovered.values[_TYPE] == "2"
    assert recovered.values[_DELAY] == "99"
    assert recovered.complete


def test_without_evidence_the_first_fitting_value_stays() -> None:
    # Nothing wrote "Delay": "Type" did not show it.
    recovered = recover_parameters(_application(), {_SEGMENT: bytes([0, 0])})
    assert recovered.values[_TYPE] == "0"
    assert recovered.complete


def test_within_a_search_a_reading_is_made_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A decode reads the same members again for the same UI and values: within a search
    # that reading is made once; for other values, memory or UI it is made again.
    application = _application()
    ui = parameters._seeded(application, {}, None)  # pyright: ignore[reportPrivateUsage]
    assert ui is not None
    made: list[dict[str, str]] = []
    reading = parameters._written_over_values  # pyright: ignore[reportPrivateUsage]

    def counted(*args: object, **kwargs: object) -> dict[str, str]:
        made.append(dict(args[3]))  # type: ignore[call-overload]
        return reading(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(parameters, "_written_over_values", counted)
    unknown = [f"{_APP}_UP-1"]

    def read(
        values: dict[str, str], memory: bytes = bytes([0, 99]), seeding: str = "a"
    ) -> dict[str, str]:
        return parameters._written_over_read(  # pyright: ignore[reportPrivateUsage]
            application,
            ui,
            (seeding,),
            {_SEGMENT: memory},
            values,
            unknown,
            None,
            {},
        )

    found = read({_DELAY: "99"})
    assert len(made) == 1
    read({_DELAY: "99"})
    assert len(made) == 2  # no search: made each time
    monkeypatch.setattr(
        parameters._SEARCH,  # pyright: ignore[reportPrivateUsage]
        "cache",
        parameters._SearchCache(application),  # pyright: ignore[reportPrivateUsage]
        raising=False,
    )
    assert read({_DELAY: "99"}) == found
    found["changed"] = "1"
    assert read({_DELAY: "99"}) == {k: v for k, v in found.items() if k != "changed"}
    assert len(made) == 3
    read({_DELAY: "98"})
    read({_DELAY: "99"}, bytes([1, 99]))
    read({_DELAY: "99"}, seeding="b")
    assert len(made) == 6


@pytest.mark.parametrize(
    ("memory", "seeded_with_shadow"), [(0x00, False), (0x20, True)]
)
def test_a_later_member_read_as_it_is_seeds_no_ui_again(
    monkeypatch: pytest.MonkeyPatch, memory: int, seeded_with_shadow: bool
) -> None:
    # "Type" is tried with "Shadow" as the memory holds it: where that is the value the UI
    # gives it already, the UI is the one seeded without it, and none is seeded again.
    application = _application()
    ui = parameters._seeded(application, {}, None)  # pyright: ignore[reportPrivateUsage]
    assert ui is not None
    seeded: list[dict[str, str]] = []
    seed = parameters._seeded  # pyright: ignore[reportPrivateUsage]

    def counted(
        application: Application,
        values: dict[str, str],
        *args: object,
        **kwargs: object,
    ) -> object:
        seeded.append(dict(values))
        return seed(application, values, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(parameters, "_seeded", counted)
    found = parameters._written_over_values(  # pyright: ignore[reportPrivateUsage]
        application,
        ui,
        {_SEGMENT: bytes([memory, 0])},
        {},
        [f"{_APP}_UP-1"],
        None,
        {},
    )
    assert found == {_TYPE: "0"}
    assert any(_SHADOW in values for values in seeded) is seeded_with_shadow

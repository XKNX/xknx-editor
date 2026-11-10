"""The values tried for the memory in a round of the inference are taken again in the next
round where only another module instance's values differ: the result is the one each value
evaluated anew gives."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F7-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_GENERAL = f"{_APP}_P-1_R-1"


def _kind(instance: int) -> str:
    return f"{_MD}_M-{instance}_MI-1_P-1_R-1"


def _low(instance: int) -> str:
    return f"{_MD}_M-{instance}_MI-1_UP-2_R-2"


def _high(instance: int) -> str:
    return f"{_MD}_M-{instance}_MI-1_UP-3_R-3"


def _module(instance: int) -> str:
    return (
        f'<Module Id="{_MD}_M-{instance}" RefId="{_MD}" Name="Channel {instance}">'
        f'<NumericArg RefId="{_MD}_A-1" Value="{2 * (instance - 1)}" /></Module>'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="247" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Channels with a kind"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="9"><Data>AAAAAAAAAAAA</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-Kind" Name="Kind">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Low" Value="0" Id="{_APP}_PT-Kind_EN-0" />
              <Enumeration Text="High" Value="1" Id="{_APP}_PT-Kind_EN-1" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-Low" Name="Low"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="9" /></ParameterType>
          <ParameterType Id="{_APP}_PT-High" Name="High"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="10" maxInclusive="99" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="General" ParameterType="{_APP}_PT-Low" Text="General" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="8" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs><ParameterRef Id="{_GENERAL}" RefId="{_APP}_P-1" /></ParameterRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments><Argument Id="{_MD}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Kind" ParameterType="{_APP}_PT-Kind" Text="Kind" Value="0" />
              <Parameter Id="{_MD}_P-4" Name="Delay" ParameterType="{_APP}_PT-Low" Text="Delay" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Union SizeInBit="8">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
                <Parameter Id="{_MD}_UP-2" Name="Low" ParameterType="{_APP}_PT-Low" Offset="0" BitOffset="0" Text="Low" Value="0" DefaultUnionParameter="true" />
                <Parameter Id="{_MD}_UP-3" Name="High" ParameterType="{_APP}_PT-High" Offset="0" BitOffset="0" Text="High" Value="10" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
              <ParameterRef Id="{_MD}_UP-2_R-2" RefId="{_MD}_UP-2" />
              <ParameterRef Id="{_MD}_UP-3_R-3" RefId="{_MD}_UP-3" />
              <ParameterRef Id="{_MD}_P-4_R-4" RefId="{_MD}_P-4" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <ParameterRefRef RefId="{_MD}_P-4_R-4" />
              <choose ParamRefId="{_MD}_P-1_R-1">
                <when test="0"><ParameterRefRef RefId="{_MD}_UP-2_R-2" /></when>
                <when test="1"><ParameterRefRef RefId="{_MD}_UP-3_R-3" /></when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General"><ParameterRefRef RefId="{_GENERAL}" /></ParameterBlock>
        </ChannelIndependentBlock>
        {_module(1)}{_module(2)}{_module(3)}{_module(4)}
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""

# Channels 1 to 3 hold a value of the high member, channel 4 one of the low member; each
# channel holds its delay after it.
_SEGMENTS = {_SEGMENT: bytes([42, 1, 42, 2, 42, 3, 5, 4, 1])}


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


@pytest.fixture
def taken_again(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The module instances whose evaluation of a value is taken again from now on."""
    seen: list[str] = []
    decode = DynamicUI.decode_parameters_with_instance

    def recorded(
        self: DynamicUI,
        segments: object,
        properties: object,
        instance_id: str,
        other: DynamicUI,
    ) -> object:
        seen.append(instance_id)
        return decode(self, segments, properties, instance_id, other)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(DynamicUI, "decode_parameters_with_instance", recorded)
    yield seen


def _anew(monkeypatch: pytest.MonkeyPatch) -> None:
    """From now on every value tried for the memory is evaluated anew."""
    infer = parameters._infer_from_memory  # pyright: ignore[reportPrivateUsage]

    def without(*args: object, **kw: object) -> object:
        kw.pop("evaluated", None)
        return infer(*args[:9], **kw)  # pyright: ignore[reportArgumentType, reportCallIssue]

    monkeypatch.setattr(parameters, "_infer_from_memory", without)


def _rounds(application: Application) -> tuple[object, ...]:
    values, unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, {}, fixed={}
    )
    return parameters._infer_rounds(  # pyright: ignore[reportPrivateUsage]
        application,
        _SEGMENTS,
        None,
        None,
        set(),
        {},
        {},
        set(),
        None,
        values,
        unknown,
        {},
    )


def test_the_inference_takes_a_value_tried_before_again(
    application: Application, taken_again: list[str]
) -> None:
    values, _unknown, inferred = _rounds(application)  # type: ignore[misc]

    # A round takes the kind of one channel. The third round takes the value of the third
    # channel tried in the second again: only the second channel's values differ.
    assert inferred == {_kind(1): "1", _kind(2): "1", _kind(3): "1"}
    assert [values[_high(n)] for n in (1, 2, 3)] == ["42"] * 3  # type: ignore[index]
    assert values[_low(4)] == "5"  # type: ignore[index]
    assert taken_again == [f"{_MD}_M-3_MI-1"]


def test_the_inference_ends_as_with_every_value_evaluated_anew(
    application: Application, taken_again: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    again = _rounds(application)
    assert taken_again
    _anew(monkeypatch)
    taken_again.clear()

    assert _rounds(application) == again
    assert not taken_again


def test_a_search_ends_as_with_every_value_evaluated_anew(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovered = recover_parameters(application, _SEGMENTS)
    _anew(monkeypatch)

    assert recover_parameters(application, _SEGMENTS) == recovered
    assert recovered.values[_kind(1)] == "1"
    assert recovered.values[_kind(3)] == "1"
    assert recovered.values.get(_kind(4), "0") == "0"


def test_a_value_is_evaluated_anew_once_the_application_scope_differs(
    application: Application, taken_again: list[str]
) -> None:
    values, unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, {}, fixed={}
    )
    evaluated: dict[tuple[str, str], parameters._Evaluated] = {}  # pyright: ignore[reportPrivateUsage]
    first = parameters._infer_from_memory(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, values, unknown, set(), {}, None, evaluated
    )
    assert first == {_kind(1): "1"}
    assert (_kind(2), "1") in evaluated
    # Another value of the application's own setting: what was tried is dropped, nothing
    # taken again.
    other = {**values, _GENERAL: "2"}
    again = parameters._infer_from_memory(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, other, unknown, set(), {}, None, evaluated
    )
    assert again == first
    assert not taken_again
    assert all(entry.shared[_GENERAL] == "2" for entry in evaluated.values())

"""Selectors the device keeps no copy of, nested in each other, told by a hidden marker the
product fixes per branch of the innermost one only.

A push-button extension (``on``) shows its type (``kind``), the type shows the mode of a rocker
(``mode``); each branch of the innermost has a reference of a hidden setting the device stores,
with its own fixed value. The outer selectors' own branches have no marker that fits: only the
markers behind the selectors they show tell their values. So the marker the memory holds names
the branch of the outermost selector, and with it the selectors it shows, one after another.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover import parameters
from xknxeditor.recover.parameters import (
    RecoveredParameters,
    RecoveryEffort,
    recover_parameters,
)

_APP = "M-00FA_A-0D02-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_ON = f"{_APP}_P-1_R-1"
_KIND = f"{_APP}_P-3_R-3"
_MODE = f"{_APP}_P-4_R-4"
_LEVEL = f"{_APP}_P-5_R-5"

_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="13" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Nested markers"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-OnOff" Name="OnOff"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
<Enumeration Text="On" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="On" ParameterType="{_APP}_PT-OnOff" Text="Extension" Value="0" />
<Parameter Id="{_APP}_P-2" Name="Marker" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-3" Name="Kind" ParameterType="{_APP}_PT-OnOff" Text="Kind" Value="0" />
<Parameter Id="{_APP}_P-4" Name="Mode" ParameterType="{_APP}_PT-OnOff" Text="Mode" Value="0" />
<Parameter Id="{_APP}_P-5" Name="Level" ParameterType="{_APP}_PT-N" Text="Level" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_ON}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_APP}_P-2_R-20" RefId="{_APP}_P-2" Value="0" />
<ParameterRef Id="{_APP}_P-2_R-21" RefId="{_APP}_P-2" Value="5" />
<ParameterRef Id="{_APP}_P-2_R-22" RefId="{_APP}_P-2" Value="7" />
<ParameterRef Id="{_APP}_P-2_R-23" RefId="{_APP}_P-2" Value="6" />
<ParameterRef Id="{_KIND}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_MODE}" RefId="{_APP}_P-4" />
<ParameterRef Id="{_LEVEL}" RefId="{_APP}_P-5" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Channel">
<ParameterRefRef RefId="{_ON}" />
<choose ParamRefId="{_ON}">
<when test="0"><ParameterRefRef RefId="{_APP}_P-2_R-20" /></when>
<when test="1"><ParameterRefRef RefId="{_KIND}" />
<choose ParamRefId="{_KIND}">
<when test="0"><ParameterRefRef RefId="{_APP}_P-2_R-21" /></when>
<when test="1"><ParameterRefRef RefId="{_MODE}" />
<choose ParamRefId="{_MODE}">
<when test="0"><ParameterRefRef RefId="{_APP}_P-2_R-22" /></when>
<when test="1"><ParameterRefRef RefId="{_APP}_P-2_R-23" /><ParameterRefRef RefId="{_LEVEL}" /></when>
</choose></when>
</choose></when>
</choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, *values: tuple[str, str]) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id, value), set in order."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values:
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


def test_the_marker_names_the_outer_selectors_too() -> None:
    application = _application()
    segments = _device(application, (_ON, "1"), (_KIND, "1"), (_MODE, "1"))
    values, _unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}, fixed={}
    )
    ui = application.dynamic_ui()
    assert ui is not None
    fixed = parameters._fixed_values(application, ui)  # pyright: ignore[reportPrivateUsage]
    named = parameters._infer_from_fixed(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, values, set(), {}, fixed
    )
    assert named == {_ON: "1", _KIND: "1", _MODE: "1"}


def test_a_marker_its_own_branch_holds_is_named_alone() -> None:
    # The type's own branch holds the marker: no selector further in is needed.
    application = _application()
    segments = _device(application, (_ON, "1"), (_KIND, "0"))
    values, _unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}, fixed={}
    )
    ui = application.dynamic_ui()
    assert ui is not None
    fixed = parameters._fixed_values(application, ui)  # pyright: ignore[reportPrivateUsage]
    named = parameters._infer_from_fixed(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, values, set(), {}, fixed
    )
    assert named == {_ON: "1"}


def test_no_branch_holds_the_marker() -> None:
    # A value no marker has: nothing is named.
    application = _application()
    segments = _device(application, (_ON, "1"), (_KIND, "1"), (_MODE, "1"))
    segments[_SEGMENT] = bytes((9,)) + segments[_SEGMENT][1:]
    values, _unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}, fixed={}
    )
    ui = application.dynamic_ui()
    assert ui is not None
    fixed = parameters._fixed_values(application, ui)  # pyright: ignore[reportPrivateUsage]
    assert (
        parameters._infer_from_fixed(  # pyright: ignore[reportPrivateUsage]
            application, segments, None, None, values, set(), {}, fixed
        )
        == {}
    )


@pytest.mark.parametrize("effort", [RecoveryEffort.FAST, RecoveryEffort.MEDIUM])
def test_the_search_reads_the_nested_selectors_back(effort: RecoveryEffort) -> None:
    application = _application()
    segments = _device(
        application, (_ON, "1"), (_KIND, "1"), (_MODE, "1"), (_LEVEL, "42")
    )
    recovered = recover_parameters(application, segments, effort=effort)
    assert recovered.complete
    assert recovered.values[_ON] == "1"
    assert recovered.values[_KIND] == "1"
    assert recovered.values[_MODE] == "1"
    assert recovered.values[_LEVEL] == "42"


def _searched(
    monkeypatch: pytest.MonkeyPatch,
    first: RecoveredParameters,
    again: RecoveredParameters,
    *,
    seen: bool,
) -> tuple[RecoveredParameters, list[bool]]:
    """What the search gives when its first run gives ``first`` (noting a selector further out
    with ``seen``) and a run that looks further out gives ``again``; and, per run, whether it
    looked further out."""
    runs: list[bool] = []

    def once(*_args: object, **_kwargs: object) -> RecoveredParameters:
        looked = bool(parameters._SEARCH.nested)  # pyright: ignore[reportPrivateUsage]
        runs.append(looked)
        if not looked:
            parameters._SEARCH.nested_seen = seen  # pyright: ignore[reportPrivateUsage]
        return again if looked else first

    monkeypatch.setattr(parameters, "_search_once", once)
    result = parameters._search(  # pyright: ignore[reportPrivateUsage]
        _application(), {}, None, None, (), None, RecoveryEffort.FAST, None
    )
    return result, runs


def _result(differing: int, unknown: int = 0) -> RecoveredParameters:
    return RecoveredParameters(
        values={}, unknown=[str(n) for n in range(unknown)], differing_bytes=differing
    )


def test_the_search_looks_further_out_only_when_it_does_not_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fitting = _result(0)
    result, runs = _searched(monkeypatch, fitting, _result(0), seen=True)
    assert result is fitting
    assert runs == [False]
    differing = _result(3)
    result, runs = _searched(monkeypatch, differing, _result(0), seen=False)
    assert result is differing
    assert runs == [False]


def test_the_result_that_looks_further_out_is_taken_when_it_fits_better(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    better = _result(1)
    result, runs = _searched(monkeypatch, _result(3), better, seen=True)
    assert result is better
    assert runs == [False, True]
    first = _result(3)
    result, _runs = _searched(monkeypatch, first, _result(3), seen=True)
    assert result is first
    result, _runs = _searched(monkeypatch, first, _result(3, unknown=1), seen=True)
    assert result is first
    assert getattr(parameters._SEARCH, "nested", None) is None  # pyright: ignore[reportPrivateUsage]


def test_a_search_at_its_effort_limit_does_not_look_further_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limited = RecoveredParameters(
        values={}, unknown=[], differing_bytes=3, limited=True
    )
    result, runs = _searched(monkeypatch, limited, _result(0), seen=True)
    assert result is limited
    assert runs == [False]

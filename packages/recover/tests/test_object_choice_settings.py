"""A setting whose chooses hold group objects only: what another of its values shows is told from
the UI as it is, without evaluating it again, once an evaluation of one of its values showed it
alike.

"Function 1" and "Function 2" (values 0 to 3) each show object 1 or 2 in a size of their own per
value, "Function 1" one of value 0 too (its default branch). "Shown" also shows a setting, "Twice"
is placed twice and "Copied" is copied by an Assign: their values are evaluated.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import xknxeditor.recover.object_choices as object_choices
import xknxeditor.recover.recover as recover_mod
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.object_choices import ShownObjects, object_selectors
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00FE-01-0000"
_FIRST = f"{_APP}_P-1_R-1"
_SECOND = f"{_APP}_P-2_R-2"
_SHOWN = f"{_APP}_P-3_R-3"
_TWICE = f"{_APP}_P-4_R-4"
_COPIED = f"{_APP}_P-5_R-5"
_TARGET = f"{_APP}_P-6_R-6"
_EXTRA = f"{_APP}_P-7_R-7"
_SIZES = ("1 Bit", "1 Byte", "2 Bytes", "4 Bytes")


def _object(number: int) -> str:
    return (
        f'<ComObject Id="{_APP}_O-{number}" Name="O{number}" Text="Object {number}"'
        f' Number="{number}" FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled"'
        ' WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled"'
        ' UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" />'
    )


def _refs(number: int) -> str:
    """A reference of object ``number`` in each size (``..._R-<number><value>``)."""
    return "".join(
        f'<ComObjectRef Id="{_APP}_O-{number}_R-{number}{value}" RefId="{_APP}_O-{number}"'
        f' ObjectSize="{size}" />'
        for value, size in enumerate(_SIZES)
    )


def _ref(number: int, value: int) -> str:
    return f'<ComObjectRefRef RefId="{_APP}_O-{number}_R-{number}{value}" />'


def _parameter(number: int, name: str) -> str:
    return (
        f'<Parameter Id="{_APP}_P-{number}" Name="{name}" ParameterType="{_APP}_PT-F"'
        f' Text="{name}" Value="0" />'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="254" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Object choice settings"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-F" Name="F">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="None" Value="0" Id="{_APP}_PT-F_EN-0" />
              <Enumeration Text="Switch" Value="1" Id="{_APP}_PT-F_EN-1" />
              <Enumeration Text="Value" Value="2" Id="{_APP}_PT-F_EN-2" />
              <Enumeration Text="Wide" Value="3" Id="{_APP}_PT-F_EN-3" />
            </TypeRestriction>
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          {_parameter(1, "Function 1")}
          {_parameter(2, "Function 2")}
          {_parameter(3, "Shown")}
          {_parameter(4, "Twice")}
          {_parameter(5, "Copied")}
          {_parameter(6, "Target")}
          {_parameter(7, "Extra")}
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_FIRST}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_SECOND}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_SHOWN}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_TWICE}" RefId="{_APP}_P-4" />
          <ParameterRef Id="{_COPIED}" RefId="{_APP}_P-5" />
          <ParameterRef Id="{_TARGET}" RefId="{_APP}_P-6" />
          <ParameterRef Id="{_EXTRA}" RefId="{_APP}_P-7" />
        </ParameterRefs>
        <ComObjectTable>{"".join(_object(n) for n in range(1, 6))}</ComObjectTable>
        <ComObjectRefs>{"".join(_refs(n) for n in range(1, 6))}</ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_FIRST}" />
            <choose ParamRefId="{_FIRST}">
              <when test="1">{_ref(1, 1)}</when>
              <when test="2 3">{_ref(1, 2)}</when>
              <when default="true">{_ref(1, 0)}</when>
            </choose>
            <ParameterRefRef RefId="{_SECOND}" />
            <choose ParamRefId="{_SECOND}">
              <when test="1">{_ref(2, 1)}</when>
              <when test="2">{_ref(2, 2)}</when>
              <when test="3">{_ref(2, 3)}</when>
            </choose>
            <ParameterRefRef RefId="{_SHOWN}" />
            <choose ParamRefId="{_SHOWN}">
              <when test="1">{_ref(3, 1)}<ParameterRefRef RefId="{_EXTRA}" /></when>
            </choose>
            <ParameterRefRef RefId="{_TWICE}" />
            <choose ParamRefId="{_TWICE}">
              <when test="1">{_ref(4, 1)}</when>
            </choose>
            <ParameterRefRef RefId="{_COPIED}" />
            <choose ParamRefId="{_COPIED}">
              <when test="1">{_ref(5, 1)}</when>
            </choose>
            <Assign TargetParamRefRef="{_TARGET}" SourceParamRefRef="{_COPIED}" />
          </ParameterBlock>
          <ParameterBlock Id="{_APP}_PB-2" Text="More">
            <ParameterRefRef RefId="{_TWICE}" />
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


def _evaluated(
    application: Application, values: dict[str, str], sizes: dict[int, str]
) -> tuple[set[int], int]:
    """The objects of 1 and 2 a UI seeded with ``values`` shows in ``sizes``, and how many
    others."""
    from xknxeditor.recover.parameters import (
        _shown_objects,  # pyright: ignore[reportPrivateUsage]
    )

    ui = recover_mod.seed_dynamic_ui(application, values)
    assert ui is not None
    shown = _shown_objects(ui, sizes)
    return shown & {1, 2}, len(shown - {1, 2})


def test_only_the_settings_whose_chooses_hold_objects_alone_are_told(
    application: Application,
) -> None:
    selectors = object_selectors(application)
    assert set(selectors) == {_FIRST, _SECOND}
    first = selectors[_FIRST]
    assert first.selected("0") == {f"{_APP}_O-1_R-10"}
    assert first.selected("1") == {f"{_APP}_O-1_R-11"}
    assert first.selected("3") == {f"{_APP}_O-1_R-12"}
    assert selectors[_SECOND].selected("0") == frozenset()
    assert first.objects[f"{_APP}_O-1_R-12"] == (1, "2 Bytes")


@pytest.mark.parametrize("sizes", [{}, {1: "1 Byte", 2: "4 Bytes"}])
def test_each_value_is_told_as_its_evaluation_shows_it(
    application: Application, sizes: dict[int, str]
) -> None:
    linked = {1, 2}
    values = {_FIRST: "1", _SHOWN: "1"}
    ui = recover_mod.seed_dynamic_ui(application, values)
    assert ui is not None
    told = ShownObjects(application, ui, sizes, linked)
    for ref_id in (_FIRST, _SECOND):
        for value in ("0", "1", "2", "3"):
            trial = {**values, ref_id: value}
            seeded = recover_mod.seed_dynamic_ui(application, trial)
            assert seeded is not None
            objects, others = _evaluated(application, trial, sizes)
            guess = told.guess(ref_id, value)
            if guess is None:
                told.learn(ref_id, value, seeded, (objects, others, False))
                guess = told.guess(ref_id, value)
            assert guess == (objects, others, False)
    assert told.guess(_SHOWN, "0") is None
    assert told.guess(_TWICE, "1") is None


def test_a_setting_shown_otherwise_than_told_is_evaluated(
    application: Application,
) -> None:
    ui = recover_mod.seed_dynamic_ui(application, {})
    assert ui is not None
    told = ShownObjects(application, ui, {}, {1})
    seeded = recover_mod.seed_dynamic_ui(application, {_FIRST: "1"})
    assert seeded is not None
    # As if the evaluation had shown nothing but object 3.
    told.learn(_FIRST, "1", seeded, ({1}, 1, False))
    assert told.guess(_FIRST, "2") is None
    assert told.guess(_FIRST, "0") is None


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, str]]]:
    """The values of each UI the search seeds."""
    seeded: list[dict[str, str]] = []
    original = recover_mod.seed_dynamic_ui

    def seed(
        application: Application, values: dict[str, str], *args: object, **kw: object
    ) -> object:
        seeded.append(dict(values))
        return original(application, values, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(recover_mod, "seed_dynamic_ui", seed)
    yield seeded


def _recover(application: Application) -> dict[str, str]:
    return recover_parameters(
        application,
        {},
        linked_objects={1, 2},
        object_sizes={1: "1 Byte", 2: "4 Bytes"},
    ).values


def test_the_search_ends_as_with_every_value_evaluated(
    application: Application,
    builds: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    told = _recover(application)
    told_builds = len(builds)
    assert told[_FIRST] == "1"
    assert told[_SECOND] == "3"
    builds.clear()
    monkeypatch.setattr(object_choices, "object_selectors", lambda _app: {})  # pyright: ignore[reportUnknownLambdaType]
    assert _recover(application) == told
    assert told_builds < len(builds)

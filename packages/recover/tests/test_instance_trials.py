"""The values of module instances' settings the device keeps no copy of are tried a value of each
instance at a time: an instance's evaluation reads only its own values and the application's, so
a UI seeded with a value in each of several instances shows in each what the value alone shows
there. What each value shows is the same as when it is tried alone."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.activation import shown_parameters
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import UiParameter
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F8-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_GENERAL = f"{_APP}_P-1_R-1"


def _instance(number: int) -> str:
    return f"{_MD}_M-{number}_MI-1"


def _mode(number: int) -> str:
    return f"{_instance(number)}_P-1_R-1"


def _extra(number: int) -> str:
    return f"{_instance(number)}_P-3_R-3"


def _delay(number: int) -> str:
    return f"{_instance(number)}_P-2_R-2"


def _object(object_id: str, number: int) -> str:
    return (
        f'<ComObject Id="{object_id}" Name="O{number}" Text="Object {number}" Number="{number}"'
        ' FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled"'
        ' CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled"'
        f' ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" BaseNumber="{_MD}_A-2" />'
    )


def _module(number: int) -> str:
    return (
        f'<Module Id="{_MD}_M-{number}" RefId="{_MD}" Name="Channel {number}">'
        f'<NumericArg RefId="{_MD}_A-1" Value="{number - 1}" />'
        f'<NumericArg RefId="{_MD}_A-2" Value="{4 * (number - 1)}" /></Module>'
    )


# Three channels, each with a stored delay and an unstored mode: mode 1 shows object 2 of the
# channel, mode 2 objects 2 and 3, mode 3 the unstored "Extra" setting, whose value 1 shows
# object 4.
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="248" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Channels with a mode"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-M" Name="Mode">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="3" />
          </ParameterType>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="General" ParameterType="{_APP}_PT-N" Text="General" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="3" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs><ParameterRef Id="{_GENERAL}" RefId="{_APP}_P-1" /></ParameterRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments>
            <Argument Id="{_MD}_A-1" Name="Offset" />
            <Argument Id="{_MD}_A-2" Name="ObjectBase" />
          </Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Mode" ParameterType="{_APP}_PT-M" Text="Mode" Value="0" />
              <Parameter Id="{_MD}_P-2" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="3">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Parameter Id="{_MD}_P-3" Name="Extra" ParameterType="{_APP}_PT-B" Text="Extra" Value="0" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
              <ParameterRef Id="{_MD}_P-2_R-2" RefId="{_MD}_P-2" />
              <ParameterRef Id="{_MD}_P-3_R-3" RefId="{_MD}_P-3" />
            </ParameterRefs>
            <ComObjects>
              {_object(f"{_MD}_O-2-1", 1)}
              {_object(f"{_MD}_O-2-2", 2)}
              {_object(f"{_MD}_O-2-3", 3)}
              {_object(f"{_MD}_O-2-4", 4)}
            </ComObjects>
            <ComObjectRefs>
              <ComObjectRef Id="{_MD}_O-2-1_R-1" RefId="{_MD}_O-2-1" />
              <ComObjectRef Id="{_MD}_O-2-2_R-2" RefId="{_MD}_O-2-2" />
              <ComObjectRef Id="{_MD}_O-2-3_R-3" RefId="{_MD}_O-2-3" />
              <ComObjectRef Id="{_MD}_O-2-4_R-4" RefId="{_MD}_O-2-4" />
            </ComObjectRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ComObjectRefRef RefId="{_MD}_O-2-1_R-1" />
              <ParameterRefRef RefId="{_MD}_P-2_R-2" />
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <choose ParamRefId="{_MD}_P-1_R-1">
                <when test="1"><ComObjectRefRef RefId="{_MD}_O-2-2_R-2" /></when>
                <when test="2">
                  <ComObjectRefRef RefId="{_MD}_O-2-2_R-2" />
                  <ComObjectRefRef RefId="{_MD}_O-2-3_R-3" />
                </when>
                <when test="3">
                  <ParameterRefRef RefId="{_MD}_P-3_R-3" />
                  <choose ParamRefId="{_MD}_P-3_R-3">
                    <when test="1"><ComObjectRefRef RefId="{_MD}_O-2-4_R-4" /></when>
                  </choose>
                </when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_GENERAL}" />
          </ParameterBlock>
        </ChannelIndependentBlock>
        {_module(1)}
        {_module(2)}
        {_module(3)}
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""

_Told = dict[tuple[str, str], tuple[set[int], int, bool]]


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, values: Mapping[str, str]) -> dict[str, bytes]:
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values.items():
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


# Channel 1 in mode 2 (objects 1-3), channel 2 in mode 1 (objects 5, 6), channel 3 in mode 3
# with its extra setting on (objects 9, 12).
_VALUES = {
    _mode(1): "2",
    _mode(2): "1",
    _mode(3): "3",
    _extra(3): "1",
    _delay(1): "5",
    _delay(2): "6",
    _delay(3): "7",
}
_LINKED = {1, 2, 3, 5, 6, 9, 12}


def _told(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Callable[..., object], _Told]]:
    """Collect what each call of _instance_trials tells, with what tries a value alone."""
    found: list[tuple[Callable[..., object], _Told]] = []
    trials = parameters._instance_trials  # pyright: ignore[reportPrivateUsage]

    def collected(
        application: Application,
        ui: DynamicUI,
        values: Mapping[str, str],
        instances: Sequence[tuple[str, str]],
        tried: Sequence[tuple[str, str]],
        sizes: Mapping[int, str],
        linked: set[int],
        project_code_page: str | None,
        reveals_in: Callable[[DynamicUI, Iterable[tuple[str, UiParameter]]], bool],
        cancel: Callable[[], bool] | None,
    ) -> _Told:
        told = trials(
            application,
            ui,
            values,
            instances,
            tried,
            sizes,
            linked,
            project_code_page,
            reveals_in,
            cancel,
        )

        def alone(ref_id: str, value: str) -> tuple[set[int], int, bool]:
            seeded = parameters._seeded(  # pyright: ignore[reportPrivateUsage]
                application,
                {**values, ref_id: value},
                project_code_page,
                module_instances=instances,
            )
            assert seeded is not None
            objects = parameters._shown_objects(seeded, sizes)  # pyright: ignore[reportPrivateUsage]
            return (
                objects & linked,
                len(objects - linked),
                reveals_in(seeded, shown_parameters(seeded.ui()).items()),
            )

        found.append((alone, dict(told)))
        return told

    monkeypatch.setattr(parameters, "_instance_trials", collected)
    return found


def test_each_value_shows_what_it_shows_alone(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    told = _told(monkeypatch)
    recovered = recover_parameters(
        application, _device(application, _VALUES), linked_objects=_LINKED
    )
    assert {key: recovered.values.get(key) for key in _VALUES} == _VALUES
    assert any(found for _alone, found in told)
    for alone, found in told:
        for (ref_id, value), result in found.items():
            assert alone(ref_id, value) == result, (ref_id, value)


def test_the_values_found_are_those_tried_alone(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    segments = _device(application, _VALUES)
    together = recover_parameters(application, segments, linked_objects=_LINKED)
    monkeypatch.setattr(
        parameters,
        "_instance_trials",
        lambda *_args, **_kwargs: {},  # pyright: ignore[reportUnknownLambdaType]
    )
    alone = recover_parameters(application, segments, linked_objects=_LINKED)
    assert together == alone


def test_a_value_tried_with_others_reveals_a_setting(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mode 3 of a channel shows its "Extra" setting, which shows no object yet.
    told = _told(monkeypatch)
    recover_parameters(
        application, _device(application, _VALUES), linked_objects=_LINKED
    )
    revealing = {
        key for _alone, found in told for key, result in found.items() if result[2]
    }
    assert (_mode(3), "3") in revealing


def test_a_single_instance_is_tried_alone(application: Application) -> None:
    ui = parameters._seeded(application, {_delay(1): "5"}, None)  # pyright: ignore[reportPrivateUsage]
    assert ui is not None
    found = parameters._instance_trials(  # pyright: ignore[reportPrivateUsage]
        application,
        ui,
        {_delay(1): "5"},
        ui.get_module_instances(),
        [(_mode(1), "1"), (_mode(1), "2")],
        {},
        {1, 2},
        None,
        lambda _ui, _parameters: False,  # pyright: ignore[reportUnknownLambdaType]
        None,
    )
    assert found == {}


def test_a_setting_revealed_in_one_instance_is_told_for_that_instance_only(
    application: Application,
) -> None:
    values = {_delay(1): "5", _delay(2): "6"}
    ui = parameters._seeded(application, values, None)  # pyright: ignore[reportPrivateUsage]
    assert ui is not None
    shown = shown_parameters(ui.ui())

    def reveals_in(
        seeded: DynamicUI, settings: Iterable[tuple[str, UiParameter]]
    ) -> bool:
        return any(ref_id not in shown for ref_id, _parameter in settings)

    found = parameters._instance_trials(  # pyright: ignore[reportPrivateUsage]
        application,
        ui,
        values,
        ui.get_module_instances(),
        [(_mode(1), "3"), (_mode(2), "1")],
        {},
        {1, 2, 5, 6},
        None,
        reveals_in,
        None,
    )
    # Mode 3 shows channel 1's extra setting, mode 1 channel 2's object 6; channel 3's
    # object 9 is not linked.
    assert found == {
        (_mode(1), "3"): ({1, 5}, 1, True),
        (_mode(2), "1"): ({1, 5, 6}, 1, False),
    }


def test_values_that_lay_out_more_than_there_is_room_for_are_tried_alone(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xknxeditor.prod.parser_v2.allocator import AllocatorOverflow

    values = {_delay(1): "5", _delay(2): "6"}
    ui = parameters._seeded(application, values, None)  # pyright: ignore[reportPrivateUsage]
    assert ui is not None
    instances = ui.get_module_instances()

    def overflowing(*_args: object, **_kwargs: object) -> DynamicUI:
        raise AllocatorOverflow("full", "allocator", None)

    monkeypatch.setattr(parameters, "_seeded", overflowing)
    found = parameters._instance_trials(  # pyright: ignore[reportPrivateUsage]
        application,
        ui,
        values,
        instances,
        [(_mode(1), "3"), (_mode(2), "1")],
        {},
        {1, 2, 5, 6},
        None,
        lambda _ui, _settings: False,  # pyright: ignore[reportUnknownLambdaType]
        None,
    )
    assert found == {}

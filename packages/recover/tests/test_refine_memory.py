"""A setting the device keeps no copy of is corrected where the memory the result would write
still differs from the device: a channel whose own settings hold values on the device is active,
even when no group object tells (issue #207: most of the settings left missing were of such
channels)."""

from __future__ import annotations

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover import RecoveredDevice, search_again
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters

_APP = "M-00FA_A-0006-01-0000"
_CHANNEL = f"{_APP}_P-1_R-1"
_MODE = f"{_APP}_P-2_R-2"
_DELAY = f"{_APP}_P-3_R-3"
_STEP = f"{_APP}_P-4_R-4"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="6" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Channel in memory"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-OnOff" Name="OnOff">
                <TypeRestriction Base="Value" SizeInBit="8">
                  <Enumeration Text="inactive" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
                  <Enumeration Text="active" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Channel" ParameterType="{_APP}_PT-OnOff" Text="Channel" Value="0" />
              <Parameter Id="{_APP}_P-2" Name="Mode" ParameterType="{_APP}_PT-OnOff" Text="Extended" Value="0" />
              <Parameter Id="{_APP}_P-3" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="0">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-4" Name="Step" ParameterType="{_APP}_PT-N" Text="Step" Value="0">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="1" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_CHANNEL}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_MODE}" RefId="{_APP}_P-2" />
              <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-3" />
              <ParameterRef Id="{_STEP}" RefId="{_APP}_P-4" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_CHANNEL}" />
                <choose ParamRefId="{_CHANNEL}">
                  <when test="1">
                    <ParameterRefRef RefId="{_DELAY}" />
                    <ParameterRefRef RefId="{_MODE}" />
                    <choose ParamRefId="{_MODE}">
                      <when test="1"><ParameterRefRef RefId="{_STEP}" /></when>
                    </choose>
                  </when>
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


def _device(application: Application, *values: tuple[str, str]) -> dict[str, bytes]:
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values:
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


def test_a_channel_whose_settings_hold_values_is_active(
    application: Application,
) -> None:
    segments = _device(application, (_CHANNEL, "1"), (_DELAY, "42"))
    recovered = recover_parameters(application, segments)
    assert recovered.values[_CHANNEL] == "1"
    assert recovered.values[_DELAY] == "42"


def test_a_setting_two_selectors_deep_is_found(application: Application) -> None:
    # The step only shows with the channel active and the mode extended: the mode is found once
    # the channel is.
    segments = _device(
        application, (_CHANNEL, "1"), (_MODE, "1"), (_DELAY, "5"), (_STEP, "9")
    )
    recovered = recover_parameters(application, segments)
    assert recovered.values[_MODE] == "1"
    assert recovered.values[_STEP] == "9"


def test_a_channel_holding_only_initial_values_stays_inactive(
    application: Application,
) -> None:
    # Nothing on the device tells the channel is active: it stays as it is.
    segments = _device(application, (_CHANNEL, "1"))
    recovered = recover_parameters(application, segments)
    assert recovered.values.get(_CHANNEL, "0") == "0"


def test_a_search_stopped_at_its_limit_says_more_effort_may_help(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The step takes two corrections, the channel and then the mode: one round is not enough.
    monkeypatch.setattr(parameters, "_REFINE_ROUNDS", 1)
    segments = _device(
        application, (_CHANNEL, "1"), (_MODE, "1"), (_DELAY, "5"), (_STEP, "9")
    )
    fast = recover_parameters(application, segments)
    assert not fast.complete
    assert fast.differing_bytes == 1
    assert fast.limited
    # More effort allows more rounds.
    medium = recover_parameters(application, segments, effort=RecoveryEffort.MEDIUM)
    assert medium.complete
    assert not medium.limited
    assert medium.values[_STEP] == "9"


def test_an_unlimited_search_goes_on_until_nothing_fits_better(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No round or value limit: the two corrections are made even with one round per effort.
    monkeypatch.setattr(parameters, "_REFINE_ROUNDS", 1)
    monkeypatch.setattr(parameters, "_REFINE_TRIES", 1)
    segments = _device(
        application, (_CHANNEL, "1"), (_MODE, "1"), (_DELAY, "5"), (_STEP, "9")
    )
    unlimited = recover_parameters(
        application, segments, effort=RecoveryEffort.UNLIMITED
    )
    assert unlimited.complete
    assert unlimited.values[_STEP] == "9"


def test_a_cancelled_search_ends_with_the_result_so_far(
    application: Application,
) -> None:
    segments = _device(
        application, (_CHANNEL, "1"), (_MODE, "1"), (_DELAY, "5"), (_STEP, "9")
    )
    cancelled = recover_parameters(
        application, segments, effort=RecoveryEffort.UNLIMITED, cancel=lambda: True
    )
    # Stopped before the first correction: the channel stays off, and more may be found.
    assert not cancelled.complete
    assert cancelled.limited
    assert cancelled.values.get(_CHANNEL, "0") == "0"


def test_a_device_is_searched_again_in_what_was_read(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parameters, "_REFINE_ROUNDS", 1)
    segments = _device(
        application, (_CHANNEL, "1"), (_MODE, "1"), (_DELAY, "5"), (_STEP, "9")
    )
    device = RecoveredDevice(
        address="1.1.7",
        application_id=application.id,
        device_address=None,
        group_addresses=[],
        links=[],
        group_objects={},
        parameters=recover_parameters(application, segments),
        parameter_segments=segments,
    )
    assert not device.parameters.complete
    again = search_again(device, application, RecoveryEffort.MEDIUM)
    assert again.parameters.complete
    assert again.parameters.values[_STEP] == "9"
    assert again.effort is RecoveryEffort.MEDIUM
    # The rest of what was read stays.
    assert again.address == "1.1.7"
    assert again.parameter_segments == segments

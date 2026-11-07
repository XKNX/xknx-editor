"""A setting the device keeps no copy of is corrected where the memory the result would write
still differs from the device: a channel whose own settings hold values on the device is active,
even when no group object tells (issue #207: most of the settings left missing were of such
channels)."""

from __future__ import annotations

import base64

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
    fast = recover_parameters(application, segments, effort=RecoveryEffort.FAST)
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
        parameters=recover_parameters(
            application, segments, effort=RecoveryEffort.FAST
        ),
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


def test_a_cancel_is_heard_inside_the_inference(application: Application) -> None:
    # Asked between the values the inference tries too, not only between corrections: the
    # search ends at the first "yes" and asks no more.
    segments = _device(
        application, (_CHANNEL, "1"), (_MODE, "1"), (_DELAY, "5"), (_STEP, "9")
    )
    asked: list[bool] = []

    def cancel() -> bool:
        asked.append(len(asked) >= 1)
        return asked[-1]

    recovered = recover_parameters(
        application, segments, effort=RecoveryEffort.UNLIMITED, cancel=cancel
    )
    assert asked == [False, True]
    assert recovered.limited
    assert not recovered.complete


def _channel_with_objects(objects: int) -> Application:
    """A channel the device keeps no copy of: on, it shows a setting in the second byte and
    ``objects`` group objects (numbers 1 and up); object 0 always shows."""
    app = "M-00FA_A-0007-01-0000"
    numbers = range(objects + 1)
    table = "".join(
        f'<ComObject Id="{app}_O-{n}" Name="O{n}" Text="Object {n}" Number="{n}"'
        ' FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled"'
        ' CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled"'
        ' ReadOnInitFlag="Disabled" />'
        for n in numbers
    )
    refs = "".join(
        f'<ComObjectRef Id="{app}_O-{n}_R-{n}" RefId="{app}_O-{n}" />' for n in numbers
    )
    shown = "".join(
        f'<ComObjectRefRef RefId="{app}_O-{n}_R-{n}" />' for n in numbers[1:]
    )
    xml = _XML.replace(_APP, app).replace(
        "</ParameterRefs>",
        f"</ParameterRefs><ComObjectTable>{table}</ComObjectTable>"
        f"<ComObjectRefs>{refs}</ComObjectRefs>",
    )
    # The step shows with the channel, with its objects; the mode and the delay never.
    xml = xml.replace(
        f'<ParameterRefRef RefId="{app}_P-3_R-3" />\n'
        f'                    <ParameterRefRef RefId="{app}_P-2_R-2" />\n'
        f'                    <choose ParamRefId="{app}_P-2_R-2">\n'
        f'                      <when test="1"><ParameterRefRef RefId="{app}_P-4_R-4" /></when>\n'
        "                    </choose>",
        f'<ParameterRefRef RefId="{app}_P-4_R-4" />{shown}',
    ).replace(
        "</ParameterBlock>",
        f'</ParameterBlock><ComObjectRefRef RefId="{app}_O-0_R-0" />',
    )
    assert shown in xml
    return parse_application_xml(xml.encode(), "M-00FA")[0]


@pytest.mark.parametrize(("objects", "active"), [(2, True), (8, False)])
def test_a_channel_showing_unlinked_objects_takes_more_than_a_byte(
    objects: int, active: bool
) -> None:
    # The second byte holds 7, though the channel is off on the device (data the device keeps
    # there at run time, say). Turning the channel on explains the byte, and shows objects the
    # device does not link: a few weigh less than the byte, a whole channel of them more.
    application = _channel_with_objects(objects)
    channel = f"{application.id}_P-1_R-1"
    segments = _device(application)
    segment = next(iter(segments))
    segments[segment] = segments[segment][:1] + b"\x07" + segments[segment][2:]
    recovered = recover_parameters(application, segments, linked_objects={0})
    assert (recovered.values.get(channel, "0") == "1") is active


_PAIR_APP = "M-00FA_A-0008-01-0000"


def _pair_application() -> Application:
    """A channel ``CH`` (no memory): off it shows ``A`` in the first byte; on, the sub-selectors
    ``S1`` and ``S2`` (no memory), each choosing for its byte a parameter of the values 1 and 2 or
    one of the values 3 and 4."""
    app = _PAIR_APP

    def parameter(n: int, type_id: str, offset: int | None) -> str:
        memory = (
            ""
            if offset is None
            else f'<Memory CodeSegment="{app}_AS-0000" Offset="{offset}" BitOffset="0" />'
        )
        return (
            f'<Parameter Id="{app}_P-{n}" Name="P{n}" ParameterType="{app}_PT-{type_id}"'
            f' Text="P{n}" Value="{1 if type_id == "Low" else 3 if type_id == "High" else 0}">'
            f"{memory}</Parameter>"
        )

    def enumeration(type_id: str, *values: int) -> str:
        return (
            f'<ParameterType Id="{app}_PT-{type_id}" Name="{type_id}">'
            '<TypeRestriction Base="Value" SizeInBit="8">'
            + "".join(
                f'<Enumeration Text="v{v}" Value="{v}" Id="{app}_PT-{type_id}_EN-{v}" />'
                for v in values
            )
            + "</TypeRestriction></ParameterType>"
        )

    # 1 CH, 2 S1, 3 S2 (no memory); 4 A (byte 0); 5/6 the two of byte 1; 7/8 those of byte 2.
    parameters = (
        parameter(1, "OnOff", None)
        + parameter(2, "OnOff", None)
        + parameter(3, "OnOff", None)
        + f'<Parameter Id="{app}_P-4" Name="P4" ParameterType="{app}_PT-N" Text="A" Value="0">'
        f'<Memory CodeSegment="{app}_AS-0000" Offset="0" BitOffset="0" /></Parameter>'
        + parameter(5, "Low", 1)
        + parameter(6, "High", 1)
        + parameter(7, "Low", 2)
        + parameter(8, "High", 2)
    )
    refs = "".join(
        f'<ParameterRef Id="{app}_P-{n}_R-{n}" RefId="{app}_P-{n}" />'
        for n in range(1, 9)
    )

    def ref(n: int) -> str:
        return f'<ParameterRefRef RefId="{app}_P-{n}_R-{n}" />'

    def choose(n: int, off: str, on: str) -> str:
        return (
            f'<choose ParamRefId="{app}_P-{n}_R-{n}"><when test="0">{off}</when>'
            f'<when test="1">{on}</when></choose>'
        )

    dynamic = ref(1) + choose(
        1,
        ref(4),
        ref(2) + choose(2, ref(5), ref(6)) + ref(3) + choose(3, ref(7), ref(8)),
    )
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{app}" ApplicationNumber="8" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Channel pairs"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{app}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>{enumeration("OnOff", 0, 1)}{enumeration("Low", 1, 2)}{enumeration("High", 3, 4)}
          <ParameterType Id="{app}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>{parameters}</Parameters>
        <ParameterRefs>{refs}</ParameterRefs>
      </Static>
      <Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app}_PB-1" Text="General">{dynamic}</ParameterBlock></ChannelIndependentBlock></Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def test_a_channel_that_fits_only_with_its_own_selectors_is_found() -> None:
    # The channel on alone fits no better than off (its two bytes decode to no value of the
    # parameters it shows by default); with its sub-selectors on too it fits exactly.
    application = _pair_application()
    on = (
        (f"{_PAIR_APP}_P-1_R-1", "1"),
        (f"{_PAIR_APP}_P-2_R-2", "1"),
        (f"{_PAIR_APP}_P-3_R-3", "1"),
        (f"{_PAIR_APP}_P-6_R-6", "3"),
        (f"{_PAIR_APP}_P-8_R-8", "4"),
    )
    segments = _device(application, *on)
    recovered = recover_parameters(application, segments)
    assert recovered.complete
    for ref_id, value in on:
        assert recovered.values.get(ref_id) == value


def _chain_application(depth: int) -> Application:
    """``depth`` settings in a chain, each in a byte of its own, each shown when the one before
    it is on."""
    app = "M-00FA_A-0009-01-0000"
    parameters = "".join(
        f'<Parameter Id="{app}_P-{n}" Name="P{n}" ParameterType="{app}_PT-OnOff" Text="P{n}"'
        f' Value="0"><Memory CodeSegment="{app}_AS-0000" Offset="{n - 1}" BitOffset="0" />'
        "</Parameter>"
        for n in range(1, depth + 1)
    )
    refs = "".join(
        f'<ParameterRef Id="{app}_P-{n}_R-{n}" RefId="{app}_P-{n}" />'
        for n in range(1, depth + 1)
    )
    dynamic = ""
    for n in range(depth, 0, -1):
        inner = (
            f'<choose ParamRefId="{app}_P-{n}_R-{n}"><when test="1">{dynamic}</when></choose>'
            if dynamic
            else ""
        )
        dynamic = f'<ParameterRefRef RefId="{app}_P-{n}_R-{n}" />{inner}'
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{app}" ApplicationNumber="9" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Chain"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{app}_AS-0000" Address="0" Size="{depth}"><Data>{base64.b64encode(bytes(depth)).decode()}</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{app}_PT-OnOff" Name="OnOff"><TypeRestriction Base="Value" SizeInBit="8">
            <Enumeration Text="off" Value="0" Id="{app}_PT-OnOff_EN-0" />
            <Enumeration Text="on" Value="1" Id="{app}_PT-OnOff_EN-1" />
          </TypeRestriction></ParameterType>
        </ParameterTypes>
        <Parameters>{parameters}</Parameters>
        <ParameterRefs>{refs}</ParameterRefs>
      </Static>
      <Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app}_PB-1" Text="General">{dynamic}</ParameterBlock></ChannelIndependentBlock></Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def test_a_long_chain_of_stored_selectors_is_read_to_its_end() -> None:
    # Each decoding round shows one more setting of the chain: the decoding goes on until
    # nothing changes, not a fixed number of rounds.
    depth = 12
    application = _chain_application(depth)
    on = [(f"{application.id}_P-{n}_R-{n}", "1") for n in range(1, depth + 1)]
    recovered = recover_parameters(application, _device(application, *on))
    assert recovered.complete
    for ref_id, value in on:
        assert recovered.values.get(ref_id) == value


def _hidden_ref_application() -> Application:
    """``P`` (byte 0) with the shown ref ``R-1`` (tag 1) and the hidden ``R-2`` (tag 2, fixed
    value 1), which the download writes last; ``R-1`` off shows ``X`` (byte 1), on ``Y`` (byte 2)."""
    app = "M-00FA_A-000A-01-0000"

    def stored(n: int, offset: int) -> str:
        return (
            f'<Parameter Id="{app}_P-{n}" Name="P{n}" ParameterType="{app}_PT-N" Text="P{n}"'
            f' Value="0"><Memory CodeSegment="{app}_AS-0000" Offset="{offset}" BitOffset="0" />'
            "</Parameter>"
        )

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{app}" ApplicationNumber="10" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Hidden ref"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{app}_AS-0000" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{app}_PT-OnOff" Name="OnOff"><TypeRestriction Base="Value" SizeInBit="8">
            <Enumeration Text="off" Value="0" Id="{app}_PT-OnOff_EN-0" />
            <Enumeration Text="on" Value="1" Id="{app}_PT-OnOff_EN-1" />
          </TypeRestriction></ParameterType>
          <ParameterType Id="{app}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{app}_P-1" Name="P" ParameterType="{app}_PT-OnOff" Text="P" Value="0"><Memory CodeSegment="{app}_AS-0000" Offset="0" BitOffset="0" /></Parameter>
          {stored(2, 1)}{stored(3, 2)}
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{app}_P-1_R-1" RefId="{app}_P-1" Tag="1" />
          <ParameterRef Id="{app}_P-1_R-2" RefId="{app}_P-1" Tag="2" Access="None" Value="1" />
          <ParameterRef Id="{app}_P-2_R-3" RefId="{app}_P-2" />
          <ParameterRef Id="{app}_P-3_R-4" RefId="{app}_P-3" />
        </ParameterRefs>
      </Static>
      <Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app}_PB-1" Text="General">
        <ParameterRefRef RefId="{app}_P-1_R-1" />
        <ParameterRefRef RefId="{app}_P-1_R-2" />
        <choose ParamRefId="{app}_P-1_R-1">
          <when test="0"><ParameterRefRef RefId="{app}_P-2_R-3" /></when>
          <when test="1"><ParameterRefRef RefId="{app}_P-3_R-4" /></when>
        </choose>
      </ParameterBlock></ChannelIndependentBlock></Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def test_a_ref_whose_memory_a_later_ref_sets_is_inferred() -> None:
    # The hidden ref writes 1 into P's byte whatever the shown ref says; the shown ref is off,
    # which shows X. Taking the byte's 1 for the shown ref showed Y instead.
    application = _hidden_ref_application()
    shown, x = f"{application.id}_P-1_R-1", f"{application.id}_P-2_R-3"
    segments = _device(application, (shown, "0"), (x, "5"))
    assert next(iter(segments.values()))[:2] == b"\x01\x05"
    recovered = recover_parameters(application, segments)
    assert recovered.complete
    assert recovered.values.get(shown, "0") == "0"
    assert recovered.values.get(x) == "5"

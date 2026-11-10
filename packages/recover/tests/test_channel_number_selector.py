"""A shown number the device keeps no copy of chooses which channel's settings show: of a range
too large to try every value, the value whose branch places the settings at the bytes that
differ is tried first.

"Channel" (1 to 40, no memory) shows "Power n" of channel n alone. Each "Power n" is stored in
byte n - 1, and a download leaves the bytes of the channels not shown as the segment has them.
So a device configured with channel 37 shown holds its power in byte 36, which only channel 37
writes.
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover import parameters
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F7-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_CHANNELS = 40
_CHANNEL = f"{_APP}_P-1_R-1"


def _power(channel: int) -> str:
    return f"{_APP}_P-{100 + channel}_R-{100 + channel}"


_PARAMETERS = "".join(
    f'<Parameter Id="{_APP}_P-{100 + n}" Name="Power{n}" ParameterType="{_APP}_PT-N"'
    f' Text="Power {n}" Value="10">'
    f'<Memory CodeSegment="{_SEGMENT}" Offset="{n - 1}" BitOffset="0" /></Parameter>'
    for n in range(1, _CHANNELS + 1)
)
_REFS = "".join(
    f'<ParameterRef Id="{_power(n)}" RefId="{_APP}_P-{100 + n}" />'
    for n in range(1, _CHANNELS + 1)
)
_BRANCHES = "".join(
    f'<when test="{n}"><ParameterRefRef RefId="{_power(n)}" /></when>'
    for n in range(1, _CHANNELS + 1)
)

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="247" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Channel number"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="{_CHANNELS}" /></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-C" Name="C"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="1" maxInclusive="{_CHANNELS}" /></ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Channel" ParameterType="{_APP}_PT-C" Text="Channel" Value="1" />
          {_PARAMETERS}
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_CHANNEL}" RefId="{_APP}_P-1" />
          {_REFS}
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_CHANNEL}" />
            <choose ParamRefId="{_CHANNEL}">{_BRANCHES}</choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, channel: int, power: str) -> dict[str, bytes]:
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_CHANNEL, str(channel))
    ui.set_parameter_ref(_power(channel), power)
    return ui.encode_to_memory()


def test_only_the_channel_shown_is_written() -> None:
    segments = _device(_application(), 37, "99")
    assert segments[_SEGMENT] == bytes(36) + b"\x63" + bytes(3)


def test_the_channel_whose_settings_the_memory_holds_is_found() -> None:
    application = _application()
    segments = _device(application, 37, "99")
    recovered = recover_parameters(application, segments)
    assert recovered.values[_CHANNEL] == "37"
    assert recovered.values[_power(37)] == "99"
    assert recovered.complete
    assert recovered.differing_bytes == 0


def test_a_neighbour_of_the_value_now_is_found_as_before() -> None:
    application = _application()
    segments = _device(application, 2, "99")
    recovered = recover_parameters(application, segments)
    assert recovered.values[_CHANNEL] == "2"
    assert recovered.complete


def test_the_branch_values_of_the_settings_at_the_bytes_that_differ() -> None:
    application = _application()
    ui = application.dynamic_ui()
    assert ui is not None
    found = parameters._branch_values(application, ui, {_power(37), _power(5)})  # pyright: ignore[reportPrivateUsage]
    assert found == {_CHANNEL: ["5", "37"]}
    assert parameters._branch_values(application, ui, set()) == {}  # pyright: ignore[reportPrivateUsage]


def test_without_the_application_the_trials_stay_the_neighbours_and_ends() -> None:
    application = _application()
    segments = _device(application, 37, "99")
    ui = application.dynamic_ui()
    assert ui is not None
    differing = {(_SEGMENT, 36)}
    plain = parameters._refinement_trials(ui, differing, set())  # pyright: ignore[reportPrivateUsage]
    assert (_CHANNEL, "37") not in plain
    trials = parameters._refinement_trials(  # pyright: ignore[reportPrivateUsage]
        ui, differing, set(), application=application
    )
    assert trials[0] == (_CHANNEL, "37")
    assert segments[_SEGMENT][36] == 99

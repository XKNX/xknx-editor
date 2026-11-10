"""Three channel switches the device keeps no copy of, each showing two settings of its own
channel, stored a bit each in two bytes all channels share.

None of the switches alone brings the result closer in bytes: switched on alone, it fits its
own bits of both bytes, and the bits of the other channels still differ. Only with all of
them switched on the bytes the device holds are written.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0D09-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_CHANNELS = (1, 2, 3)


def _switch(channel: int) -> str:
    return f"{_APP}_P-{channel}_R-{channel}"


def _setting(channel: int, byte: int) -> str:
    number = 10 * channel + byte
    return f"{_APP}_P-{number}_R-{number}"


def _parameters() -> str:
    lines: list[str] = []
    for channel in _CHANNELS:
        lines.append(
            f'<Parameter Id="{_APP}_P-{channel}" Name="Switch{channel}" ParameterType="{_APP}_PT-B"'
            f' Text="Scenes" Value="0" />'
        )
        for byte in (0, 1):
            lines.append(
                f'<Parameter Id="{_APP}_P-{10 * channel + byte}" Name="S{channel}{byte}"'
                f' ParameterType="{_APP}_PT-B" Text="Setting" Value="0">'
                f'<Memory CodeSegment="{_SEGMENT}" Offset="{byte}" BitOffset="{channel}" />'
                "</Parameter>"
            )
    return "\n".join(lines)


def _refs() -> str:
    refs = [_switch(channel) for channel in _CHANNELS] + [
        _setting(channel, byte) for channel in _CHANNELS for byte in (0, 1)
    ]
    return "\n".join(
        f'<ParameterRef Id="{ref}" RefId="{ref.rsplit("_R-", 1)[0]}" />' for ref in refs
    )


def _blocks() -> str:
    return "\n".join(
        f"""<ParameterRefRef RefId="{_switch(channel)}" />
            <choose ParamRefId="{_switch(channel)}">
              <when test="1">
                <ParameterRefRef RefId="{_setting(channel, 0)}" />
                <ParameterRefRef RefId="{_setting(channel, 1)}" />
              </when>
            </choose>"""
        for channel in _CHANNELS
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="15" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Shared bytes"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          {_parameters()}
        </Parameters>
        <ParameterRefs>
          {_refs()}
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="Scenes">
            {_blocks()}
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""

_CONFIGURED = {
    **{_switch(channel): "1" for channel in _CHANNELS},
    **{_setting(channel, byte): "1" for channel in _CHANNELS for byte in (0, 1)},
}


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, values: dict[str, str]) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_every_channel_shares_both_bytes(application: Application) -> None:
    assert _device(application, _CONFIGURED)[_SEGMENT] == bytes([0x70, 0x70])
    # One channel switched off leaves both bytes differing from the device in its bits.
    one_off = {**_CONFIGURED, _switch(2): "0"}
    assert _device(application, one_off)[_SEGMENT] == bytes([0x50, 0x50])


def test_every_switch_is_found(application: Application) -> None:
    recovered = recover_parameters(
        application,
        _device(application, _CONFIGURED),
        effort=RecoveryEffort.MEDIUM,
    )
    assert recovered.complete
    assert {key: recovered.values.get(key) for key in _CONFIGURED} == _CONFIGURED

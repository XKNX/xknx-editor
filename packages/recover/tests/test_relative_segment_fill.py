"""A relative segment a full download fills when it allocates it (``LdCtrlRelSegment`` with
mode 1): where no active setting is written (a hidden one, which the application downloads
not at all with ``DownloadInvisibleParameters="None"``, or a byte no setting is stored at),
the device holds the fill. A result that hides the setting fits a device holding the fill
there; one holding another value still tells the setting was shown.

"Channel" is stored in byte 0 and shows "Delay", stored in byte 1; bytes 2 and 3 hold no
setting.
"""

from __future__ import annotations

import xknxeditor.recover.parameters as parameters
from xknxeditor.download import relative_segment_fills
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F8-01-0000"
_SEGMENT = f"{_APP}_RS-04-00000"
_CHANNEL = f"{_APP}_P-1_R-1"
_DELAY = f"{_APP}_P-2_R-2"
_FILLED = (
    '<LdCtrlRelSegment AppliesTo="full" LsmIdx="4" Size="4" Mode="1" Fill="255" />'
    '<LdCtrlRelSegment AppliesTo="par" LsmIdx="4" Size="4" Mode="0" Fill="255" />'
)


def _application(allocation: str = _FILLED) -> Application:
    """The application, with ``allocation`` allocating its relative segment."""
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="248" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Filled segment"
        LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><RelativeSegment Id="{_SEGMENT}" Name="Parameters" Offset="0" Size="4" LoadStateMachine="4" /></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
          <ParameterType Id="{_APP}_PT-D" Name="D"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="60" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Channel" ParameterType="{_APP}_PT-B" Text="Channel" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-2" Name="Delay" ParameterType="{_APP}_PT-D" Text="Delay" Value="5">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_CHANNEL}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-2" />
        </ParameterRefs>
        <LoadProcedures>
          <LoadProcedure>
            <LdCtrlConnect />
            <LdCtrlUnload LsmIdx="4" />
            <LdCtrlLoad LsmIdx="4" />
            {allocation}
            <LdCtrlWriteRelMem ObjIdx="4" Offset="0" Size="4" Verify="true" />
            <LdCtrlLoadCompleted LsmIdx="4" />
            <LdCtrlRestart />
          </LoadProcedure>
        </LoadProcedures>
        <Options DownloadInvisibleParameters="None" />
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_CHANNEL}" />
            <choose ParamRefId="{_CHANNEL}">
              <when test="1">
                <ParameterRefRef RefId="{_DELAY}" />
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def test_the_full_download_fills_the_segment() -> None:
    assert relative_segment_fills(_application(), None) == {_SEGMENT: 255}


def test_a_segment_allocated_without_fill_has_none() -> None:
    zeros = '<LdCtrlRelSegment LsmIdx="4" Size="4" Mode="1" Fill="0" />'
    kept = '<LdCtrlRelSegment LsmIdx="4" Size="4" Mode="0" Fill="255" />'
    assert relative_segment_fills(_application(zeros), None) == {}
    assert relative_segment_fills(_application(kept), None) == {}


def test_the_fill_where_the_hidden_setting_is_stored_fits_the_hidden_setting() -> None:
    recovered = recover_parameters(_application(), {_SEGMENT: b"\x00\xff\xff\xff"})
    assert recovered.values[_CHANNEL] == "0"
    assert recovered.complete
    assert recovered.differing_bytes == 0


def test_the_shown_setting_is_recovered_over_the_fill() -> None:
    recovered = recover_parameters(_application(), {_SEGMENT: b"\x01\x07\xff\xff"})
    assert recovered.values[_CHANNEL] == "1"
    assert recovered.values[_DELAY] == "7"
    assert recovered.complete


def test_another_value_where_the_hidden_setting_is_stored_still_differs() -> None:
    recovered = recover_parameters(_application(), {_SEGMENT: b"\x00\x07\xff\xff"})
    assert not recovered.complete


def test_without_fill_the_hidden_setting_fits_zeros_only() -> None:
    kept = '<LdCtrlRelSegment LsmIdx="4" Size="4" Mode="0" Fill="255" />'
    application = _application(kept)
    assert recover_parameters(application, {_SEGMENT: b"\x00\x00\x00\x00"}).complete
    assert not recover_parameters(application, {_SEGMENT: b"\x00\xff\xff\xff"}).complete


def test_the_seed_value_where_the_fill_belongs_still_differs() -> None:
    # The encoding leaves the hidden setting's byte at 0, as the device holds it: the full
    # download fills it, so a device holding 0 there was not downloaded with it hidden.
    recovered = recover_parameters(_application(), {_SEGMENT: b"\x00\x00\xff\xff"})
    assert not recovered.complete


def test_the_bits_of_a_byte_the_fill_belongs_in_count_against_the_fill() -> None:
    # The hidden setting's byte holds 0x7F: one bit off the fill the download leaves there,
    # not seven off the 0 the encoding has.
    application = _application()
    ui = seed_dynamic_ui(application, {_CHANNEL: "0"})
    assert ui is not None
    device = {_SEGMENT: bytes([0x00, 0x7F, 0xFF, 0xFF])}
    previous = getattr(parameters._SEARCH, "cache", None)  # pyright: ignore[reportPrivateUsage]
    parameters._SEARCH.cache = parameters._SearchCache(application)  # pyright: ignore[reportPrivateUsage]
    try:
        assert parameters._differing_bits(ui, device, [(_SEGMENT, 1)]) == 1  # pyright: ignore[reportPrivateUsage]
    finally:
        parameters._SEARCH.cache = previous  # pyright: ignore[reportPrivateUsage]

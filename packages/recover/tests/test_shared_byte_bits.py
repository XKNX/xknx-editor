"""A byte a setting is stored at can hold other data in its other bits: a product can place a
parameter in the flags of a group object table record, whose other bits the download writes
from the links (the communication flag of an object without a link is cleared). No result
writes those bits otherwise, so they do not make a byte differ; the bits a setting is stored
at still do.

"Debounce" is a 1-bit setting in bit 5 (0x20) of byte 0, whose seed is EFh; "Mode" a 2-bit
setting in the top bits of byte 1 that knows the values 0 and 1 only.
"""

from __future__ import annotations

from typing import Any, cast

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F8-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_DEBOUNCE = f"{_APP}_P-1_R-1"
_MODE = f"{_APP}_P-2_R-2"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="248" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Shared byte"
        LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>7wA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-Bit" Name="Bit">
            <TypeRestriction Base="Value" SizeInBit="1">
              <Enumeration Text="50 ms" Value="0" Id="{_APP}_PT-Bit_EN-0" />
              <Enumeration Text="400 ms" Value="1" Id="{_APP}_PT-Bit_EN-1" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-Mode" Name="Mode">
            <TypeRestriction Base="Value" SizeInBit="2">
              <Enumeration Text="Off" Value="0" Id="{_APP}_PT-Mode_EN-0" />
              <Enumeration Text="On" Value="1" Id="{_APP}_PT-Mode_EN-1" />
            </TypeRestriction>
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Debounce" ParameterType="{_APP}_PT-Bit" Text="Debounce" Value="1">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="2" />
          </Parameter>
          <Parameter Id="{_APP}_P-2" Name="Mode" ParameterType="{_APP}_PT-Mode" Text="Mode" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_DEBOUNCE}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_MODE}" RefId="{_APP}_P-2" />
        </ParameterRefs>
        <LoadProcedures>
          <LoadProcedure>
            <LdCtrlConnect />
            <LdCtrlRestart />
          </LoadProcedure>
        </LoadProcedures>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_DEBOUNCE}" />
            <ParameterRefRef RefId="{_MODE}" />
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_the_stored_bits_are_those_of_the_settings() -> None:
    ui = seed_dynamic_ui(_application(), {})
    assert ui is not None
    assert ui.stored_bit_mask() == {_SEGMENT: b"\x20\xc0"}


def test_other_bits_of_a_byte_a_setting_is_stored_at_do_not_differ() -> None:
    # The device cleared bit 2 (04h) of byte 0, which no setting is stored at.
    recovered = recover_parameters(_application(), {_SEGMENT: b"\xeb\x40"})
    assert recovered.values[_DEBOUNCE] == "1"
    assert recovered.values[_MODE] == "1"
    assert recovered.complete
    assert recovered.differing_bytes == 0


def test_the_setting_in_the_shared_byte_is_read() -> None:
    recovered = recover_parameters(_application(), {_SEGMENT: b"\xcb\x00"})
    assert recovered.values[_DEBOUNCE] == "0"
    assert recovered.complete


def test_bits_a_setting_is_stored_at_still_differ() -> None:
    # 11b in the bits of Mode is no value of its type: no result gives it.
    recovered = recover_parameters(_application(), {_SEGMENT: b"\xef\xc0"})
    assert not recovered.complete
    assert recovered.differing_bytes == 1


class _Stub:
    """The parts of a DynamicUI the comparison asks: byte 0 holds bit 5 of a setting of the
    application and a setting of a module instance whose bits are not known."""

    def __init__(self, owners: frozenset[str]) -> None:
        self._owners = owners

    def encode_to_memory(self) -> dict[str, bytes]:
        return {_SEGMENT: b"\xef"}

    def stored_bit_mask(self) -> dict[str, bytes]:
        return {_SEGMENT: b"\x20"}

    def stored_parameter_cells(self) -> dict[tuple[str, int], frozenset[str]]:
        return {(_SEGMENT, 0): self._owners}


def test_a_byte_a_module_instance_setting_is_stored_at_differs_as_a_whole() -> None:
    _differing_cells = parameters._differing_cells  # pyright: ignore[reportPrivateUsage]
    own = _Stub(frozenset({_DEBOUNCE}))
    shared = _Stub(frozenset({_DEBOUNCE, f"{_APP}_MD-1_M-1_MI-1_P-1_R-1"}))
    assert _differing_cells(cast(Any, own), {_SEGMENT: b"\xeb"}) == set()
    assert _differing_cells(cast(Any, shared), {_SEGMENT: b"\xeb"}) == {(_SEGMENT, 0)}

"""A stored number outside its type's range is downloaded while its bits are a value in range.

A product's plug-in may store an unsigned 32-bit value as a signed one: the bit mask of a DALI
gateway's ballast groups with group 32 set is stored as -2147483648 instead of 2147483648.
Written into the parameter's 32 bits both are 0x80000000, so the download writes it. A value
whose bits are no value of the type is still refused, and a value set in the editor is still
checked against the range.
"""

from __future__ import annotations

import pytest

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.errors import EncodingError
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-0D05-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_MASK = f"{_APP}_P-1_R-1"
_SIGNED = f"{_APP}_P-2_R-2"
_SCENE = f"{_APP}_P-3_R-3"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="17" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Stored number bits"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="9"><Data>AAAAAAAAAAAA</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-4Byte" Name="4Byte"><TypeNumber SizeInBit="32" Type="unsignedInt" minInclusive="0" maxInclusive="4294967295" /></ParameterType>
<ParameterType Id="{_APP}_PT-Signed" Name="Signed"><TypeNumber SizeInBit="32" Type="signedInt" minInclusive="-2147483648" maxInclusive="2147483647" /></ParameterType>
<ParameterType Id="{_APP}_PT-Scene" Name="Scene"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="1" maxInclusive="64" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Groups" ParameterType="{_APP}_PT-4Byte" Text="Groups" Access="None" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-2" Name="Offset" ParameterType="{_APP}_PT-Signed" Text="Offset" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="4" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-3" Name="Scene" ParameterType="{_APP}_PT-Scene" Text="Scene" Value="1">
<Memory CodeSegment="{_SEGMENT}" Offset="8" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_MASK}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_SIGNED}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_SCENE}" RefId="{_APP}_P-3" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_MASK}" />
<ParameterRefRef RefId="{_SIGNED}" />
<ParameterRefRef RefId="{_SCENE}" />
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui(values: dict[str, str]) -> DynamicUI:
    program = parse_application_xml(_XML.encode(), "M-00FA")[0].program
    refs = [ParameterInstanceRef(ref_id=r, value=v) for r, v in values.items()]
    ui = DynamicUI(program, parameter_instance_refs=refs)
    ui.ui()
    return ui


def test_a_signed_value_of_an_unsigned_parameter_is_downloaded_as_its_bits() -> None:
    ui = _ui({_MASK: "-2147483648"})
    assert ui.encode_to_memory()[_SEGMENT][:4] == b"\x80\x00\x00\x00"
    data, mask = ui.encode_to_memory_masked()[_SEGMENT]
    assert data[:4] == b"\x80\x00\x00\x00"
    assert mask[:4] == b"\xff\xff\xff\xff"


def test_an_unsigned_value_of_a_signed_parameter_is_downloaded_as_its_bits() -> None:
    ui = _ui({_SIGNED: "4294967295"})
    assert ui.encode_to_memory()[_SEGMENT][4:8] == b"\xff\xff\xff\xff"


def test_a_value_whose_bits_are_no_value_of_the_type_is_refused() -> None:
    # -1 in 8 bits is 255, outside 1..64; 300 does not fit 8 bits at all.
    for value in ("-1", "300"):
        with pytest.raises(EncodingError, match=r"outside 1\.\.64"):
            _ui({_SCENE: value}).encode_to_memory()


def test_a_value_beyond_the_parameter_size_is_refused() -> None:
    with pytest.raises(EncodingError, match=r"outside 0\.\.4294967295"):
        _ui({_MASK: str(-(1 << 31) - 1)}).encode_to_memory()


def test_a_value_set_in_the_editor_is_still_checked_against_the_range() -> None:
    ui = _ui({})
    with pytest.raises(ValueError):
        ui.edit_parameter(_SIGNED, "4294967295")
    assert ui.get_value(_SIGNED) == "0"


def test_a_value_with_bits_in_range_is_not_logged_as_outside_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        _ui({_MASK: "-2147483646"}).encode_to_memory()
    assert "written as given" not in caplog.text

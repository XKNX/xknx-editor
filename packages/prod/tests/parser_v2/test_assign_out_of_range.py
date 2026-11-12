"""An Assign may write a value outside its target's range; the download writes it as given.

A scene is switched off by an Assign that sets its number to 0, although the number's type
allows 1..64 (a gateway's scene list). The value is kept as the product assigns it and
lands in the memory image while it fits the parameter's size.
"""

from __future__ import annotations

import pytest

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.errors import EncodingError
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-0D04-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_USED = f"{_APP}_P-1_R-1"
_NUMBER = f"{_APP}_P-2_R-2"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="16" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assign out of range"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-Used" Name="Used"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="No" Value="0" Id="{_APP}_PT-Used_EN-0" />
<Enumeration Text="Yes" Value="1" Id="{_APP}_PT-Used_EN-1" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-Scene" Name="Scene"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="1" maxInclusive="64" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Used" ParameterType="{_APP}_PT-Used" Text="Scene used" Value="1">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-2" Name="Number" ParameterType="{_APP}_PT-Scene" Text="Scene number" Value="5">
<Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_USED}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_NUMBER}" RefId="{_APP}_P-2" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_USED}" />
<choose ParamRefId="{_USED}">
<when test="0"><Assign TargetParamRefRef="{_NUMBER}" Value="0" /></when>
<when test="1"><ParameterRefRef RefId="{_NUMBER}" /></when>
</choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui(values: dict[str, str]) -> DynamicUI:
    program = parse_application_xml(_XML.encode(), "M-00FA")[0].program
    refs = [ParameterInstanceRef(ref_id=r, value=v) for r, v in values.items()]
    return DynamicUI(program, parameter_instance_refs=refs)


def test_the_assigned_value_outside_the_range_is_downloaded_as_given() -> None:
    ui = _ui({_USED: "0"})
    ui.ui()
    assert ui.get_value(_NUMBER) == "0"
    assert ui.encode_to_memory()[_SEGMENT] == b"\x00\x00"
    data, mask = ui.encode_to_memory_masked()[_SEGMENT]
    assert (data, mask) == (b"\x00\x00", b"\xff\xff")


def test_an_edit_assigning_the_value_outside_the_range_keeps_the_download() -> None:
    ui = _ui({})
    ui.ui()
    ui.edit_parameter(_USED, "0")
    assert ui.get_value(_NUMBER) == "0"
    assert ui.encode_to_memory()[_SEGMENT] == b"\x00\x00"


def test_a_value_that_is_no_number_is_still_refused() -> None:
    ui = _ui({_USED: "1", _NUMBER: "five"})
    with pytest.raises(EncodingError, match="five"):
        ui.encode_to_memory()


def test_a_value_set_in_the_editor_is_still_checked_against_the_range() -> None:
    ui = _ui({})
    ui.ui()
    with pytest.raises(ValueError):
        ui.edit_parameter(_NUMBER, "0")

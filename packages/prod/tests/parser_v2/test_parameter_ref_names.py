"""A ParameterRef with a ``Name`` of its own is known by that name to calculations and scripts.

Two refs of one parameter, one of them named apart (a read-only copy that shows what a chosen
model supports): a calculation reads both, each under its own name, so the editable ref's value
is not taken from the copy (an air conditioning gateway counting the modes the manual
configuration enables).
"""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-0D04-01-0000"
_FLAG = f"{_APP}_P-1_R-1"
_FLAG_COPY = f"{_APP}_P-1_R-2"
_RESULT = f"{_APP}_P-2_R-3"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="16" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Ref names"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-U1" Name="U1"><TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" /></ParameterType>
<ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="flag" ParameterType="{_APP}_PT-U1" Text="Flag" Value="1">
<Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-2" Name="result" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_FLAG}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_FLAG_COPY}" RefId="{_APP}_P-1" Name="flag_ro" Access="Read" Value="1" />
<ParameterRef Id="{_RESULT}" RefId="{_APP}_P-2" />
</ParameterRefs>
<ParameterCalculations>
<ParameterCalculation Id="{_APP}_PC-1" Name="Combine" Language="JavaScript">
<LRTransformation>result = flag * 10 + flag_ro;</LRTransformation>
<RLTransformation>//Not implemented</RLTransformation>
<LParameters><ParameterRefRef RefId="{_FLAG}" /><ParameterRefRef RefId="{_FLAG_COPY}" /></LParameters>
<RParameters><ParameterRefRef RefId="{_RESULT}" /></RParameters>
</ParameterCalculation>
</ParameterCalculations>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_FLAG}" />
<ParameterRefRef RefId="{_FLAG_COPY}" />
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui(values: dict[str, str] | None = None) -> DynamicUI:
    program = parse_application_xml(_XML.encode(), "M-00FA")[0].program
    refs = [ParameterInstanceRef(ref_id=r, value=v) for r, v in (values or {}).items()]
    return DynamicUI(program, parameter_instance_refs=refs)


def test_a_calculation_reads_each_ref_under_its_own_name() -> None:
    ui = _ui()
    ui.edit_parameters([(_FLAG, "0")])
    assert ui.get_value(_RESULT) == "1"
    ui.edit_parameters([(_FLAG, "1")])
    assert ui.get_value(_RESULT) == "11"


def test_the_name_of_a_ref_is_its_own_before_its_parameters() -> None:
    indexer = _ui().indexer
    assert indexer.parameter_name(_FLAG_COPY) == "flag_ro"
    assert indexer.parameter_name(_FLAG) == "flag"
    assert indexer.parameter_name(f"{_APP}_P-9_R-9") is None


def test_a_script_finds_a_ref_by_its_own_name_and_by_its_parameters() -> None:
    ui = _ui()
    assert ui.find_parameter_ref("name", "flag_ro") == _FLAG_COPY
    assert ui.find_parameter_ref("name", "flag") == _FLAG


def test_under_its_parameters_name_a_ref_without_a_name_of_its_own_comes_first() -> (
    None
):
    # The copy named apart comes first in the application: a lookup by the parameter's name
    # still finds the ref that has no name of its own, by its own name the copy.
    copy = f'<ParameterRef Id="{_FLAG_COPY}" RefId="{_APP}_P-1" Name="flag_ro" Access="Read" Value="1" />'
    plain = f'<ParameterRef Id="{_FLAG}" RefId="{_APP}_P-1" />'
    xml = _XML.replace(plain + "\n" + copy, copy + "\n" + plain)
    assert xml != _XML
    program = parse_application_xml(xml.encode(), "M-00FA")[0].program
    ui = DynamicUI(program)
    assert ui.find_parameter_ref("name", "flag") == _FLAG
    assert ui.find_parameter_ref("name", "flag_ro") == _FLAG_COPY

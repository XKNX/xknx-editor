"""A value an Assign writes runs the calculations of its target, as an edit of the target does.

A hidden setting takes the value of an editable one through an Assign, and a calculation
computes from it a hidden switch that shows a further setting: an edit of the editable setting
changes the switch in the same change (an ABB actuator's "i-bus Tool access" copied into a
hidden setting the "changeable via i-bus Tool" options follow).
"""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.activation import shown_parameters
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-0D03-01-0000"
_ACCESS = f"{_APP}_P-1_R-1"
_COPY = f"{_APP}_P-2_R-2"
_SWITCH = f"{_APP}_P-3_R-3"
_OPTION = f"{_APP}_P-4_R-4"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="15" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assign calculations"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-Access" Name="Access"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Locked" Value="1" Id="{_APP}_PT-Access_EN-1" />
<Enumeration Text="Open" Value="2" Id="{_APP}_PT-Access_EN-2" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Access" ParameterType="{_APP}_PT-Access" Text="Tool access" Value="2" />
<Parameter Id="{_APP}_P-2" Name="AccessCopy" ParameterType="{_APP}_PT-Access" Text="" Access="None" Value="2">
<Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-3" Name="Shown" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0" />
<Parameter Id="{_APP}_P-4" Name="Option" ParameterType="{_APP}_PT-N" Text="Changeable via the tool" Value="0">
<Memory CodeSegment="{_APP}_AS-0000" Offset="1" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_ACCESS}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_COPY}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_SWITCH}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_OPTION}" RefId="{_APP}_P-4" />
</ParameterRefs>
<ParameterCalculations>
<ParameterCalculation Id="{_APP}_PC-1" Name="Show the option" Language="JavaScript">
<LRTransformation>shown = 0; if (access == 2) {{ shown = 1; }}</LRTransformation>
<RLTransformation>//Not implemented</RLTransformation>
<LParameters><ParameterRefRef RefId="{_COPY}" AliasName="access" /></LParameters>
<RParameters><ParameterRefRef RefId="{_SWITCH}" AliasName="shown" /></RParameters>
</ParameterCalculation>
</ParameterCalculations>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_ACCESS}" />
<ParameterRefRef RefId="{_COPY}" />
<ParameterRefRef RefId="{_SWITCH}" />
<Assign TargetParamRefRef="{_COPY}" SourceParamRefRef="{_ACCESS}" />
<choose ParamRefId="{_SWITCH}"><when test="1"><ParameterRefRef RefId="{_OPTION}" /></when></choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui(values: dict[str, str] | None = None) -> DynamicUI:
    program = parse_application_xml(_XML.encode(), "M-00FA")[0].program
    refs = [ParameterInstanceRef(ref_id=r, value=v) for r, v in (values or {}).items()]
    return DynamicUI(program, parameter_instance_refs=refs)


def _option_shown(ui: DynamicUI) -> bool:
    return _OPTION in shown_parameters(ui.ui())


def test_the_calculation_starts_with_the_default_of_the_copy() -> None:
    ui = _ui({_SWITCH: "1"})
    assert _option_shown(ui)


def test_an_assigned_value_runs_the_calculations_of_its_target() -> None:
    ui = _ui({_SWITCH: "1"})
    ui.ui()
    changes = ui.edit_parameter(_ACCESS, "1")
    assert changes[_COPY] == ("2", "1")
    assert changes[_SWITCH] == ("1", "0")
    assert not _option_shown(ui)
    changes = ui.edit_parameters([(_ACCESS, "2")])
    assert changes[_SWITCH] == ("0", "1")
    assert _option_shown(ui)


def test_an_edited_ui_agrees_with_one_seeded_from_its_changes() -> None:
    stored = {_SWITCH: "1"}
    edited = _ui(stored)
    edited.ui()
    for value in ("1", "2", "1"):
        changes = edited.edit_parameter(_ACCESS, value)
        stored.update(
            {ref: new for ref, (_, new) in changes.items() if new is not None}
        )
        seeded = _ui(stored)
        assert _option_shown(edited) == _option_shown(seeded)
        assert edited.encode_to_memory() == seeded.encode_to_memory()

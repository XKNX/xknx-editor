"""An Assign behind a Choose on a parameter that is not active does not apply.

A switch the device keeps no copy of shows a group object; Assigns set it, behind Chooses on
two settings that only a mode shows. With the mode hiding those settings, their Chooses select
no branch, and the switch keeps the value stored for it: opening the device keeps the object
shown. The first pass of an evaluation knows no parameter active yet and selects a branch of
every Choose: what its Assigns write is given back once a pass with the Chooses gated leaves
them out.
"""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-0D04-01-0000"
_MODE = f"{_APP}_P-1_R-1"
_IMPORT = f"{_APP}_P-2_R-2"
_EXPORT = f"{_APP}_P-3_R-3"
_TOTAL = f"{_APP}_P-4_R-4"
_OBJECT = f"{_APP}_O-1_R-1"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="16" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assign behind a choose"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
<ParameterType Id="{_APP}_PT-M" Name="M"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="2" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-M" Text="Mode" Value="0">
<Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-2" Name="Import" ParameterType="{_APP}_PT-B" Text="Import" Value="0">
<Memory CodeSegment="{_APP}_AS-0000" Offset="1" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-3" Name="Export" ParameterType="{_APP}_PT-B" Text="Export" Value="0">
<Memory CodeSegment="{_APP}_AS-0000" Offset="2" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-4" Name="Total" ParameterType="{_APP}_PT-B" Text="" Access="None" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_MODE}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_IMPORT}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_EXPORT}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_TOTAL}" RefId="{_APP}_P-4" />
</ParameterRefs>
<ComObjectTable>
<ComObject Id="{_APP}_O-1" Name="Total" Text="Total" Number="1" FunctionText="Total" ObjectSize="4 Bytes" ReadFlag="Enabled" WriteFlag="Disabled" CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" />
</ComObjectTable>
<ComObjectRefs><ComObjectRef Id="{_OBJECT}" RefId="{_APP}_O-1" /></ComObjectRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_MODE}" />
<choose ParamRefId="{_MODE}"><when test="0 1">
<ParameterRefRef RefId="{_IMPORT}" />
<ParameterRefRef RefId="{_EXPORT}" />
</when></choose>
<choose ParamRefId="{_IMPORT}"><when test="1"><Assign TargetParamRefRef="{_TOTAL}" Value="1" /></when></choose>
<choose ParamRefId="{_EXPORT}">
<when test="1"><Assign TargetParamRefRef="{_TOTAL}" Value="1" /></when>
<when test="0"><choose ParamRefId="{_IMPORT}"><when test="0"><Assign TargetParamRefRef="{_TOTAL}" Value="0" /></when></choose></when>
</choose>
<ParameterRefRef RefId="{_TOTAL}" />
<choose ParamRefId="{_TOTAL}"><when test="1"><ComObjectRefRef RefId="{_OBJECT}" /></when></choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui(values: dict[str, str]) -> DynamicUI:
    program = parse_application_xml(_XML.encode(), "M-00FA")[0].program
    refs = [ParameterInstanceRef(ref_id=r, value=v) for r, v in values.items()]
    ui = DynamicUI(program, parameter_instance_refs=refs)
    ui.ui()
    return ui


def _object_shown(ui: DynamicUI) -> bool:
    return _OBJECT in ui.active_parameter_driven_com_object_ref_ids()


def test_the_stored_switch_stays_where_its_assigns_do_not_apply() -> None:
    ui = _ui({_MODE: "2", _IMPORT: "0", _EXPORT: "0", _TOTAL: "1"})
    assert ui.get_value(_TOTAL) == "1"
    assert _object_shown(ui)
    assert _TOTAL not in ui.assign_targets()


def test_an_assign_that_applies_writes_the_switch() -> None:
    ui = _ui({_MODE: "0", _IMPORT: "0", _EXPORT: "0", _TOTAL: "1"})
    assert ui.get_value(_TOTAL) == "0"
    assert not _object_shown(ui)
    ui = _ui({_MODE: "1", _IMPORT: "1", _EXPORT: "0", _TOTAL: "0"})
    assert ui.get_value(_TOTAL) == "1"
    assert _object_shown(ui)

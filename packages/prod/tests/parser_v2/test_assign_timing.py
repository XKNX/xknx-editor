"""An Assign writes its target when it starts applying and when its source changes.

In between, the target keeps the value it has, also one set directly (an edit, a script, an
application update): the Assign writes it again only when it is switched on again, when the
module instance it is in appears, or when its source changes. A UI seeded from stored values
starts every Assign that applies, as a device calculated anew; one that continues an earlier
evaluation (``keep_assigned``, a device built again after an edit) takes them as what the Assigns
wrote: an Assign that applies then writes only a target without a stored value.
"""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-0011-01-0000"
_MD = f"{_APP}_MD-1"
_ENABLE = f"{_APP}_P-1_R-1"
_SOURCE = f"{_APP}_P-2_R-2"
_COPY = f"{_APP}_P-3_R-3"
_FIXED = f"{_APP}_P-4_R-4"
_OTHER = f"{_APP}_P-5_R-5"
_CHANNEL = f"{_APP}_P-6_R-6"
_LEVEL = f"{_MD}_M-1_MI-1_P-1_R-1"

_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="17" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assign timing"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-N" Name="N">
<TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="9" /></ParameterType>
<ParameterType Id="{_APP}_PT-B" Name="B">
<TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Enable" ParameterType="{_APP}_PT-B" Text="Enable" Value="0" />
<Parameter Id="{_APP}_P-2" Name="Source" ParameterType="{_APP}_PT-N" Text="Source" Value="3" />
<Parameter Id="{_APP}_P-3" Name="Copy" ParameterType="{_APP}_PT-N" Text="Copy" Value="0" />
<Parameter Id="{_APP}_P-4" Name="Fixed" ParameterType="{_APP}_PT-N" Text="Fixed" Value="0" />
<Parameter Id="{_APP}_P-5" Name="Other" ParameterType="{_APP}_PT-N" Text="Other" Value="0" />
<Parameter Id="{_APP}_P-6" Name="Channel" ParameterType="{_APP}_PT-B" Text="Channel" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_ENABLE}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_SOURCE}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_COPY}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_FIXED}" RefId="{_APP}_P-4" />
<ParameterRef Id="{_OTHER}" RefId="{_APP}_P-5" />
<ParameterRef Id="{_CHANNEL}" RefId="{_APP}_P-6" />
</ParameterRefs>
</Static>
<ModuleDefs>
<ModuleDef Id="{_MD}" Name="Channel">
<Static>
<Parameters>
<Parameter Id="{_MD}_P-1" Name="Level" ParameterType="{_APP}_PT-N" Text="Level" Value="0" />
</Parameters>
<ParameterRefs><ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" /></ParameterRefs>
</Static>
<Dynamic><ParameterBlock Id="{_MD}_PB-1" Text="Channel">
<ParameterRefRef RefId="{_MD}_P-1_R-1" />
<Assign TargetParamRefRef="{_MD}_P-1_R-1" Value="6" />
</ParameterBlock></Dynamic>
</ModuleDef>
</ModuleDefs>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_ENABLE}" />
<ParameterRefRef RefId="{_OTHER}" />
<ParameterRefRef RefId="{_CHANNEL}" />
<choose ParamRefId="{_ENABLE}"><when test="1">
<ParameterRefRef RefId="{_SOURCE}" />
<ParameterRefRef RefId="{_COPY}" />
<ParameterRefRef RefId="{_FIXED}" />
<Assign TargetParamRefRef="{_COPY}" SourceParamRefRef="{_SOURCE}" />
<Assign TargetParamRefRef="{_FIXED}" Value="5" />
</when></choose>
</ParameterBlock>
<choose ParamRefId="{_CHANNEL}"><when test="1">
<Module Id="{_MD}_M-1" RefId="{_MD}" Name="A" />
</when></choose>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui(values: dict[str, str] | None = None, *, keep: bool = False) -> DynamicUI:
    program = parse_application_xml(_XML.encode(), "M-00FA")[0].program
    refs = [ParameterInstanceRef(ref_id=r, value=v) for r, v in (values or {}).items()]
    ui = DynamicUI(program, parameter_instance_refs=refs or None, keep_assigned=keep)
    ui.ui()
    return ui


def _enabled() -> DynamicUI:
    ui = _ui()
    ui.edit_parameters([(_ENABLE, "1")])
    return ui


def _values(ui: DynamicUI) -> tuple[str | None, str | None]:
    ui.ui()
    return ui.get_value(_COPY), ui.get_value(_FIXED)


def test_switching_an_assign_on_writes_its_target() -> None:
    assert _values(_enabled()) == ("3", "5")


def test_a_target_set_directly_keeps_its_value() -> None:
    ui = _enabled()
    assert ui.edit_parameters([(_COPY, "7"), (_FIXED, "2")]) == {
        _COPY: ("3", "7"),
        _FIXED: ("5", "2"),
    }
    assert _values(ui) == ("7", "2")
    # Another edit leaves them as they are.
    ui.edit_parameters([(_OTHER, "4")])
    assert _values(ui) == ("7", "2")


def test_a_script_write_of_a_target_keeps_its_value() -> None:
    ui = _enabled()
    ui.write_parameter(_COPY, "8")
    assert _values(ui) == ("8", "5")


def test_a_change_of_the_source_writes_the_target() -> None:
    ui = _enabled()
    ui.edit_parameters([(_COPY, "7"), (_FIXED, "2")])
    assert ui.edit_parameters([(_SOURCE, "4")]) == {
        _SOURCE: ("3", "4"),
        _COPY: ("7", "4"),
    }
    # The Assign with a fixed value has no source: its target keeps its value.
    assert _values(ui) == ("4", "2")


def test_switching_on_again_writes_the_targets_again() -> None:
    ui = _enabled()
    ui.edit_parameters([(_COPY, "7"), (_FIXED, "2")])
    # Switched off, the targets get their defaults; switched on, the Assigns write them.
    ui.edit_parameters([(_ENABLE, "0")])
    assert _values(ui) == ("0", "0")
    ui.edit_parameters([(_ENABLE, "1")])
    assert _values(ui) == ("3", "5")


def test_a_module_instance_that_appears_writes_its_targets() -> None:
    ui = _ui()
    ui.edit_parameters([(_CHANNEL, "1")])
    assert ui.get_value(_LEVEL) == "6"
    ui.edit_parameters([(_LEVEL, "2")])
    ui.edit_parameters([(_OTHER, "1")])
    assert ui.get_value(_LEVEL) == "2"
    ui.edit_parameters([(_CHANNEL, "0")])
    ui.edit_parameters([(_CHANNEL, "1")])
    assert ui.get_value(_LEVEL) == "6"


def test_a_continued_ui_keeps_the_stored_targets() -> None:
    ui = _ui({_ENABLE: "1", _SOURCE: "4", _COPY: "7", _FIXED: "2"}, keep=True)
    assert _values(ui) == ("7", "2")
    # Built again from what the edits store, the UI is the one that was edited.
    edited = _enabled()
    edited.edit_parameters([(_COPY, "7")])
    stored = edited.explicit_values()
    assert _values(_ui(stored, keep=True)) == _values(edited) == ("7", "5")


def test_a_ui_calculated_anew_writes_the_targets() -> None:
    ui = _ui({_ENABLE: "1", _SOURCE: "4", _COPY: "7", _FIXED: "2"})
    assert _values(ui) == ("4", "5")


def test_a_continued_ui_writes_a_target_without_a_stored_value() -> None:
    ui = _ui({_ENABLE: "1", _SOURCE: "4"}, keep=True)
    assert _values(ui) == ("4", "5")
    assert ui.explicit_values()[_COPY] == "4"


def test_a_change_of_the_source_after_seeding_writes_the_target() -> None:
    ui = _ui({_ENABLE: "1", _SOURCE: "4", _COPY: "7"}, keep=True)
    ui.edit_parameters([(_OTHER, "1")])
    assert _values(ui) == ("7", "5")
    ui.edit_parameters([(_SOURCE, "1")])
    assert _values(ui) == ("1", "5")


def test_a_continued_module_instance_keeps_its_stored_target() -> None:
    ui = _ui({_CHANNEL: "1", _LEVEL: "2"}, keep=True)
    assert ui.get_value(_LEVEL) == "2"
    assert _ui({_CHANNEL: "1", _LEVEL: "2"}).get_value(_LEVEL) == "6"


def test_a_directly_set_target_stays_a_target() -> None:
    # The Assign still applies: its target stays assigned and is reset when it stops applying.
    ui = _enabled()
    ui.edit_parameters([(_COPY, "7")])
    assert _COPY in ui.assign_targets()
    assert ui.edit_parameters([(_ENABLE, "0")])[_COPY] == ("7", "0")

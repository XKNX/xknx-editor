"""Setting a value as an application update takes it over, and what the update reads of a device.

``carry_value`` checks a value as an edit does, changes nothing for the value the parameter has
(its own one, not that of the argument a module parameter takes its value from), and sets any
other one also on an inactive parameter. ``active_assigns`` tells the Assigns that apply,
``argument_value`` the value a module parameter takes from an argument.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.script.errors import ParameterValidationError

_APP = "M-00FA_A-0007-01-0000"
_MD = f"{_APP}_MD-1"
_TARGET = f"{_APP}_P-1_R-1"
_SWITCH = f"{_APP}_P-2_R-2"
_HIDDEN = f"{_APP}_P-3_R-3"
_KIND = f"{_MD}_M-1_MI-1_P-1_R-1"
_DELAY = f"{_MD}_M-1_MI-1_P-2_R-2"

_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Carry value"
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
<Parameter Id="{_APP}_P-1" Name="Target" ParameterType="{_APP}_PT-N" Text="Target" Value="0" />
<Parameter Id="{_APP}_P-2" Name="Switch" ParameterType="{_APP}_PT-B" Text="Switch" Value="0" />
<Parameter Id="{_APP}_P-3" Name="Hidden" ParameterType="{_APP}_PT-N" Text="Hidden" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_TARGET}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_SWITCH}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_HIDDEN}" RefId="{_APP}_P-3" />
</ParameterRefs>
</Static>
<ModuleDefs>
<ModuleDef Id="{_MD}" Name="Channel">
<Arguments><Argument Id="{_MD}_A-1" Name="Kind" /></Arguments>
<Static>
<Parameters>
<Parameter Id="{_MD}_P-1" Name="Kind" ParameterType="{_APP}_PT-B" Text="Kind" Value="1"
    BaseValue="{_MD}_A-1" />
<Parameter Id="{_MD}_P-2" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="3" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
<ParameterRef Id="{_MD}_P-2_R-2" RefId="{_MD}_P-2" />
</ParameterRefs>
</Static>
<Dynamic><ParameterBlock Id="{_MD}_PB-1" Text="Channel">
<ParameterRefRef RefId="{_MD}_P-1_R-1" /><ParameterRefRef RefId="{_MD}_P-2_R-2" />
</ParameterBlock></Dynamic>
</ModuleDef>
</ModuleDefs>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_TARGET}" />
<ParameterRefRef RefId="{_SWITCH}" />
<choose ParamRefId="{_SWITCH}"><when test="1">
<ParameterRefRef RefId="{_HIDDEN}" />
<Assign TargetParamRefRef="{_TARGET}" Value="7" />
</when></choose>
</ParameterBlock>
<Module Id="{_MD}_M-1" RefId="{_MD}" Name="A"><NumericArg RefId="{_MD}_A-1" Value="0" /></Module>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui() -> DynamicUI:
    return DynamicUI(parse_application_xml(_XML.encode(), "M-00FA")[0].program)


def test_a_value_the_type_refuses_raises() -> None:
    ui = _ui()
    with pytest.raises(ParameterValidationError):
        ui.carry_value(_TARGET, "12")
    assert ui.get_value(_TARGET) == "0"


def test_the_value_the_parameter_has_changes_nothing() -> None:
    assert _ui().carry_value(_TARGET, "0") is None


def test_an_inactive_parameter_takes_a_value() -> None:
    ui = _ui()
    assert _HIDDEN not in ui.active_parameter_ref_ids()
    assert ui.carry_value(_HIDDEN, "4") == {_HIDDEN: ("0", "4")}
    assert ui.get_value(_HIDDEN) == "4"


def test_the_assigns_that_apply() -> None:
    ui = _ui()
    assert ui.has_assigns
    assert ui.active_assigns() == frozenset()
    ui.carry_value(_SWITCH, "1")
    assert len(ui.active_assigns()) == 1
    assert ui.get_value(_TARGET) == "7"
    assert ui.explicit_values() == {_SWITCH: "1", _TARGET: "7"}
    # Switched off again, the Assign no longer applies and its target keeps the value.
    ui.carry_value(_SWITCH, "0")
    assert ui.active_assigns() == frozenset()
    assert ui.get_value(_TARGET) == "7"


def test_a_module_parameter_takes_the_value_of_its_argument() -> None:
    ui = _ui()
    assert ui.has_parameter(_KIND)
    assert not ui.has_parameter(f"{_MD}_M-1_MI-2_P-1_R-1")
    assert not ui.has_parameter(f"{_MD}_P-1_R-1")
    assert ui.argument_value(_KIND) == "0"
    assert ui.argument_value(_DELAY) is None
    assert ui.argument_value(_TARGET) is None
    # Its own value is the default 1, though it has the argument's 0.
    assert ui.get_value(_KIND) == "0"
    assert ui.carry_value(_KIND, "1") is None
    # Set as its own value; what it has stays the argument's.
    assert ui.carry_value(_KIND, "0") == {}
    assert ui.explicit_values()[_KIND] == "0"
    assert ui.get_value(_KIND) == "0"

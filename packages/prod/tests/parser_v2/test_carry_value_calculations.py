"""The calculations of a value an application update takes over write their outputs one by one.

The calculation of the setting computes two outputs from it, the second one with a smaller
range. ``carry_value`` tells the value and each output it changes, in that order, once each is
written. An output its parameter does not take ends the calculation: the value and the outputs
before it stay, and the error is raised. An edit with the same value is undone as a whole.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.script.errors import CalculationError

_APP = "M-00FA_A-0008-01-0000"
_SETTING = f"{_APP}_P-1_R-1"
_FIRST = f"{_APP}_P-2_R-2"
_SECOND = f"{_APP}_P-3_R-3"

_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="8" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Carry calculations"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-N" Name="N">
<TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="9" /></ParameterType>
<ParameterType Id="{_APP}_PT-S" Name="S">
<TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="3" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Setting" ParameterType="{_APP}_PT-N" Text="Setting" Value="0" />
<Parameter Id="{_APP}_P-2" Name="First" ParameterType="{_APP}_PT-N" Text="First" Value="0" />
<Parameter Id="{_APP}_P-3" Name="Second" ParameterType="{_APP}_PT-S" Text="Second" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_SETTING}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_FIRST}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_SECOND}" RefId="{_APP}_P-3" />
</ParameterRefs>
<ParameterCalculations>
<ParameterCalculation Id="{_APP}_PC-1" Name="Outputs" Language="JavaScript">
<LRTransformation>first = setting + 1; second = setting * 2;</LRTransformation>
<LParameters><ParameterRefRef RefId="{_SETTING}" AliasName="setting" /></LParameters>
<RParameters>
<ParameterRefRef RefId="{_FIRST}" AliasName="first" />
<ParameterRefRef RefId="{_SECOND}" AliasName="second" />
</RParameters>
</ParameterCalculation>
</ParameterCalculations>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_SETTING}" />
<ParameterRefRef RefId="{_FIRST}" />
<ParameterRefRef RefId="{_SECOND}" />
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui() -> DynamicUI:
    return DynamicUI(parse_application_xml(_XML.encode(), "M-00FA")[0].program)


def test_the_value_and_each_output_are_told_once_written() -> None:
    ui = _ui()
    told: list[tuple[str, str | None]] = []
    changes = ui.carry_value(
        _SETTING, "1", lambda ref: told.append((ref, ui.get_value(ref)))
    )
    assert told == [(_SETTING, "1"), (_FIRST, "2"), (_SECOND, "2")]
    assert changes == {
        _SETTING: ("0", "1"),
        _FIRST: ("0", "2"),
        _SECOND: ("0", "2"),
    }


def test_an_output_left_unchanged_is_not_told() -> None:
    ui = _ui()
    told: list[str] = []
    # The first output has the 2 the calculation computes already.
    ui.carry_value(_FIRST, "2")
    ui.carry_value(_SETTING, "1", told.append)
    assert told == [_SETTING, _SECOND]


def test_an_output_the_parameter_refuses_ends_the_calculation() -> None:
    ui = _ui()
    told: list[str] = []
    with pytest.raises(CalculationError):
        ui.carry_value(_SETTING, "3", told.append)
    # 6 is out of the second output's range: the value and the first output stay.
    assert told == [_SETTING, _FIRST]
    assert ui.get_value(_SETTING) == "3"
    assert ui.get_value(_FIRST) == "4"
    assert ui.get_value(_SECOND) == "0"


def test_an_edit_whose_output_is_refused_is_undone() -> None:
    ui = _ui()
    with pytest.raises(CalculationError):
        ui.edit_parameter(_SETTING, "3")
    assert ui.get_value(_SETTING) == "0"
    assert ui.get_value(_FIRST) == "0"

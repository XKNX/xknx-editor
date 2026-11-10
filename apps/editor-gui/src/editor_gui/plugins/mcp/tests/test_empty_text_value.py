"""A cleared text reads as empty through MCP, also while the configuration hides it: only a
parameter without a value reads as its default."""

from __future__ import annotations

from typing import Any

from editor_gui.plugins.mcp.tools.project import hidden_parameter_rows
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-0007-01-0000"
_SWITCH = f"{_APP}_P-1_R-1"
_NAME = f"{_APP}_P-2_R-2"

_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Empty text"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-B" Name="B">
<TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
<ParameterType Id="{_APP}_PT-T" Name="T"><TypeText SizeInBit="80" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Switch" ParameterType="{_APP}_PT-B" Text="Switch" Value="0" />
<Parameter Id="{_APP}_P-2" Name="Name" ParameterType="{_APP}_PT-T" Text="Name" Value="Device 1" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_SWITCH}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_NAME}" RefId="{_APP}_P-2" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_SWITCH}" />
<choose ParamRefId="{_SWITCH}"><when test="1"><ParameterRefRef RefId="{_NAME}" /></when></choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


class _Device:
    def __init__(self) -> None:
        self.dynamic_ui = DynamicUI(
            parse_application_xml(_XML.encode(), "M-00FA")[0].program
        )

    def get_ui(self) -> list[Any]:
        return self.dynamic_ui.ui()


def _hidden_name(device: _Device, **kwargs: Any) -> list[dict[str, Any]]:
    return [
        row
        for row in hidden_parameter_rows(device, False, **kwargs)
        if row["ref_id"] == _NAME
    ]


def test_a_hidden_text_without_a_value_reads_as_its_default() -> None:
    [row] = _hidden_name(_Device())
    assert (row["value"], row["default_value"]) == ("Device 1", "Device 1")
    assert _hidden_name(_Device(), modified_only=True) == []


def test_a_hidden_cleared_text_reads_as_empty() -> None:
    device = _Device()
    device.dynamic_ui.carry_value(_NAME, "")
    assert _NAME not in device.dynamic_ui.active_parameter_ref_ids()
    [row] = _hidden_name(device)
    assert (row["value"], row["default_value"]) == ("", "Device 1")
    assert row["visible"] is False
    assert _hidden_name(device, modified_only=True) == [row]

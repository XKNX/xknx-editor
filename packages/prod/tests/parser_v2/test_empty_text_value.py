"""An empty value is a value: a cleared text shows empty, not its default.

Only a parameter without a value shows its default, the ParameterRef's when it has one (also an
empty one), as the value the device has is that default.
"""

from __future__ import annotations

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import UiNode, UiParameter

_APP = "M-00FA_A-0007-01-0000"
_NAME = f"{_APP}_P-1_R-1"
_EMPTY_REF = f"{_APP}_P-2_R-2"

_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Empty text"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-T" Name="T"><TypeText SizeInBit="80" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Name" ParameterType="{_APP}_PT-T" Text="Name" Value="Device 1" />
<Parameter Id="{_APP}_P-2" Name="Other" ParameterType="{_APP}_PT-T" Text="Other" Value="Other" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_NAME}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_EMPTY_REF}" RefId="{_APP}_P-2" Value="" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_NAME}" />
<ParameterRefRef RefId="{_EMPTY_REF}" />
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _ui() -> DynamicUI:
    return DynamicUI(parse_application_xml(_XML.encode(), "M-00FA")[0].program)


def _shown(ui: DynamicUI) -> dict[str, UiParameter]:
    found: dict[str, UiParameter] = {}

    def walk(nodes: list[UiNode]) -> None:
        for node in nodes:
            if isinstance(node, UiParameter):
                found[node.ref_id] = node
            walk(list(getattr(node, "children", ()) or ()))

    walk(ui.ui())
    return found


def test_a_text_without_a_value_shows_its_default() -> None:
    shown = _shown(_ui())[_NAME]
    assert (shown.value, shown.default_value) == ("Device 1", "Device 1")


def test_a_cleared_text_shows_empty() -> None:
    ui = _ui()
    ui.set_parameter_ref(_NAME, "")
    assert ui.get_value(_NAME) == ""
    shown = _shown(ui)[_NAME]
    assert (shown.value, shown.default_value) == ("", "Device 1")


def test_an_empty_parameter_ref_value_is_the_default() -> None:
    ui = _ui()
    assert ui.get_value(_EMPTY_REF) == ""
    shown = _shown(ui)[_EMPTY_REF]
    assert (shown.value, shown.default_value) == ("", "")

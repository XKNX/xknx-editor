"""The search of pairs: a switch the device keeps no copy of shows a setting the device keeps
no copy of either through a hidden selector a calculation computes from the switch. Which of
three hidden values with a fixed value each is stored depends on both: the switch alone shows
the value of the setting's default and fits as badly as before; only with the setting it fits.
"""

from __future__ import annotations

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters

_APP = "M-00FA_A-0C02-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_LOCK = f"{_APP}_P-1_R-1"
_POLARITY = f"{_APP}_P-2_R-2"
_SHOWN = f"{_APP}_P-3_R-1"
_INVERTED = f"{_APP}_P-3_R-2"
_NORMAL = f"{_APP}_P-3_R-3"
_OFF = f"{_APP}_P-3_R-4"
# What the device holds with the switch on and the setting at 0 (the product's default is 1).
_DEVICE = bytes([1])
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="194" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Calculated selector pair"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-OnOff" Name="OnOff"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
<Enumeration Text="On" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-Byte" Name="Byte"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Lock" ParameterType="{_APP}_PT-OnOff" Text="Lock" Value="0" />
<Parameter Id="{_APP}_P-2" Name="Polarity" ParameterType="{_APP}_PT-OnOff" Text="Polarity" Value="1" />
<Parameter Id="{_APP}_P-3" Name="Condition" ParameterType="{_APP}_PT-OnOff" Text="" Access="None" Value="0" />
<Parameter Id="{_APP}_P-4" Name="Stored" ParameterType="{_APP}_PT-Byte" Text="Stored" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_LOCK}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_POLARITY}" RefId="{_APP}_P-2" />
<ParameterRef Id="{_SHOWN}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_INVERTED}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_NORMAL}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_OFF}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_APP}_P-4_R-1" RefId="{_APP}_P-4" Access="None" Value="1" />
<ParameterRef Id="{_APP}_P-4_R-2" RefId="{_APP}_P-4" Access="None" Value="2" />
<ParameterRef Id="{_APP}_P-4_R-3" RefId="{_APP}_P-4" Access="None" Value="0" />
</ParameterRefs>
<ParameterCalculations>
<ParameterCalculation Id="{_APP}_PC-1" Name="Shown" Language="JavaScript">
<LRTransformation>r = l == 1 ? 1 : 0;</LRTransformation>
<LParameters><ParameterRefRef RefId="{_LOCK}" AliasName="l" /></LParameters>
<RParameters><ParameterRefRef RefId="{_SHOWN}" AliasName="r" /></RParameters>
</ParameterCalculation>
<ParameterCalculation Id="{_APP}_PC-2" Name="Inverted" Language="JavaScript">
<LRTransformation>r = (l == 1 &amp;&amp; s == 1 &amp;&amp; p == 0) ? 1 : 0;</LRTransformation>
<LParameters>
<ParameterRefRef RefId="{_LOCK}" AliasName="l" />
<ParameterRefRef RefId="{_POLARITY}" AliasName="p" />
<ParameterRefRef RefId="{_SHOWN}" AliasName="s" />
</LParameters>
<RParameters><ParameterRefRef RefId="{_INVERTED}" AliasName="r" /></RParameters>
</ParameterCalculation>
<ParameterCalculation Id="{_APP}_PC-3" Name="Normal" Language="JavaScript">
<LRTransformation>r = (l == 1 &amp;&amp; s == 1 &amp;&amp; p == 1) ? 1 : 0;</LRTransformation>
<LParameters>
<ParameterRefRef RefId="{_LOCK}" AliasName="l" />
<ParameterRefRef RefId="{_POLARITY}" AliasName="p" />
<ParameterRefRef RefId="{_SHOWN}" AliasName="s" />
</LParameters>
<RParameters><ParameterRefRef RefId="{_NORMAL}" AliasName="r" /></RParameters>
</ParameterCalculation>
<ParameterCalculation Id="{_APP}_PC-4" Name="Off" Language="JavaScript">
<LRTransformation>r = l == 0 ? 1 : 0;</LRTransformation>
<LParameters><ParameterRefRef RefId="{_LOCK}" AliasName="l" /></LParameters>
<RParameters><ParameterRefRef RefId="{_OFF}" AliasName="r" /></RParameters>
</ParameterCalculation>
</ParameterCalculations>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Lock">
<ParameterRefRef RefId="{_LOCK}" />
<ParameterRefRef RefId="{_SHOWN}" />
<choose ParamRefId="{_SHOWN}"><when test="1"><ParameterRefRef RefId="{_POLARITY}" /></when></choose>
<ParameterRefRef RefId="{_INVERTED}" />
<choose ParamRefId="{_INVERTED}"><when test="1"><ParameterRefRef RefId="{_APP}_P-4_R-1" /></when></choose>
<ParameterRefRef RefId="{_NORMAL}" />
<choose ParamRefId="{_NORMAL}"><when test="1"><ParameterRefRef RefId="{_APP}_P-4_R-2" /></when></choose>
<ParameterRefRef RefId="{_OFF}" />
<choose ParamRefId="{_OFF}"><when test="1"><ParameterRefRef RefId="{_APP}_P-4_R-3" /></when></choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _memory(application: Application, *values: tuple[str, str]) -> bytes:
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values:
        ui.edit_parameter(ref_id, value)
    return ui.encode_to_memory()[_SEGMENT]


def test_the_device_is_the_switch_with_the_setting(application: Application) -> None:
    assert _memory(application, (_LOCK, "1"), (_POLARITY, "0")) == _DEVICE
    # The switch alone stores another fixed value, as far from the device as off.
    assert _memory(application, (_LOCK, "1")) == bytes([2])
    assert _memory(application) == bytes([0])


def test_the_setting_behind_the_calculated_selector_is_tried_with_the_switch(
    application: Application,
) -> None:
    recovered = parameters._refine_pairs(  # pyright: ignore[reportPrivateUsage]
        application,
        {_SEGMENT: _DEVICE},
        {},
        None,
        set(),
        {},
        parameters.RecoveredParameters({}, [], differing_bytes=1),
        1,
    )
    assert recovered.complete
    assert recovered.values[_LOCK] == "1"
    assert recovered.values[_POLARITY] == "0"


def test_the_search_finds_the_switch_and_the_setting(application: Application) -> None:
    recovered = recover_parameters(
        application, {_SEGMENT: _DEVICE}, effort=RecoveryEffort.MEDIUM
    )
    assert recovered.complete
    assert recovered.values[_LOCK] == "1"
    assert recovered.values[_POLARITY] == "0"

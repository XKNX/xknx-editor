"""A hidden switch only Assigns of constants set, none of which applies now: it keeps the
value the last one gave, which only the group object it shows tells. The device keeps no copy
of it; the search tries the values the Assigns give it.

The switch shows a total object; Assigns set it behind Chooses on two settings that only a
mode shows. With the mode hiding those settings, the device links the object all the same.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0D05-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_MODE = f"{_APP}_P-1_R-1"
_IMPORT = f"{_APP}_P-2_R-2"
_EXPORT = f"{_APP}_P-3_R-3"
_TOTAL = f"{_APP}_P-4_R-4"
_OBJECT = f"{_APP}_O-1_R-1"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="17" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Kept assigned switch"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
<ParameterType Id="{_APP}_PT-M" Name="M"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="2" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-M" Text="Mode" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-2" Name="Import" ParameterType="{_APP}_PT-B" Text="Import" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-3" Name="Export" ParameterType="{_APP}_PT-B" Text="Export" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" /></Parameter>
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


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_the_device_holds_the_mode_alone(application: Application) -> None:
    ui = seed_dynamic_ui(
        application, {_MODE: "2", _IMPORT: "0", _EXPORT: "0", _TOTAL: "1"}
    )
    assert ui is not None
    assert ui.encode_to_memory()[_SEGMENT] == bytes([2, 0, 0])


@pytest.mark.parametrize("effort", list(RecoveryEffort))
def test_the_kept_switch_shows_the_linked_object(
    application: Application, effort: RecoveryEffort
) -> None:
    recovered = recover_parameters(
        application, {_SEGMENT: bytes([2, 0, 0])}, linked_objects={1}, effort=effort
    )
    assert recovered.complete
    assert recovered.values[_MODE] == "2"
    assert recovered.values[_TOTAL] == "1"


def test_without_the_object_the_switch_stays_off(application: Application) -> None:
    recovered = recover_parameters(application, {_SEGMENT: bytes([2, 0, 0])})
    assert recovered.complete
    assert recovered.values.get(_TOTAL, "0") == "0"


def test_an_assign_that_applies_decides_the_switch(application: Application) -> None:
    # The mode shows the settings: their Assigns write the switch, nothing to try.
    recovered = recover_parameters(
        application, {_SEGMENT: bytes([0, 1, 0])}, linked_objects={1}
    )
    assert recovered.complete
    assert recovered.values[_IMPORT] == "1"

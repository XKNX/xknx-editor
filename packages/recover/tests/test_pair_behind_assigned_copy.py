"""The search of pairs: a shown setting the device keeps no copy of whose hidden copy (an Assign
writes it) places a module instance. That setting alone shows the instance's group object with
another size than the device's table; with the function of the instance, which the hidden copy
places, it fits."""

from __future__ import annotations

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0009-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_COPY = f"{_APP}_P-1_R-1"
_MODE = f"{_APP}_P-1_R-2"
_VALUE = f"{_APP}_P-3_R-3"
_FUNCTION = f"{_MD}_M-1_MI-1_P-1_R-1"
# The value 7, read with the mode on; the function's object (number 10 + 1) a byte.
_DEVICE = bytes([7])
_SIZES = {11: "1 Byte"}
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="9" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Assigned copy"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-Selector" Name="Selector"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_APP}_PT-Selector_EN-0" />
<Enumeration Text="On" Value="1" Id="{_APP}_PT-Selector_EN-1" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-N" Name="Value"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-Selector" Text="Mode" Value="0" />
<Parameter Id="{_APP}_P-3" Name="Value" ParameterType="{_APP}_PT-N" Text="Value" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_COPY}" RefId="{_APP}_P-1" Access="None" />
<ParameterRef Id="{_MODE}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_VALUE}" RefId="{_APP}_P-3" />
</ParameterRefs>
</Static>
<ModuleDefs>
<ModuleDef Id="{_MD}" Name="Function">
<Arguments><Argument Id="{_MD}_A-1" Name="ObjectBase" /></Arguments>
<Static>
<Parameters>
<Parameter Id="{_MD}_P-1" Name="Function" ParameterType="{_APP}_PT-Selector" Text="Function" Value="0" />
</Parameters>
<ParameterRefs><ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" /></ParameterRefs>
<ComObjects>
<ComObject Id="{_MD}_O-1" Name="Output" Text="Output" Number="1" ObjectSize="1 Bit"
 FunctionText="" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
 TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" BaseNumber="{_MD}_A-1" />
</ComObjects>
<ComObjectRefs>
<ComObjectRef Id="{_MD}_O-1_R-1" RefId="{_MD}_O-1" />
<ComObjectRef Id="{_MD}_O-1_R-2" RefId="{_MD}_O-1" ObjectSize="1 Byte" />
</ComObjectRefs>
</Static>
<Dynamic><ParameterBlock Id="{_MD}_PB-1" Text="Function">
<ParameterRefRef RefId="{_MD}_P-1_R-1" />
<choose ParamRefId="{_MD}_P-1_R-1">
<when test="0"><ComObjectRefRef RefId="{_MD}_O-1_R-1" /></when>
<when test="1"><ComObjectRefRef RefId="{_MD}_O-1_R-2" /></when>
</choose>
</ParameterBlock></Dynamic>
</ModuleDef>
</ModuleDefs>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Settings">
<ParameterRefRef RefId="{_MODE}" />
<Assign TargetParamRefRef="{_COPY}" SourceParamRefRef="{_MODE}" />
<choose ParamRefId="{_COPY}"><when test="1"><ParameterRefRef RefId="{_VALUE}" /></when></choose>
</ParameterBlock>
<choose ParamRefId="{_COPY}"><when test="1">
<Module Id="{_MD}_M-1" RefId="{_MD}"><NumericArg RefId="{_MD}_A-1" Value="10" /></Module>
</when></choose>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_the_device_is_the_mode_with_the_function(application: Application) -> None:
    ui = seed_dynamic_ui(application, {_MODE: "1", _VALUE: "7", _FUNCTION: "1"})
    assert ui is not None
    # The copy places the value and the module instance.
    assert ui.encode_to_memory()[_SEGMENT] == _DEVICE
    assert parameters._shown_objects(ui, _SIZES) == {11}  # pyright: ignore[reportPrivateUsage]


def test_the_function_behind_the_copy_is_tried_with_the_mode(
    application: Application,
) -> None:
    recovered = parameters._refine_pairs(  # pyright: ignore[reportPrivateUsage]
        application,
        {_SEGMENT: _DEVICE},
        {},
        None,
        set(),
        _SIZES,
        parameters.RecoveredParameters({}, [], differing_bytes=1),
        1,
    )
    assert recovered.complete
    assert recovered.values[_MODE] == "1"
    assert recovered.values[_FUNCTION] == "1"
    assert recovered.values[_VALUE] == "7"


def test_the_search_finds_the_mode_and_the_function(application: Application) -> None:
    recovered = recover_parameters(
        application,
        {_SEGMENT: _DEVICE},
        object_sizes=_SIZES,
        effort=RecoveryEffort.MEDIUM,
    )
    assert recovered.complete
    assert recovered.values[_MODE] == "1"
    assert recovered.values[_FUNCTION] == "1"

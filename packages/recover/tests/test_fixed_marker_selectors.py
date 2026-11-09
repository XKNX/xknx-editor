"""A selector the device keeps no copy of, told by a hidden marker the product fixes per choice.

A channel's function picks one of two references of a hidden setting the device stores, each
with its own fixed value. Decoding the memory into the marker makes either choice fit, so the
search that keeps the fixed values corrects the function: also when the right choice shows a
linked group object only through a setting of its own the device keeps no copy of either, and
when a calculation computes a hidden switch from the function (its value is no value read).
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters

_APP = "M-00FA_A-0D01-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_FUNCTION = f"{_APP}_P-1_R-1"
_DETAIL = f"{_APP}_P-3_R-3"
_SWITCH = f"{_APP}_P-4_R-4"

_CALCULATION = f"""<ParameterCalculations>
<ParameterCalculation Id="{_APP}_PC-1" Name="Show" Language="JavaScript">
<LRTransformation>shown = kind == 3 ? 1 : 0;</LRTransformation>
<RLTransformation>/* */</RLTransformation>
<LParameters><ParameterRefRef RefId="{_FUNCTION}" AliasName="kind" /></LParameters>
<RParameters><ParameterRefRef RefId="{_SWITCH}" AliasName="shown" /></RParameters>
</ParameterCalculation></ParameterCalculations>"""


def _xml(*, detail: bool, calculation: bool) -> str:
    """The application: the function (3 by default) shows object 1 directly with 3; with 0 it
    shows it directly, or (``detail``) through a setting of its own set to 1. ``calculation``
    computes a hidden switch from the function."""
    zero = (
        f'<ParameterRefRef RefId="{_DETAIL}" />'
        f'<choose ParamRefId="{_DETAIL}"><when test="1">'
        f'<ComObjectRefRef RefId="{_APP}_O-1_R-1" /></when></choose>'
        if detail
        else f'<ComObjectRefRef RefId="{_APP}_O-1_R-1" />'
    )
    return f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="13" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Fixed markers"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-Function" Name="Function"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Switch" Value="0" Id="{_APP}_PT-Function_EN-0" />
<Enumeration Text="Dim" Value="3" Id="{_APP}_PT-Function_EN-3" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-OnOff" Name="OnOff"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
<Enumeration Text="On" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Function" ParameterType="{_APP}_PT-Function" Text="Function" Value="3" />
<Parameter Id="{_APP}_P-2" Name="Marker" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-3" Name="Detail" ParameterType="{_APP}_PT-OnOff" Text="Detail" Value="0" />
<Parameter Id="{_APP}_P-4" Name="Shown" ParameterType="{_APP}_PT-OnOff" Text="" Access="None" Value="0" />
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_FUNCTION}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_APP}_P-2_R-20" RefId="{_APP}_P-2" Value="0" />
<ParameterRef Id="{_APP}_P-2_R-23" RefId="{_APP}_P-2" Value="5" />
<ParameterRef Id="{_DETAIL}" RefId="{_APP}_P-3" />
<ParameterRef Id="{_SWITCH}" RefId="{_APP}_P-4" />
</ParameterRefs>
<ComObjectTable>
<ComObject Id="{_APP}_O-1" Name="O1" Text="Object 1" Number="1" FunctionText="" ObjectSize="1 Bit"
    ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled"
    UpdateFlag="Disabled" ReadOnInitFlag="Disabled" />
</ComObjectTable>
<ComObjectRefs><ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" /></ComObjectRefs>
{_CALCULATION if calculation else ""}
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Channel">
<ParameterRefRef RefId="{_FUNCTION}" />
<ParameterRefRef RefId="{_SWITCH}" />
<choose ParamRefId="{_FUNCTION}">
<when test="0"><ParameterRefRef RefId="{_APP}_P-2_R-20" />{zero}</when>
<when test="3"><ParameterRefRef RefId="{_APP}_P-2_R-23" /><ComObjectRefRef RefId="{_APP}_O-1_R-1" /></when>
</choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _application(*, detail: bool, calculation: bool) -> Application:
    return parse_application_xml(
        _xml(detail=detail, calculation=calculation).encode(), "M-00FA"
    )[0]


def _device(application: Application, *values: tuple[str, str]) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id, value), set in order."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values:
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


@pytest.mark.parametrize("effort", [RecoveryEffort.FAST, RecoveryEffort.MEDIUM])
def test_a_marker_corrects_a_function_that_shows_its_object_through_a_setting(
    effort: RecoveryEffort,
) -> None:
    # Function 0 shows object 1 only once its detail is on: the function alone fits worse
    # (the object goes), with the detail inferred behind it, it fits.
    application = _application(detail=True, calculation=False)
    segments = _device(application, (_FUNCTION, "0"), (_DETAIL, "1"))
    assert segments[_SEGMENT][0] == 0
    recovered = recover_parameters(
        application,
        segments,
        linked_objects={1},
        object_sizes={1: "1 Bit"},
        effort=effort,
    )
    assert recovered.complete
    assert recovered.values[_FUNCTION] == "0"
    assert recovered.values[_DETAIL] == "1"


@pytest.mark.parametrize("effort", [RecoveryEffort.FAST, RecoveryEffort.MEDIUM])
def test_a_function_a_calculation_computes_a_hidden_switch_from_is_corrected(
    effort: RecoveryEffort,
) -> None:
    # The switch the calculation computes is no value read from the device: the function it
    # comes from is a setting to try like any other.
    application = _application(detail=False, calculation=True)
    segments = _device(application, (_FUNCTION, "0"))
    recovered = recover_parameters(
        application,
        segments,
        linked_objects={1},
        object_sizes={1: "1 Bit"},
        effort=effort,
    )
    assert recovered.complete
    assert recovered.values[_FUNCTION] == "0"


def test_the_default_function_is_kept_when_its_marker_fits() -> None:
    application = _application(detail=True, calculation=True)
    segments = _device(application)
    assert segments[_SEGMENT][0] == 5
    recovered = recover_parameters(
        application,
        segments,
        linked_objects={1},
        object_sizes={1: "1 Bit"},
        effort=RecoveryEffort.FAST,
    )
    assert recovered.complete
    assert recovered.values.get(_FUNCTION, "3") == "3"

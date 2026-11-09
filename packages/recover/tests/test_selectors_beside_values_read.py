"""Shown settings the device keeps no copy of on the left side of a calculation that also has a
value read from the device on its right: the values read do not decide them.

In the first application the calculation copies a shown "Sensitivity" to its hidden copy, which
places one of two hidden members of a union with the values the product fixes, beside the copy
of a stored "Mode". In the second one a shown "Function" gives a stored function code, the same
code for two functions: only the member of a union the second function places tells it.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _alike_for_reads,  # pyright: ignore[reportPrivateUsage]
    _calculated_from_reads,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0C03-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_SENSITIVITY = f"{_APP}_P-1_R-1"
_SENSITIVITY_COPY = f"{_APP}_P-1_R-2"
_MODE_STORED = f"{_APP}_P-2_R-3"
_MODE = f"{_APP}_P-3_R-4"


def _program(app: str, body: str, dynamic: str, size: int = 2) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{app}" ApplicationNumber="195" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Selectors beside values"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{app}_AS-0000" Address="0" Size="{size}"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{app}_PT-Choice" Name="Choice"><TypeRestriction Base="Value" SizeInBit="8">
            <Enumeration Text="Zero" Value="0" Id="{app}_PT-Choice_EN-0" />
            <Enumeration Text="One" Value="1" Id="{app}_PT-Choice_EN-1" />
            <Enumeration Text="Two" Value="2" Id="{app}_PT-Choice_EN-2" />
          </TypeRestriction></ParameterType>
          <ParameterType Id="{app}_PT-Byte" Name="Byte">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
          </ParameterType>
        </ParameterTypes>
        {body}
      </Static>
      <Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app}_PB-1" Text="General">
        {dynamic}
      </ParameterBlock></ChannelIndependentBlock></Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


_SENSITIVITY_XML = _program(
    _APP,
    f"""<Parameters>
      <Parameter Id="{_APP}_P-1" Name="Sensitivity" ParameterType="{_APP}_PT-Choice" Text="Sensitivity" Value="0" />
      <Parameter Id="{_APP}_P-2" Name="ModeStored" ParameterType="{_APP}_PT-Byte" Text="" Access="None" Value="0">
        <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
      </Parameter>
      <Parameter Id="{_APP}_P-3" Name="Mode" ParameterType="{_APP}_PT-Byte" Text="Mode" Value="0" />
      <Union SizeInBit="8">
        <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
        <Parameter Id="{_APP}_UP-4" Name="Low" ParameterType="{_APP}_PT-Byte" Offset="0" BitOffset="0" Text="Low" Access="None" Value="10" />
        <Parameter Id="{_APP}_UP-5" Name="High" ParameterType="{_APP}_PT-Byte" Offset="0" BitOffset="0" Text="High" Access="None" Value="200" />
      </Union>
    </Parameters>
    <ParameterRefs>
      <ParameterRef Id="{_SENSITIVITY}" RefId="{_APP}_P-1" Access="ReadWrite" />
      <ParameterRef Id="{_SENSITIVITY_COPY}" RefId="{_APP}_P-1" Access="None" />
      <ParameterRef Id="{_MODE_STORED}" RefId="{_APP}_P-2" />
      <ParameterRef Id="{_MODE}" RefId="{_APP}_P-3" />
      <ParameterRef Id="{_APP}_UP-4_R-5" RefId="{_APP}_UP-4" />
      <ParameterRef Id="{_APP}_UP-5_R-6" RefId="{_APP}_UP-5" />
    </ParameterRefs>
    <ParameterCalculations>
      <ParameterCalculation Id="{_APP}_PC-1" Name="Copies" Language="JavaScript">
        <RLTransformation>s = h; u = m;</RLTransformation>
        <LRTransformation>h = s; m = u;</LRTransformation>
        <LParameters>
          <ParameterRefRef RefId="{_SENSITIVITY}" AliasName="s" />
          <ParameterRefRef RefId="{_MODE}" AliasName="u" />
        </LParameters>
        <RParameters>
          <ParameterRefRef RefId="{_SENSITIVITY_COPY}" AliasName="h" />
          <ParameterRefRef RefId="{_MODE_STORED}" AliasName="m" />
        </RParameters>
      </ParameterCalculation>
    </ParameterCalculations>""",
    f"""<ParameterRefRef RefId="{_SENSITIVITY}" />
    <ParameterRefRef RefId="{_MODE}" />
    <ParameterRefRef RefId="{_MODE_STORED}" />
    <ParameterRefRef RefId="{_SENSITIVITY_COPY}" />
    <choose ParamRefId="{_SENSITIVITY_COPY}">
      <when test="0"><ParameterRefRef RefId="{_APP}_UP-4_R-5" /></when>
      <when test="1"><ParameterRefRef RefId="{_APP}_UP-5_R-6" /></when>
    </choose>""",
)

_CODE_APP = "M-00FA_A-0C04-01-0000"
_FUNCTION = f"{_CODE_APP}_P-1_R-1"
_CODE = f"{_CODE_APP}_P-2_R-2"
_VALUE = f"{_CODE_APP}_UP-4_R-4"
_CODE_XML = _program(
    _CODE_APP,
    f"""<Parameters>
      <Parameter Id="{_CODE_APP}_P-1" Name="Function" ParameterType="{_CODE_APP}_PT-Choice" Text="Function" Value="0" />
      <Parameter Id="{_CODE_APP}_P-2" Name="Code" ParameterType="{_CODE_APP}_PT-Byte" Text="" Access="None" Value="0">
        <Memory CodeSegment="{_CODE_APP}_AS-0000" Offset="0" BitOffset="0" />
      </Parameter>
      <Union SizeInBit="8">
        <Memory CodeSegment="{_CODE_APP}_AS-0000" Offset="1" BitOffset="0" />
        <Parameter Id="{_CODE_APP}_UP-3" Name="Reserved" ParameterType="{_CODE_APP}_PT-Byte" Offset="0" BitOffset="0" Text="" Access="None" Value="0" />
        <Parameter Id="{_CODE_APP}_UP-4" Name="Value" ParameterType="{_CODE_APP}_PT-Byte" Offset="0" BitOffset="0" Text="Value" Value="0" />
      </Union>
    </Parameters>
    <ParameterRefs>
      <ParameterRef Id="{_FUNCTION}" RefId="{_CODE_APP}_P-1" />
      <ParameterRef Id="{_CODE}" RefId="{_CODE_APP}_P-2" />
      <ParameterRef Id="{_CODE_APP}_UP-3_R-3" RefId="{_CODE_APP}_UP-3" />
      <ParameterRef Id="{_VALUE}" RefId="{_CODE_APP}_UP-4" />
    </ParameterRefs>
    <ParameterCalculations>
      <ParameterCalculation Id="{_CODE_APP}_PC-1" Name="Code" Language="JavaScript">
        <RLTransformation>//Not implemented</RLTransformation>
        <LRTransformation>c = 0; if (f == 1 || f == 2) {{ c = 7; }}</LRTransformation>
        <LParameters><ParameterRefRef RefId="{_FUNCTION}" AliasName="f" /></LParameters>
        <RParameters><ParameterRefRef RefId="{_CODE}" AliasName="c" /></RParameters>
      </ParameterCalculation>
    </ParameterCalculations>""",
    f"""<ParameterRefRef RefId="{_FUNCTION}" />
    <ParameterRefRef RefId="{_CODE}" />
    <choose ParamRefId="{_FUNCTION}">
      <when test="0 1"><ParameterRefRef RefId="{_CODE_APP}_UP-3_R-3" /></when>
      <when test="2"><ParameterRefRef RefId="{_VALUE}" /></when>
    </choose>""",
)


@pytest.fixture(scope="module")
def sensitivity() -> Application:
    return parse_application_xml(_SENSITIVITY_XML.encode(), "M-00FA")[0]


@pytest.fixture(scope="module")
def function_code() -> Application:
    return parse_application_xml(_CODE_XML.encode(), "M-00FA")[0]


def _device(application: Application, *values: tuple[str, str]) -> dict[str, bytes]:
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values:
        ui.edit_parameter(ref_id, value)
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_sensitivity_places_the_member_with_its_fixed_value(
    sensitivity: Application,
) -> None:
    memory = _device(sensitivity, (_SENSITIVITY, "1"), (_MODE, "5"))
    assert memory[f"{_APP}_AS-0000"] == bytes([5, 200])


def test_a_shown_copy_beside_a_value_read_is_not_decided_by_it(
    sensitivity: Application,
) -> None:
    ui = seed_dynamic_ui(sensitivity, {_MODE_STORED: "5"})
    assert ui is not None
    assert not _calculated_from_reads(ui, _SENSITIVITY)


def test_the_sensitivity_is_found_from_the_member_it_places(
    sensitivity: Application,
) -> None:
    memory = _device(sensitivity, (_SENSITIVITY, "1"), (_MODE, "5"))

    result = recover_parameters(sensitivity, memory, effort=RecoveryEffort.FAST)

    assert result.complete
    assert result.values[_SENSITIVITY] == "1"
    assert result.values[_MODE] == "5"


def test_the_default_sensitivity_stays(sensitivity: Application) -> None:
    memory = _device(sensitivity, (_MODE, "5"))

    result = recover_parameters(sensitivity, memory, effort=RecoveryEffort.FAST)

    assert result.complete
    assert result.values.get(_SENSITIVITY, "0") == "0"


def test_the_functions_with_the_code_read(function_code: Application) -> None:
    ui = seed_dynamic_ui(function_code, {_CODE: "7"})
    assert ui is not None
    assert _calculated_from_reads(ui, _FUNCTION)
    values = {_CODE: "7", _FUNCTION: "1"}
    # Both functions give the code read; the first does not.
    assert _alike_for_reads(function_code, values, None, _FUNCTION, "2")
    assert not _alike_for_reads(function_code, values, None, _FUNCTION, "0")


def test_the_function_the_code_does_not_tell_is_found_from_its_value(
    function_code: Application,
) -> None:
    memory = _device(function_code, (_FUNCTION, "2"), (_VALUE, "9"))
    assert memory[f"{_CODE_APP}_AS-0000"] == bytes([7, 9])

    result = recover_parameters(function_code, memory, effort=RecoveryEffort.FAST)

    assert result.complete
    assert result.values[_FUNCTION] == "2"
    assert result.values[_VALUE] == "9"


def test_the_first_function_of_the_code_stays_without_a_value(
    function_code: Application,
) -> None:
    memory = _device(function_code, (_FUNCTION, "1"))

    result = recover_parameters(function_code, memory, effort=RecoveryEffort.FAST)

    assert result.complete
    assert result.values[_FUNCTION] == "1"

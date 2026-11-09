"""A function the device keeps no copy of whose right value fits only once two settings it
shows are corrected too.

The logic's "Operation" (kept nowhere) picks one of three references of a hidden marker the
product fixes per choice, and for "Or" shows its two "Input" choices (kept nowhere either),
each of which shows one of the two push button settings the device stores. "And" shows the
push buttons through other references, which decode any memory. The marker tells "Or", but
"Or" alone leaves both push buttons unwritten, and with one input corrected it fits
as well as "And" does: only with both inputs it fits.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0D07-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_OPERATION = f"{_APP}_P-1_R-1"
_FIRST = f"{_APP}_P-3_R-30"
_SECOND = f"{_APP}_P-4_R-40"
_BUTTON_1 = f"{_APP}_P-5_R-50"
_BUTTON_2 = f"{_APP}_P-6_R-61"


def _input(ref: str, button_1: str, button_2: str) -> str:
    return f"""<ParameterRefRef RefId="{ref}" />
<choose ParamRefId="{ref}">
  <when test="1"><ParameterRefRef RefId="{button_1}" /></when>
  <when test="2"><ParameterRefRef RefId="{button_2}" /></when>
</choose>"""


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="13" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Logic inputs"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-L" Name="L">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="disabled" Value="255" Id="{_APP}_PT-L_EN-255" />
              <Enumeration Text="And" Value="1" Id="{_APP}_PT-L_EN-1" />
              <Enumeration Text="Or" Value="0" Id="{_APP}_PT-L_EN-0" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-I" Name="I">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="none" Value="0" Id="{_APP}_PT-I_EN-0" />
              <Enumeration Text="Push button 1" Value="1" Id="{_APP}_PT-I_EN-1" />
              <Enumeration Text="Push button 2" Value="2" Id="{_APP}_PT-I_EN-2" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="3" />
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Operation" ParameterType="{_APP}_PT-L" Text="Operation" Value="255" />
          <Parameter Id="{_APP}_P-2" Name="Marker" ParameterType="{_APP}_PT-L" Text="" Access="None" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-3" Name="First" ParameterType="{_APP}_PT-I" Text="Input 1" Value="0" />
          <Parameter Id="{_APP}_P-4" Name="Second" ParameterType="{_APP}_PT-I" Text="Input 2" Value="0" />
          <Parameter Id="{_APP}_P-5" Name="Button1" ParameterType="{_APP}_PT-B" Text="Push button 1" Value="1">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-6" Name="Button2" ParameterType="{_APP}_PT-B" Text="Push button 2" Value="1">
            <Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_OPERATION}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_APP}_P-2_R-20" RefId="{_APP}_P-2" Value="255" />
          <ParameterRef Id="{_APP}_P-2_R-21" RefId="{_APP}_P-2" Value="1" />
          <ParameterRef Id="{_APP}_P-2_R-22" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_FIRST}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_SECOND}" RefId="{_APP}_P-4" />
          <ParameterRef Id="{_APP}_P-3_R-31" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_APP}_P-4_R-41" RefId="{_APP}_P-4" />
          <ParameterRef Id="{_BUTTON_1}" RefId="{_APP}_P-5" />
          <ParameterRef Id="{_APP}_P-6_R-60" RefId="{_APP}_P-6" />
          <ParameterRef Id="{_APP}_P-5_R-51" RefId="{_APP}_P-5" />
          <ParameterRef Id="{_BUTTON_2}" RefId="{_APP}_P-6" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="Logic">
            <ParameterRefRef RefId="{_OPERATION}" />
            <choose ParamRefId="{_OPERATION}">
              <when test="255"><ParameterRefRef RefId="{_APP}_P-2_R-20" /></when>
              <when test="1">
                <ParameterRefRef RefId="{_APP}_P-2_R-21" />
                {_input(f"{_APP}_P-3_R-31", f"{_APP}_P-5_R-51", f"{_APP}_P-6_R-60")}
                {_input(f"{_APP}_P-4_R-41", f"{_APP}_P-5_R-51", f"{_APP}_P-6_R-60")}
              </when>
              <when test="0">
                <ParameterRefRef RefId="{_APP}_P-2_R-22" />
                {_input(_FIRST, _BUTTON_1, f"{_APP}_P-6_R-60")}
                {_input(_SECOND, f"{_APP}_P-5_R-51", _BUTTON_2)}
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, values: dict[str, str]) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


_OR = {_OPERATION: "0", _FIRST: "1", _SECOND: "2", _BUTTON_1: "3", _BUTTON_2: "2"}


def test_or_stores_the_marker_and_both_push_buttons(application: Application) -> None:
    assert _device(application, _OR)[_SEGMENT][:3] == bytes((0, 3, 2))
    # "Or" alone shows neither push button: their bytes keep the segment's data.
    assert _device(application, {_OPERATION: "0"})[_SEGMENT][:3] == bytes((0, 0, 0))


def test_the_function_is_found_with_both_inputs_behind_it(
    application: Application,
) -> None:
    recovered = recover_parameters(
        application, _device(application, _OR), effort=RecoveryEffort.MEDIUM
    )
    assert recovered.complete
    assert {key: recovered.values.get(key) for key in _OR} == _OR


# A button whose "Function" (kept nowhere) shows its "Mode" (kept nowhere either), which shows
# two "Assignment" switches (kept nowhere) that each pick the reference of one bit of a byte the
# product fixes per choice; the other function sets both bits. "Function" alone fits worse (the
# byte of the "Mode" marker, and the bits), with the right "Mode" as well as before (the bits
# still differ), with one assignment in fewer bits, and only with both it fits.
_BUTTON_APP = "M-00FA_A-0D08-01-0000"
_B = _BUTTON_APP
_FUNCTION = f"{_B}_P-1_R-1"
_MODE = f"{_B}_P-2_R-2"
_LEFT = f"{_B}_P-3_R-3"
_RIGHT = f"{_B}_P-4_R-4"


def _fixed(parameter: int, values: tuple[int, int]) -> str:
    return "".join(
        f'<ParameterRef Id="{_B}_P-{parameter}_R-{parameter}{n}" RefId="{_B}_P-{parameter}"'
        f' Value="{value}" />'
        for n, value in enumerate(values)
    )


def _pick(selector: str, parameter: int) -> str:
    return f"""<ParameterRefRef RefId="{selector}" />
<choose ParamRefId="{selector}">
  <when test="0"><ParameterRefRef RefId="{_B}_P-{parameter}_R-{parameter}0" /></when>
  <when test="1"><ParameterRefRef RefId="{_B}_P-{parameter}_R-{parameter}1" /></when>
</choose>"""


def _memory(offset: int, bit: int = 0) -> str:
    return f'<Memory CodeSegment="{_B}_AS-0000" Offset="{offset}" BitOffset="{bit}" />'


_BUTTON_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_B}" ApplicationNumber="14" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Button assignments"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_B}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_B}_PT-S" Name="S">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
          </ParameterType>
          <ParameterType Id="{_B}_PT-M" Name="M">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="2" />
          </ParameterType>
          <ParameterType Id="{_B}_PT-N" Name="N">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
          </ParameterType>
          <ParameterType Id="{_B}_PT-1" Name="Bit">
            <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_B}_P-1" Name="Function" ParameterType="{_B}_PT-S" Text="Function" Value="0" />
          <Parameter Id="{_B}_P-2" Name="Mode" ParameterType="{_B}_PT-M" Text="Mode" Value="0" />
          <Parameter Id="{_B}_P-3" Name="Left" ParameterType="{_B}_PT-S" Text="Left assignment" Value="0" />
          <Parameter Id="{_B}_P-4" Name="Right" ParameterType="{_B}_PT-S" Text="Right assignment" Value="0" />
          <Parameter Id="{_B}_P-5" Name="FunctionMarker" ParameterType="{_B}_PT-N" Text="" Access="None" Value="0">{_memory(0)}</Parameter>
          <Parameter Id="{_B}_P-6" Name="ModeMarker" ParameterType="{_B}_PT-N" Text="" Access="None" Value="0">{_memory(1)}</Parameter>
          <Parameter Id="{_B}_P-7" Name="LeftBit" ParameterType="{_B}_PT-1" Text="" Access="None" Value="0">{_memory(2, 7)}</Parameter>
          <Parameter Id="{_B}_P-8" Name="RightBit" ParameterType="{_B}_PT-1" Text="" Access="None" Value="0">{_memory(2, 6)}</Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_FUNCTION}" RefId="{_B}_P-1" />
          <ParameterRef Id="{_MODE}" RefId="{_B}_P-2" />
          <ParameterRef Id="{_LEFT}" RefId="{_B}_P-3" />
          <ParameterRef Id="{_RIGHT}" RefId="{_B}_P-4" />
          {_fixed(5, (7, 0))}{_fixed(6, (5, 0))}{_fixed(7, (0, 1))}{_fixed(8, (0, 1))}
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_B}_PB-1" Text="Button">
            {_pick(_FUNCTION, 5)}
            <choose ParamRefId="{_FUNCTION}">
              <when test="0">
                <ParameterRefRef RefId="{_B}_P-7_R-71" />
                <ParameterRefRef RefId="{_B}_P-8_R-81" />
              </when>
              <when test="1">
                <ParameterRefRef RefId="{_MODE}" />
                <choose ParamRefId="{_MODE}">
                  <when test="0 1"><ParameterRefRef RefId="{_B}_P-6_R-60" /></when>
                  <when test="2">
                    <ParameterRefRef RefId="{_B}_P-6_R-61" />
                    {_pick(_LEFT, 7)}
                    {_pick(_RIGHT, 8)}
                  </when>
                </choose>
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""

_BOTH = {_FUNCTION: "1", _MODE: "2", _LEFT: "1", _RIGHT: "1"}


@pytest.fixture(scope="module")
def button() -> Application:
    return parse_application_xml(_BUTTON_XML.encode(), "M-00FA")[0]


def test_the_assignments_set_the_bits_behind_the_mode(button: Application) -> None:
    assert _device(button, _BOTH)[f"{_B}_AS-0000"][:3] == bytes((0, 0, 3))
    assert _device(button, {})[f"{_B}_AS-0000"][:3] == bytes((7, 0, 3))
    assert _device(button, {_FUNCTION: "1"})[f"{_B}_AS-0000"][:3] == bytes((0, 5, 0))


def test_the_function_is_found_with_the_settings_its_mode_shows(
    button: Application,
) -> None:
    recovered = recover_parameters(
        button, _device(button, _BOTH), effort=RecoveryEffort.MEDIUM
    )
    assert recovered.complete
    assert {key: recovered.values.get(key) for key in _BOTH} == _BOTH


# A "Kind" (kept nowhere) whose "Variant" (kept nowhere either) picks the references of two
# markers the product fixes per choice: the default variant leaves both bytes differing, the
# first one tried one of them (as close as the other kind), the last one none.
_KIND_APP = "M-00FA_A-0D0A-01-0000"
_K = _KIND_APP
_KIND = f"{_K}_P-1_R-1"
_VARIANT = f"{_K}_P-2_R-2"


def _marker(parameter: int, offset: int, values: tuple[int, ...]) -> tuple[str, str]:
    """A hidden marker at ``offset`` with a reference per value: the parameter and its refs."""
    declaration = (
        f'<Parameter Id="{_K}_P-{parameter}" Name="M{parameter}" ParameterType="{_K}_PT-N"'
        f' Text="" Access="None" Value="0"><Memory CodeSegment="{_K}_AS-0000"'
        f' Offset="{offset}" BitOffset="0" /></Parameter>'
    )
    refs = "".join(
        f'<ParameterRef Id="{_K}_P-{parameter}_R-{parameter}{n}" RefId="{_K}_P-{parameter}"'
        f' Value="{value}" />'
        for n, value in enumerate(values)
    )
    return declaration, refs


_KIND_MARKERS = [
    _marker(3, 0, (7, 0)),
    _marker(4, 1, (0, 9, 0)),
    _marker(5, 2, (9, 9, 0)),
]


def _variant(n: int) -> str:
    return (
        f'<when test="{n}"><ParameterRefRef RefId="{_K}_P-4_R-4{n}" />'
        f'<ParameterRefRef RefId="{_K}_P-5_R-5{n}" /></when>'
    )


_KIND_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_K}" ApplicationNumber="16" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Kind variants"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_K}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_K}_PT-K" Name="K">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
          </ParameterType>
          <ParameterType Id="{_K}_PT-S" Name="S">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="2" />
          </ParameterType>
          <ParameterType Id="{_K}_PT-N" Name="N">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_K}_P-1" Name="Kind" ParameterType="{_K}_PT-K" Text="Kind" Value="0" />
          <Parameter Id="{_K}_P-2" Name="Variant" ParameterType="{_K}_PT-S" Text="Variant" Value="1" />
          {"".join(declaration for declaration, _ in _KIND_MARKERS)}
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_KIND}" RefId="{_K}_P-1" />
          <ParameterRef Id="{_VARIANT}" RefId="{_K}_P-2" />
          {"".join(refs for _, refs in _KIND_MARKERS)}
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_K}_PB-1" Text="Kind">
            <ParameterRefRef RefId="{_KIND}" />
            <choose ParamRefId="{_KIND}">
              <when test="0"><ParameterRefRef RefId="{_K}_P-3_R-30" /></when>
              <when test="1">
                <ParameterRefRef RefId="{_K}_P-3_R-31" />
                <ParameterRefRef RefId="{_VARIANT}" />
                <choose ParamRefId="{_VARIANT}">{"".join(_variant(n) for n in range(3))}</choose>
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def kinds() -> Application:
    return parse_application_xml(_KIND_XML.encode(), "M-00FA")[0]


def test_the_variants_set_the_markers(kinds: Application) -> None:
    segment = f"{_K}_AS-0000"
    assert _device(kinds, {_KIND: "1", _VARIANT: "2"})[segment][:3] == bytes(3)
    assert _device(kinds, {_KIND: "1"})[segment][:3] == bytes((0, 9, 9))
    assert _device(kinds, {_KIND: "1", _VARIANT: "0"})[segment][:3] == bytes((0, 0, 9))


def test_the_variant_that_fits_is_found_behind_one_that_fits_closer(
    kinds: Application,
) -> None:
    want = {_KIND: "1", _VARIANT: "2"}
    recovered = recover_parameters(
        kinds, _device(kinds, want), effort=RecoveryEffort.MEDIUM
    )
    assert recovered.complete
    assert {key: recovered.values.get(key) for key in want} == want

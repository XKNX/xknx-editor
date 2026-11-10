"""A setting shown behind a selector an Assign sets later in the tree keeps the value read.

"Flag" has no memory: only the Assigns of "Gate" set it, and they come after the choose on
"Flag" that shows "Setting". While "Flag" is 0 a fixed Assign writes 0 into "Setting". Values
read off a device give "Gate" and "Setting" but not "Flag": an evaluation that starts from the
default of "Flag" writes 0 into "Setting" before "Gate" sets "Flag" to 1, and the value read is
lost although nothing writes "Setting" in the end.
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F3-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_GATE = f"{_APP}_P-1_R-1"
_FLAG = f"{_APP}_P-2_R-2"
_SETTING = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="243" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Late selector"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Gate" ParameterType="{_APP}_PT-B" Text="Gate" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-2" Name="Flag" ParameterType="{_APP}_PT-B" Text="Flag" Value="0" Access="None" />
          <Parameter Id="{_APP}_P-3" Name="Setting" ParameterType="{_APP}_PT-B" Text="Setting" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_GATE}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_FLAG}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_SETTING}" RefId="{_APP}_P-3" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_GATE}" />
            <choose ParamRefId="{_FLAG}">
              <when test="1"><ParameterRefRef RefId="{_SETTING}" /></when>
              <when default="true"><Assign TargetParamRefRef="{_SETTING}" Value="0" /></when>
            </choose>
            <choose ParamRefId="{_GATE}">
              <when test="1"><Assign TargetParamRefRef="{_FLAG}" Value="1" /></when>
              <when default="true"><Assign TargetParamRefRef="{_FLAG}" Value="0" /></when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application) -> dict[str, bytes]:
    """The memory of a device with "Gate" and then "Setting" switched on."""
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_GATE, "1")
    ui.set_parameter_ref(_SETTING, "1")
    assert ui.get_value(_FLAG) == "1"
    assert ui.assign_targets() == {_FLAG}
    return ui.encode_to_memory()


def test_a_seeded_value_is_kept() -> None:
    ui = seed_dynamic_ui(_application(), {_GATE: "1", _SETTING: "1"})
    assert ui is not None
    assert ui.get_value(_SETTING) == "1"
    assert ui.get_value(_FLAG) == "1"
    assert ui.encode_to_memory()[_SEGMENT] == b"\x01\x01"


def _opened(application: Application, values: dict[str, str]) -> DynamicUI:
    """A UI evaluated once with ``values``, as a project opens."""
    program, tree_builder = application.resolved()
    ui = DynamicUI(program, parameter_values=values, tree_builder=tree_builder)
    ui.ui()
    return ui


def test_no_value_is_lost_to_the_first_pass() -> None:
    # The first pass of an evaluation selects a branch of every choose: the Assign behind the
    # choose on "Flag" writes 0 into "Setting" there. Once "Gate" has set "Flag", that Assign
    # does not apply, and the value given is kept by one evaluation.
    ui = _opened(_application(), {_GATE: "1", _SETTING: "1"})
    assert ui.get_value(_FLAG) == "1"
    assert ui.get_value(_SETTING) == "1"


def test_the_fixed_assign_applies_while_the_flag_is_off() -> None:
    application = _application()
    assert _opened(application, {_GATE: "1", _SETTING: "0"}).get_value(_SETTING) == "0"
    # "Gate" leaves "Flag" at 0: the fixed Assign writes "Setting".
    ui = _opened(application, {_GATE: "0", _SETTING: "1"})
    assert ui.get_value(_FLAG) == "0"
    assert ui.get_value(_SETTING) == "0"


def test_the_device_is_recovered_completely() -> None:
    application = _application()
    segments = _device(application)
    assert segments[_SEGMENT] == b"\x01\x01"
    recovered = recover_parameters(application, segments)
    assert recovered.complete
    assert recovered.values[_GATE] == "1"
    assert recovered.values[_SETTING] == "1"
    # A project with the values recovered opens with the value read.
    assert _opened(application, recovered.values).get_value(_SETTING) == "1"

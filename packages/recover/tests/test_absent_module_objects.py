"""Reading a device back: a linked group object of a module instance that only a setting the
device keeps no copy of makes (a function switched on), behind a stored setting of the instance,
is found by trying the selectors on the way to the module."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _absent_module_gates,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F3-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_EASY = f"{_APP}_P-1_R-1"
_OTHER = f"{_APP}_P-2_R-2"
_ENABLE = f"{_MD}_M-1_MI-1_P-1_R-1"


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="243" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Absent module objects"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Easy" ParameterType="{_APP}_PT-B" Text="Easy control" Value="0" />
          <Parameter Id="{_APP}_P-2" Name="Other" ParameterType="{_APP}_PT-B" Text="Other" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="3" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_EASY}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_OTHER}" RefId="{_APP}_P-2" />
        </ParameterRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Function">
          <Arguments>
            <Argument Id="{_MD}_A-1" Name="Offset" />
            <Argument Id="{_MD}_A-2" Name="ObjectBase" />
          </Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Enable" ParameterType="{_APP}_PT-B" Text="Output" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
            </ParameterRefs>
            <ComObjects>
              <ComObject Id="{_MD}_O-1" Name="Output" Text="Output" Number="1" FunctionText=""
                  ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
                  TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"
                  BaseNumber="{_MD}_A-2" />
            </ComObjects>
            <ComObjectRefs>
              <ComObjectRef Id="{_MD}_O-1_R-1" RefId="{_MD}_O-1" />
            </ComObjectRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Function">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <choose ParamRefId="{_MD}_P-1_R-1">
                <when test="1"><ComObjectRefRef RefId="{_MD}_O-1_R-1" /></when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_EASY}" />
            <ParameterRefRef RefId="{_OTHER}" />
          </ParameterBlock>
          <choose ParamRefId="{_EASY}">
            <when test="1">
              <Module Id="{_MD}_M-1" RefId="{_MD}" Name="Easy function">
                <NumericArg RefId="{_MD}_A-1" Value="1" />
                <NumericArg RefId="{_MD}_A-2" Value="10" />
              </Module>
            </when>
          </choose>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, **values: str) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id -> value)."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values.items():
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


def test_the_switch_of_a_module_whose_object_is_linked_is_found(
    application: Application,
) -> None:
    # The function is switched on and its output is on and linked: the module instance is only
    # there with the switch on, its object only with the instance's own setting read too.
    memory = _device(application, **{_EASY: "1", _ENABLE: "1"})
    assert memory[_SEGMENT][1] == 1

    result = recover_parameters(
        application,
        memory,
        linked_objects={11},
        object_sizes={11: "1 Bit"},
        effort=RecoveryEffort.FAST,
    )

    assert result.values.get(_EASY) == "1"
    assert result.values.get(_ENABLE) == "1"
    assert result.complete


def test_without_a_linked_object_the_function_stays_off(
    application: Application,
) -> None:
    memory = _device(application)

    result = recover_parameters(application, memory, linked_objects=set())

    assert result.values.get(_EASY, "0") == "0"
    assert result.complete


def test_the_selectors_of_absent_modules_with_objects(application: Application) -> None:
    ui = seed_dynamic_ui(application, {}, None)
    assert ui is not None
    assert _absent_module_gates(ui) == {_EASY}

    shown = seed_dynamic_ui(application, {_EASY: "1"}, None)
    assert shown is not None
    # Made now: nothing left to switch on.
    assert _absent_module_gates(shown) == set()

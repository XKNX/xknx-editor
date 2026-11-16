"""Reading a device back: hidden switches a calculation of a module instance computes from the
instance's stored settings (one "show the object" switch per box of a page) show the linked
objects. Run back from a switch given (a result checked once more), the calculation gives the
first box a placeholder ("Empty") and computes the other switches from it. The instance's
settings are read only after the application's, so that value is no value read: the switches
are computed again from the settings read."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveryEffort,
    _decode,  # pyright: ignore[reportPrivateUsage]
    _shown_objects,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00F7-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_MD = f"{_APP}_MD-1"
_MI = f"{_MD}_M-1_MI-1"
_KIND_1 = f"{_MI}_P-1_R-1"
_KIND_2 = f"{_MI}_P-2_R-2"
_SHOW_1 = f"{_MI}_P-3_R-3"
_SHOW_2 = f"{_MI}_P-4_R-4"


def _object(number: int) -> str:
    return (
        f'<ComObject Id="{_MD}_O-2-{number}" Name="Box{number}" Text="Box {number}"'
        f' Number="{number}" FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled"'
        ' WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Disabled"'
        ' UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1"'
        f' BaseNumber="{_MD}_A-2" />'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="247" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Calculated module switches"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-Kind" Name="Kind">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Empty" Value="0" Id="{_APP}_PT-Kind_EN-0" />
              <Enumeration Text="Switch" Value="1" Id="{_APP}_PT-Kind_EN-1" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
          </ParameterType>
        </ParameterTypes>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Page">
          <Arguments>
            <Argument Id="{_MD}_A-1" Name="Offset" />
            <Argument Id="{_MD}_A-2" Name="ObjectBase" />
          </Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Kind1" ParameterType="{_APP}_PT-Kind" Text="Box 1" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Parameter Id="{_MD}_P-2" Name="Kind2" ParameterType="{_APP}_PT-Kind" Text="Box 2" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Parameter Id="{_MD}_P-3" Name="Show1" ParameterType="{_APP}_PT-B" Text="" Access="None" Value="0" />
              <Parameter Id="{_MD}_P-4" Name="Show2" ParameterType="{_APP}_PT-B" Text="" Access="None" Value="0" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
              <ParameterRef Id="{_MD}_P-2_R-2" RefId="{_MD}_P-2" />
              <ParameterRef Id="{_MD}_P-3_R-3" RefId="{_MD}_P-3" />
              <ParameterRef Id="{_MD}_P-4_R-4" RefId="{_MD}_P-4" />
            </ParameterRefs>
            <ParameterCalculations>
              <ParameterCalculation Id="{_MD}_PC-1" Name="Show" Language="JavaScript">
                <RLTransformation>kind1 = 0;</RLTransformation>
                <LRTransformation>show1 = kind1 ? 1 : 0; show2 = kind2 ? 1 : 0;</LRTransformation>
                <LParameters>
                  <ParameterRefRef RefId="{_MD}_P-1_R-1" AliasName="kind1" />
                  <ParameterRefRef RefId="{_MD}_P-2_R-2" AliasName="kind2" />
                </LParameters>
                <RParameters>
                  <ParameterRefRef RefId="{_MD}_P-3_R-3" AliasName="show1" />
                  <ParameterRefRef RefId="{_MD}_P-4_R-4" AliasName="show2" />
                </RParameters>
              </ParameterCalculation>
            </ParameterCalculations>
            <ComObjects>
              {_object(1)}
              {_object(2)}
            </ComObjects>
            <ComObjectRefs>
              <ComObjectRef Id="{_MD}_O-2-1_R-1" RefId="{_MD}_O-2-1" />
              <ComObjectRef Id="{_MD}_O-2-2_R-2" RefId="{_MD}_O-2-2" />
            </ComObjectRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Page">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <ParameterRefRef RefId="{_MD}_P-2_R-2" />
              <ParameterRefRef RefId="{_MD}_P-3_R-3" />
              <choose ParamRefId="{_MD}_P-3_R-3">
                <when test="1"><ComObjectRefRef RefId="{_MD}_O-2-1_R-1" /></when>
              </choose>
              <ParameterRefRef RefId="{_MD}_P-4_R-4" />
              <choose ParamRefId="{_MD}_P-4_R-4">
                <when test="1"><ComObjectRefRef RefId="{_MD}_O-2-2_R-2" /></when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General" />
        </ChannelIndependentBlock>
        <Module Id="{_MD}_M-1" RefId="{_MD}" Name="Page 1">
          <NumericArg RefId="{_MD}_A-1" Value="2" />
          <NumericArg RefId="{_MD}_A-2" Value="10" />
        </Module>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _memory(application: Application) -> dict[str, bytes]:
    """The memory of a page with both boxes in use: both objects show."""
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_KIND_1, "1")
    ui.set_parameter_ref(_KIND_2, "1")
    assert ui.get_value(_SHOW_1) == "1"
    assert ui.get_value(_SHOW_2) == "1"
    return ui.encode_to_memory()


def test_a_given_switch_leaves_the_others_to_the_settings_read(
    application: Application,
) -> None:
    memory = _memory(application)

    values, unknown = _decode(application, memory, None, None, {_SHOW_2: "1"})

    assert unknown == []
    assert values[_KIND_1] == "1"
    assert values[_KIND_2] == "1"
    ui = seed_dynamic_ui(application, values, None)
    assert ui is not None
    # The first box's switch follows the box read, not the box's default ("Empty").
    assert ui.get_value(_SHOW_1) == "1"
    assert _shown_objects(ui) == {11, 12}


@pytest.mark.parametrize("effort", list(RecoveryEffort))
def test_the_switches_show_the_linked_objects(
    application: Application, effort: RecoveryEffort
) -> None:
    memory = _memory(application)

    result = recover_parameters(
        application,
        memory,
        linked_objects={11, 12},
        object_sizes={11: "1 Bit", 12: "1 Bit"},
        effort=effort,
    )

    assert result.complete
    ui = seed_dynamic_ui(application, result.values, None)
    assert ui is not None
    assert _shown_objects(ui) == {11, 12}

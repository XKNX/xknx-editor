"""The RepeatIndex an export writes for the module instances in nested Repeats, by the ids
the module state gives them (a submodule instance with a counter for each Repeat it is in, a
module instance of the application numbered through all its Repeats), and for the iterations
of a Repeat whose count a parameter gives.
"""

from __future__ import annotations

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.proj.core.knxproj_export import module_repeat_indexes

_APP = "M-00FA_A-0006-01-0000"
_ROOM = f"{_APP}_MD-1"
_DISPLAY = f"{_ROOM}_SM-1"
_LAMP = f"{_APP}_MD-2"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="6" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Nested repeats"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code>
          <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="16"><Data>AAAAAAAAAAAAAAAAAAAAAA==</Data></AbsoluteSegment>
        </Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N" Name="N">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
          </ParameterType>
          <ParameterType Id="{_APP}_PT-C" Name="C">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="2" />
          </ParameterType>
        </ParameterTypes>
        <Allocators>
          <Allocator Id="{_APP}_L-1" Name="Room" Start="0" maxInclusive="15" />
        </Allocators>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_ROOM}" Name="Room">
          <Arguments>
            <Argument Id="{_ROOM}_A-1" Name="Offset" Allocates="8" />
          </Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_ROOM}_P-2" Name="Displays" ParameterType="{_APP}_PT-C" Text="Displays" Value="2" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_ROOM}_P-2_R-2" RefId="{_ROOM}_P-2" />
            </ParameterRefs>
            <Allocators>
              <Allocator Id="{_ROOM}_L-1" Name="Display" Start="1" maxInclusive="7" />
            </Allocators>
          </Static>
          <SubModuleDefs>
            <ModuleDef Id="{_DISPLAY}" Name="Display">
              <Arguments>
                <Argument Id="{_DISPLAY}_A-1" Name="Offset" Allocates="1" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_DISPLAY}_P-1" Name="Brightness" ParameterType="{_APP}_PT-N" Text="Brightness" Value="9">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" BaseOffset="{_DISPLAY}_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_DISPLAY}_P-1_R-1" RefId="{_DISPLAY}_P-1" />
                </ParameterRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_DISPLAY}_PB-1" Text="Display">
                  <ParameterRefRef RefId="{_DISPLAY}_P-1_R-1" />
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </SubModuleDefs>
          <Dynamic>
            <ParameterBlock Id="{_ROOM}_PB-1" Text="Room">
              <ParameterRefRef RefId="{_ROOM}_P-2_R-2" />
            </ParameterBlock>
            <Repeat Id="{_ROOM}_X-1" Name="Walls" Count="2">
              <Repeat Id="{_ROOM}_X-2" Name="Displays" ParameterRefId="{_ROOM}_P-2_R-2">
                <Module Id="{_DISPLAY}_M-1" RefId="{_DISPLAY}">
                  <NumericArg RefId="{_DISPLAY}_A-1" AllocatorRefId="{_ROOM}_L-1" BaseValue="{_ROOM}_A-1" />
                </Module>
              </Repeat>
            </Repeat>
          </Dynamic>
        </ModuleDef>
        <ModuleDef Id="{_LAMP}" Name="Lamp">
          <Static />
          <Dynamic><ParameterBlock Id="{_LAMP}_PB-1" Text="Lamp" /></Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <Repeat Id="{_APP}_X-1" Name="Rooms" Count="2">
            <Module Id="{_ROOM}_M-1" RefId="{_ROOM}">
              <NumericArg RefId="{_ROOM}_A-1" AllocatorRefId="{_APP}_L-1" />
            </Module>
          </Repeat>
          <Repeat Id="{_APP}_X-2" Name="Floors" Count="2">
            <Repeat Id="{_APP}_X-3" Name="Lamps" Count="2">
              <Module Id="{_LAMP}_M-1" RefId="{_LAMP}" />
            </Repeat>
          </Repeat>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def test_every_instance_the_state_makes_has_its_repeat_index() -> None:
    program = parse_application_xml(_XML.encode(), "M-00FA")[0].program
    ids = [
        iid.removeprefix(f"{_APP}_")
        for iid, _ in DynamicUI(program).get_module_instances()
    ]
    indexes = module_repeat_indexes(_XML.encode())
    assert len(ids) == 14
    assert [iid for iid in ids if iid not in indexes] == []


def test_the_places_of_nested_repeats_are_in_the_index() -> None:
    indexes = module_repeat_indexes(_XML.encode())
    # Dynamic 0, ChannelIndependentBlock 1, Repeat 2, Module 3 (its NumericArg 4); in the
    # room: Dynamic 0, ParameterBlock 1 (its ParameterRefRef 2), Repeat 3, Repeat 4, Module 5.
    assert indexes["MD-1_M-1_MI-2"] == "2x2 3x1"
    assert indexes["MD-1_M-1_MI-2_SM-1_M-1_MI-2-1-1-2"] == "2x2 3x1 3x1 4x2 5x1"
    assert indexes["MD-1_M-1_MI-1_SM-1_M-1_MI-1-1-2-1"] == "2x1 3x1 3x2 4x1 5x1"
    # The lamps: Repeat 5, Repeat 6, Module 7; the third is the first of the second floor.
    assert indexes["MD-2_M-1_MI-3"] == "5x2 6x1 7x1"
    assert indexes["MD-2_M-1_MI-4"] == "5x2 6x2 7x1"


def test_a_repeat_counted_by_a_parameter_has_as_many_iterations_as_its_type_allows() -> (
    None
):
    # The displays' Repeat has no Count: its parameter allows 2.
    indexes = module_repeat_indexes(_XML.encode())
    displays = [iid for iid in indexes if "_SM-1_M-1_MI-1-1-1-" in iid]
    assert sorted(displays) == [
        "MD-1_M-1_MI-1_SM-1_M-1_MI-1-1-1-1",
        "MD-1_M-1_MI-1_SM-1_M-1_MI-1-1-1-2",
    ]
    more = _XML.replace('maxInclusive="2"', 'maxInclusive="5"')
    assert "MD-1_M-1_MI-1_SM-1_M-1_MI-1-1-1-5" in module_repeat_indexes(more.encode())
    assert len(module_repeat_indexes(more.encode())) == 2 + 2 * 2 * 5 + 4

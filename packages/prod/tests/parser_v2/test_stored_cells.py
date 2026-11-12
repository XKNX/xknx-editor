"""What encoding and decoding work out once per application stays with it: the bytes where
settings are stored, the writes and the decoded values of module instances, the local ids of
qualified refs. A result handed out cannot change what later calls hand out, and nothing of an
application is kept once it is gone.
"""

from __future__ import annotations

import gc

from xknxeditor.namespaces.intermediate import ModuleDef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-00F2-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_OWN = f"{_APP}_P-9_R-9"
_I = f"{_MD}_M-1_MI-1_"
_SWITCH = f"{_I}P-1_R-1"
_FIRST = f"{_I}UP-2_R-2"
_SECOND = f"{_I}UP-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="242" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Stored cells"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-9" Name="Own" ParameterType="{_APP}_PT-N" Text="Own" Value="4">
            <Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs><ParameterRef Id="{_OWN}" RefId="{_APP}_P-9" /></ParameterRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments><Argument Id="{_MD}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Switch" ParameterType="{_APP}_PT-N" Text="Switch" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Union SizeInBit="8">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
                <Parameter Id="{_MD}_UP-2" Name="First" ParameterType="{_APP}_PT-N" Offset="0" BitOffset="0" Text="First" Value="0" DefaultUnionParameter="true" />
                <Parameter Id="{_MD}_UP-3" Name="Second" ParameterType="{_APP}_PT-N" Offset="0" BitOffset="0" Text="Second" Value="0" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
              <ParameterRef Id="{_MD}_UP-2_R-2" RefId="{_MD}_UP-2" />
              <ParameterRef Id="{_MD}_UP-3_R-3" RefId="{_MD}_UP-3" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <choose ParamRefId="{_MD}_P-1_R-1">
                <when test="0"><ParameterRefRef RefId="{_MD}_UP-2_R-2" /></when>
                <when test="1"><ParameterRefRef RefId="{_MD}_UP-3_R-3" /></when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
          <ParameterRefRef RefId="{_OWN}" />
        </ParameterBlock></ChannelIndependentBlock>
        <Module Id="{_MD}_M-1" RefId="{_MD}" Name="Channel 1">
          <NumericArg RefId="{_MD}_A-1" Value="0" />
        </Module>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _ui() -> DynamicUI:
    return DynamicUI(parse_application_xml(_XML.encode(), "M-00FA")[0].program)


def test_the_stored_cells_hold_the_applications_and_the_instances_refs() -> None:
    cells = _ui().stored_parameter_cells()
    assert cells[(_SEGMENT, 0)] == {_FIRST, _SECOND}
    assert cells[(_SEGMENT, 1)] == {_SWITCH}
    assert cells[(_SEGMENT, 2)] == {_OWN}


def test_a_changed_result_leaves_the_next_one_as_it_was() -> None:
    ui = _ui()
    cells = ui.stored_parameter_cells()
    # The sets cannot be changed in place: what a caller makes of them is its own.
    for key in cells:
        cells[key] |= {"changed"}
    again = ui.stored_parameter_cells()
    assert again[(_SEGMENT, 2)] == {_OWN}
    assert again[(_SEGMENT, 0)] == {_FIRST, _SECOND}
    assert all(isinstance(refs, frozenset) for refs in again.values())


def test_nothing_of_an_application_is_kept_once_it_is_gone() -> None:
    ui = _ui()
    md = ui.indexer.module_defs[_MD]
    assert md.static.parameters is not None
    # The module definition and its parameters (which cannot be weakly referenced), by
    # identity and type.
    watched = {id(md): ModuleDef, id(md.static.parameters.choice): list}
    ui.set_parameter_ref(_SWITCH, "1")
    ui.set_parameter_ref(_SECOND, "7")
    segments = ui.encode_to_memory()
    assert segments[_SEGMENT] == b"\x07\x01\x04"
    assert ui.decode_module_parameters(segments, {})[_SECOND] == "7"
    assert ui.local_ref_id(_SECOND) == f"{_MD}_UP-3_R-3"
    assert ui.stored_parameter_cells()[(_SEGMENT, 2)] == {_OWN}
    del ui, md, segments
    gc.collect()
    objects = gc.get_objects()
    assert not any(watched.get(id(o)) is type(o) and o is not objects for o in objects)


def _ui_with_two_instances() -> DynamicUI:
    """The application with a second instance two bytes on, where its union shares the byte of
    the application's own parameter."""
    second = (
        f'<Module Id="{_MD}_M-2" RefId="{_MD}" Name="Channel 2">'
        f'<NumericArg RefId="{_MD}_A-1" Value="2" /></Module>'
    )
    xml = _XML.replace("</Module>", "</Module>" + second, 1)
    return DynamicUI(parse_application_xml(xml.encode(), "M-00FA")[0].program)


def test_each_instance_adds_its_refs_where_its_arguments_place_them() -> None:
    ui = _ui_with_two_instances()
    other = f"{_MD}_M-2_MI-1_"
    for _ in range(2):
        cells = ui.stored_parameter_cells()
        assert cells[(_SEGMENT, 0)] == {_FIRST, _SECOND}
        assert cells[(_SEGMENT, 1)] == {_SWITCH}
        assert cells[(_SEGMENT, 2)] == {_OWN, f"{other}UP-2_R-2", f"{other}UP-3_R-3"}
        assert cells[(_SEGMENT, 3)] == {f"{other}P-1_R-1"}
        assert all(isinstance(refs, frozenset) for refs in cells.values())

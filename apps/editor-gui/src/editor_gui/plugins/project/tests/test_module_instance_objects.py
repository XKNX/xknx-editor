"""The group objects of a module instance follow the instance: a change that ends it removes
them, and one that creates it again adds the objects its values select, as a device built from
its stored values shows them."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService, _without_objects_of
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml

APP = "M-00FA_A-0006-01-0000"
ON = f"{APP}_P-1_R-1"
MODE = f"{APP}_MD-1_M-1_MI-1_P-1_R-1"
INSTANCE = f"{APP}_MD-1_M-1_MI-1"
SWITCH = f"{INSTANCE}_O-1_R-1"
DIM = f"{INSTANCE}_O-2_R-2"

_FLAGS = (
    'ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled" '
    'TransmitFlag="Disabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"'
)

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{APP}" ApplicationNumber="6" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Module objects"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{APP}_AS-0000" Address="0" Size="8"><Data>AAAAAAAAAAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
              </ParameterType>
              <ParameterType Id="{APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{APP}_P-1" Name="On" ParameterType="{APP}_PT-B" Text="Channel on" Value="1">
                <Memory CodeSegment="{APP}_AS-0000" Offset="0" BitOffset="7" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{APP}_P-1_R-1" RefId="{APP}_P-1" />
            </ParameterRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{APP}_MD-1" Name="Channel">
              <Static>
                <Parameters>
                  <Parameter Id="{APP}_MD-1_P-1" Name="Mode" ParameterType="{APP}_PT-N" Text="Mode" Value="0">
                    <Memory CodeSegment="{APP}_AS-0000" Offset="1" BitOffset="0" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{APP}_MD-1_P-1_R-1" RefId="{APP}_MD-1_P-1" />
                </ParameterRefs>
                <ComObjects>
                  <ComObject Id="{APP}_MD-1_O-1" Name="Switch" Text="Switch" Number="1" FunctionText="Switch" ObjectSize="1 Bit" {_FLAGS} />
                  <ComObject Id="{APP}_MD-1_O-2" Name="Dim" Text="Dim" Number="2" FunctionText="Dim" ObjectSize="4 Bit" {_FLAGS} />
                </ComObjects>
                <ComObjectRefs>
                  <ComObjectRef Id="{APP}_MD-1_O-1_R-1" RefId="{APP}_MD-1_O-1" />
                  <ComObjectRef Id="{APP}_MD-1_O-2_R-2" RefId="{APP}_MD-1_O-2" />
                </ComObjectRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{APP}_MD-1_PB-1" Text="Channel">
                  <ParameterRefRef RefId="{APP}_MD-1_P-1_R-1" />
                  <choose ParamRefId="{APP}_MD-1_P-1_R-1">
                    <when test="0"><ComObjectRefRef RefId="{APP}_MD-1_O-1_R-1" /></when>
                    <when test="4"><ComObjectRefRef RefId="{APP}_MD-1_O-2_R-2" /></when>
                  </choose>
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{APP}_P-1_R-1" />
              </ParameterBlock>
              <choose ParamRefId="{APP}_P-1_R-1">
                <when test="1">
                  <Module Id="{APP}_MD-1_M-1" RefId="{APP}_MD-1" Name="A" />
                </when>
              </choose>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.fixture
def app() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


@pytest.fixture
def proj(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "p.xknx")
    return service


def _set(proj: ProjectService, node_id: int, ref: str, value: str) -> None:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ref, value)


def _stored(proj: ProjectService, node_id: int) -> tuple[set[str], set[str]]:
    """The device's saved group objects (qualified) and module instances."""
    assert proj._pid is not None
    row = next(r for r in proj._svc.devices(proj._pid) if r.id == node_id)
    objects = {
        co.instance_ref_id if co.instance_ref_id.startswith("M-") else co.ref_id
        for co in row.com_objects
    }
    return objects, {mi.instance_id for mi in row.module_instances}


def _visible(proj: ProjectService, node_id: int) -> list[str]:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    return [co.id for co in device.get_visible_com_objects()]


def test_an_instance_that_comes_back_has_the_objects_of_its_values(
    proj: ProjectService, app: Application
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Channel", app)
    assert node_id is not None
    assert _stored(proj, node_id) == ({SWITCH}, {INSTANCE})
    _set(proj, node_id, MODE, "4")
    assert _stored(proj, node_id) == ({DIM}, {INSTANCE})
    _set(proj, node_id, ON, "0")
    assert _stored(proj, node_id) == (set(), set())
    # The instance comes back with mode 4: its dimming object, not the switching one.
    _set(proj, node_id, ON, "1")
    assert _stored(proj, node_id) == ({DIM}, {INSTANCE})
    assert _visible(proj, node_id) == [DIM]
    # Ending it again leaves no object of it behind.
    _set(proj, node_id, ON, "0")
    assert _stored(proj, node_id) == (set(), set())


def test_a_script_that_ends_an_instance_saves_that_with_its_objects(
    proj: ProjectService, app: Application
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Channel", app)
    assert node_id is not None
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    old_active = proj.parameter_driven_com_objects(device)
    # A parameter script changes the value on the device, then the change is stored.
    device.set_param_value(ON, "0")
    proj.persist_script_changes(node_id, {ON: ("1", "0")}, None)
    proj.finish_script_changes(node_id, old_active)
    assert _stored(proj, node_id) == (set(), set())


def test_objects_of_an_ended_instance_leave_even_when_the_change_kept_them() -> None:
    other = f"{APP}_MD-1_M-1_MI-10_O-1_R-1"
    sub = f"{INSTANCE}_SM-1_M-1_MI-1_O-3_R-3"
    device = SimpleNamespace(
        com_objects=[SimpleNamespace(id=ref) for ref in (SWITCH, sub, other, ON)]
    )
    # Saved ids may be relative to the application.
    kept = _without_objects_of(cast("Any", device), None, ["MD-1_M-1_MI-1"], APP)
    assert kept == {other, ON}
    assert _without_objects_of(cast("Any", device), {SWITCH, ON}, [INSTANCE], APP) == {
        ON
    }
    assert _without_objects_of(cast("Any", device), None, [], APP) is None
    assert (
        _without_objects_of(cast("Any", device), None, [f"{APP}_MD-1_M-2_MI-1"], APP)
        is None
    )

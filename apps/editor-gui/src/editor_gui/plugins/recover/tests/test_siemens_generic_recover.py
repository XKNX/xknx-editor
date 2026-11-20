"""Recovering a device configured through the Siemens Generic Plug-in: its parameter memory
is read off it, decoded into the plug-in's configuration and given to the recovered device,
which then places its group objects as the device does.

The product is small and made up (the application and the plug-in's description of it): no
manufacturer product data is used.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

import xknxeditor.recover.recover as recover_mod
from editor_gui.plugin_providers import device_reader
from editor_gui.plugin_providers.siemens_generic import PROVIDER, blob, sources
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from xknxeditor.prod import parse_application_xml
from xknxeditor.recover.dossier import DeviceDossier
from xknxeditor.recover.read_config import RawGroupCommunication
from xknxeditor.recover.tables_decode import DecodedGroupObject, DecodedLink
from xknxeditor.vendor.siemens_generic import parse_settings
from xknxeditor.vendor.siemens_generic.configuration import evaluate
from xknxeditor.vendor.siemens_generic.image import compile_image
from xknxeditor.vendor.siemens_generic.recover import DeviceImage, image_of

_APP = "M-0001_A-0002-01-0000"
_BASE = 0x4000
_PREFIX = PROVIDER.parameter_prefix

APPLICATION = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/14">
 <ManufacturerData><Manufacturer RefId="M-0001"><ApplicationPrograms>
  <ApplicationProgram Id="{_APP}" ApplicationNumber="2" ApplicationVersion="1"
   ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Demo actuator"
   LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
   DynamicTableManagement="false" Linkable="false" MinEtsVersion="4.0">
   <Static>
    <ParameterTypes>
     <ParameterType Id="{_APP}_PT-Size" Name="Size">
      <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255"/>
     </ParameterType>
    </ParameterTypes>
    <Parameters>
     <Parameter Id="{_APP}_P-1" Name="Object0" ParameterType="{_APP}_PT-Size" Text="Object 0"
      Value="0" Access="None"/>
     <Parameter Id="{_APP}_P-2" Name="Object1" ParameterType="{_APP}_PT-Size" Text="Object 1"
      Value="0" Access="None"/>
    </Parameters>
    <ParameterRefs>
     <ParameterRef Id="{_APP}_P-1_R-1" RefId="{_APP}_P-1"/>
     <ParameterRef Id="{_APP}_P-2_R-2" RefId="{_APP}_P-2"/>
    </ParameterRefs>
    <ComObjectTable>
     <ComObject Id="{_APP}_O-0" Name="Switch" Text="Switch" Number="0" FunctionText="On/Off"
      ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
      TransmitFlag="Disabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"
      DatapointType="DPST-1-1"/>
     <ComObject Id="{_APP}_O-1" Name="Value" Text="Value" Number="1" FunctionText="Status"
      ObjectSize="1 Byte" ReadFlag="Enabled" WriteFlag="Disabled" CommunicationFlag="Enabled"
      TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"
      DatapointType="DPST-5-1"/>
    </ComObjectTable>
    <ComObjectRefs>
     <ComObjectRef Id="{_APP}_O-0_R-1" RefId="{_APP}_O-0"/>
     <ComObjectRef Id="{_APP}_O-1_R-2" RefId="{_APP}_O-1"/>
    </ComObjectRefs>
    <Extension EtsUiPlugin="{{F31F098B-BCB7-469A-AF01-3B44AB8242CD}}">
     <Baggage RefId="M-0001_BG-Demo.2Exml"/>
    </Extension>
   </Static>
   <Dynamic>
    <ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Objects">
     <choose ParamRefId="{_APP}_P-1_R-1">
      <when test="1"><ComObjectRefRef RefId="{_APP}_O-0_R-1"/></when>
     </choose>
     <choose ParamRefId="{_APP}_P-2_R-2">
      <when test="8"><ComObjectRefRef RefId="{_APP}_O-1_R-2"/></when>
     </choose>
    </ParameterBlock></ChannelIndependentBlock>
   </Dynamic>
  </ApplicationProgram>
 </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>""".encode()

# The plug-in's description of the product: a mode that shows a delay, a name, and in mode B
# an offset and the object for the value.
DESCRIPTION = f"""<ApplicationProgram Id="id-0001_0002_01"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xsi:noNamespaceSchemaLocation="GenericPlugIn_4.xsd"><Static>
 <CodeSegments><CodeSegment Id="s" Address="{_BASE}" AllocLength="32"/></CodeSegments>
 <ParameterTypes>
  <ParameterType Id="t_mode"><TypeRestriction Size="8">
   <Enumeration Id="e_off" Name="dm_Off" Text="Off" Value="0"/>
   <Enumeration Id="e_a" Name="dm_A" Text="Mode A" Value="1"/>
   <Enumeration Id="e_b" Name="dm_B" Text="Mode B" Value="2"/>
  </TypeRestriction></ParameterType>
  <ParameterType Id="t_word"><TypeNumber Size="16" IntelOrderSwap="true"
   minInclusive="0" maxInclusive="1000"/></ParameterType>
  <ParameterType Id="t_sbyte"><TypeNumber Size="8" Type="signed"/></ParameterType>
  <ParameterType Id="t_text"><TypeText Size="32"/></ParameterType>
 </ParameterTypes>
 <Parameters>
  <Parameter Id="p_mode" RefId="t_mode" Value="dm_Off"><Memory CodeSegment="s" Offset="0"/></Parameter>
  <Parameter Id="p_delay" RefId="t_word" Value="300"><Memory CodeSegment="s" Offset="1"/></Parameter>
  <Parameter Id="p_name" RefId="t_text" Value="Lamp"><Memory CodeSegment="s" Offset="3"/></Parameter>
  <Parameter Id="p_offset" RefId="t_sbyte" Value="0"><Memory CodeSegment="s" Offset="7"/></Parameter>
 </Parameters>
 <ParameterRefs>
  <ParameterRef Id="r_mode" RefId="p_mode"/>
  <ParameterRef Id="r_delay" RefId="p_delay"/>
  <ParameterRef Id="r_name" RefId="p_name"/>
  <ParameterRef Id="r_offset" RefId="p_offset"/>
 </ParameterRefs>
 <ComObjectTable>
  <ComObject Id="o_switch" Number="0" Text="Switch" Size="1 Bit" CommunicationFlag="Enabled"
   WriteFlag="Enabled"/>
  <ComObject Id="o_value" Number="1" Text="Value" Size="1 Byte" CommunicationFlag="Enabled"
   ReadFlag="Enabled" TransmitFlag="Enabled"/>
 </ComObjectTable>
 <ComObjectRefs>
  <ComObjectRef Id="o_switch_r" RefId="o_switch"/>
  <ComObjectRef Id="o_value_r" RefId="o_value"/>
 </ComObjectRefs></Static>
 <Dynamic>
  <ChannelIndependentBlock><ParameterBlock Id="b_gen" Text="General">
   <ParameterRefRef RefId="r_mode"/>
   <ParameterRefRef RefId="r_name"/>
  </ParameterBlock></ChannelIndependentBlock>
  <Channel Id="c1" Number="1"><ParameterBlock Id="b_c1" Text="Channel 1">
   <ComObjectRefRef RefId="o_switch_r"/>
   <choose ParamRefId="r_mode">
    <when test="dm_A;dm_B"><ParameterRefRef RefId="r_delay"/></when>
    <when test="dm_B"><ParameterRefRef RefId="r_offset"/><ComObjectRefRef RefId="o_value_r"/></when>
   </choose>
  </ParameterBlock></Channel>
 </Dynamic></ApplicationProgram>""".encode()

_BAGGAGE = {"M-0001_BG-Demo.2Exml": DESCRIPTION}


def _application():
    return parse_application_xml(APPLICATION, "M-0001")[0]


def _project(path, monkeypatch) -> tuple[ProjectService, int]:
    path.mkdir()
    app = _application()
    project = ProjectService(CatalogService(path / "catalog.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    monkeypatch.setattr(project, "plugin_baggage_source", lambda _: _BAGGAGE.get)
    node_id = project.add_device("P-1", "HP-1", "Demo actuator", app)
    assert node_id is not None
    return project, node_id


def _current(project: ProjectService, node_id: int):
    device = project.find_device_by_node_id(node_id)
    assert device is not None
    return device


def _held(project: ProjectService, node_id: int) -> DeviceImage:
    """The parameter memory the device holds after a download of its configuration."""
    device = _current(project, node_id)
    settings = parse_settings(blob(device))
    assert settings is not None
    return image_of(compile_image(sources(device).description, settings))


def _active(project: ProjectService, node_id: int) -> dict[str, str]:
    device = _current(project, node_id)
    settings = parse_settings(blob(device))
    assert settings is not None
    state = evaluate(sources(device).description, settings)
    return {r: state.values[r] for r in state.active_refs}


def _service(project: ProjectService) -> RecoverService:
    return RecoverService(cast(Any, SimpleNamespace(project=project)))


def _entry(application: Any) -> RecoverEntry:
    entry = RecoverEntry(address="1.1.5", mask_version=0x07B0)
    entry.application = application
    entry.hardware2program_ref_id = "M-0001_H-1-1_HP-1"
    return entry


def _group_object(number: int, **flags: bool) -> DecodedGroupObject:
    values = {
        "communication": True,
        "read": False,
        "write": True,
        "transmit": False,
        "update": False,
        "read_on_init": None,
        **flags,
    }
    return DecodedGroupObject(
        number=number,
        priority="Low",
        size_code=0 if number == 0 else 7,
        object_size="1 Bit" if number == 0 else "1 Byte",
        **values,
    )


def test_a_generic_plug_in_application_has_a_device_reader():
    assert device_reader(_application()) is PROVIDER
    assert device_reader(SimpleNamespace(id="M-00FA_A-0001-01-0000")) is None


EDITS = [
    pytest.param([], id="defaults"),
    pytest.param(
        [("r_mode", "dm_A"), ("r_delay", "900"), ("r_name", "Hall")],
        id="enum-number-text",
    ),
    pytest.param(
        [("r_mode", "dm_B"), ("r_offset", "-4"), ("r_name", "")], id="mode-b-objects"
    ),
]


@pytest.mark.parametrize("edits", EDITS)
def test_the_configuration_read_off_a_device_is_the_recovered_devices(
    tmp_path, monkeypatch, edits
):
    configured, configured_id = _project(tmp_path / "a", monkeypatch)
    configured.setup_plugin(_current(configured, configured_id))
    for ref, value in edits:
        configured.edit_plugin_parameter(
            _current(configured, configured_id), _PREFIX + ref, value
        )
    image = _held(configured, configured_id)

    project, node_id = _project(tmp_path / "b", monkeypatch)
    device = _current(project, node_id)
    recovered = SimpleNamespace(native_image=image, group_objects={}, links=[])
    result = _service(project)._apply_native(device, cast(Any, recovered), device.app)
    assert result is not None
    device, slots = result
    assert _held(project, node_id) == image
    assert _active(project, node_id) == _active(configured, configured_id)
    # Each group object number names the object the configuration places there.
    expected = {0, 1} if ("r_mode", "dm_B") in edits else {0}
    assert set(slots) == expected
    assert slots[0] == f"{_APP}_O-0_R-1"
    assert all(device.find_com_object(ref) is not None for ref in slots.values())


def test_a_memory_no_configuration_gives_is_not_applied(tmp_path, monkeypatch):
    project, node_id = _project(tmp_path / "a", monkeypatch)
    device = _current(project, node_id)
    held = bytearray(_held_default(device))
    held[0] = 9  # no mode has the value 9
    recovered = SimpleNamespace(
        native_image=DeviceImage(_BASE, bytes(held)), group_objects={}, links=[]
    )
    with pytest.raises(ValueError):
        _service(project)._apply_native(device, cast(Any, recovered), device.app)
    with pytest.raises(ValueError):
        PROVIDER.recover(device, b"not an image")


def _held_default(device) -> bytes:
    from xknxeditor.vendor.siemens_generic.configuration import default_config

    description = sources(device).description
    settings = parse_settings(default_config(description))
    assert settings is not None
    return image_of(compile_image(description, settings)).data


def test_the_flags_read_off_the_device_are_set(tmp_path, monkeypatch):
    project, node_id = _project(tmp_path / "a", monkeypatch)
    device = _current(project, node_id)
    recovered = SimpleNamespace(
        native_image=DeviceImage(_BASE, _held_default(device)),
        # Object 0 is linked and can be read; the download switched the communication
        # of the unlinked object 5 off, which is no object of this configuration anyway.
        group_objects={
            0: _group_object(0, read=True),
            5: _group_object(5, communication=False),
        },
        links=[DecodedLink(group_address=0x0801, group_object_number=0, sending=True)],
    )
    result = _service(project)._apply_native(device, cast(Any, recovered), device.app)
    assert result is not None
    device, slots = result
    switch = device.find_com_object(slots[0])
    assert switch is not None
    assert switch.flags.read is True
    assert switch.flags.write is True
    assert switch.flags.communication is True


def test_an_unlinked_objects_communication_stays(tmp_path, monkeypatch):
    project, node_id = _project(tmp_path / "a", monkeypatch)
    device = _current(project, node_id)
    recovered = SimpleNamespace(
        native_image=DeviceImage(_BASE, _held_default(device)),
        group_objects={0: _group_object(0, communication=False)},
        links=[],
    )
    result = _service(project)._apply_native(device, cast(Any, recovered), device.app)
    assert result is not None
    device, slots = result
    switch = device.find_com_object(slots[0])
    assert switch is not None
    assert switch.flags.communication is True


async def test_a_device_read_reads_the_parameter_memory(tmp_path, monkeypatch):
    configured, configured_id = _project(tmp_path / "a", monkeypatch)
    configured.setup_plugin(_current(configured, configured_id))
    for ref, value in (("r_mode", "dm_B"), ("r_delay", "42"), ("r_offset", "3")):
        configured.edit_plugin_parameter(
            _current(configured, configured_id), _PREFIX + ref, value
        )
    image = _held(configured, configured_id)
    memory = {_BASE + i: b for i, b in enumerate(image.data)}
    reads: list[tuple[int, int]] = []

    class Programmer:
        async def read_device_descriptor(self) -> int:
            return 0x07B0

        async def read_memory(self, address: int, count: int, *, user: bool = False):
            reads.append((address, count))
            return bytes(memory.get(a, 0) for a in range(address, address + count))

    async def group_communication(*_args: Any) -> RawGroupCommunication:
        return RawGroupCommunication(
            device_address=None,
            group_addresses=[0x0801],
            links=[
                DecodedLink(group_address=0x0801, group_object_number=1, sending=True)
            ],
            group_objects={1: _group_object(1, read=False)},
        )

    async def nothing(*_args: Any) -> dict[Any, bytes]:
        return {}

    async def dossier(*_args: Any) -> DeviceDossier:
        return DeviceDossier()

    monkeypatch.setattr(recover_mod, "read_group_communication", group_communication)
    monkeypatch.setattr(recover_mod, "read_parameter_memory", nothing)
    monkeypatch.setattr(recover_mod, "read_property_values", nothing)
    monkeypatch.setattr(recover_mod, "read_dossier", dossier)

    project, node_id = _project(tmp_path / "b", monkeypatch)
    entry = _entry(_application())
    assert entry.application is not None
    native_read = _service(project)._native_reader(entry)
    assert native_read is not None
    recovered = await recover_mod.recover_device(
        cast(Any, Programmer()),
        entry.application,
        address="1.1.5",
        native_read=native_read,
    )
    # Exactly the parameter memory a download writes is read, nothing else.
    assert reads == [(_BASE, len(image.data))]
    assert recovered.native_image == image

    device = _current(project, node_id)
    result = _service(project)._apply_native(device, recovered, device.app)
    assert result is not None
    device, slots = result
    assert _held(project, node_id) == image
    assert _active(project, node_id) == _active(configured, configured_id)
    assert slots == {0: f"{_APP}_O-0_R-1", 1: f"{_APP}_O-1_R-2"}
    value = device.find_com_object(slots[1])
    assert value is not None
    assert value.flags.read is False


def test_a_product_without_a_plug_in_reads_nothing(tmp_path, monkeypatch):
    project, _node_id = _project(tmp_path / "a", monkeypatch)
    entry = _entry(SimpleNamespace(id="M-00FA_A-0001-01-0000"))
    assert _service(project)._native_reader(entry) is None

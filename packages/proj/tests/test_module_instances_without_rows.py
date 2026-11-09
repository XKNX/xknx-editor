"""A device whose stored module instances are missing (a project saved before they were stored)
or incomplete: the export writes the module instances its parameter values and group objects
are in from the evaluated application, so their links are not lost and the file reads back."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy.orm import Session
from xknxproject import XKNXProj

from xknxeditor.proj import ProjectService, export_knxproj
from xknxeditor.proj.core.knxproj_export import (
    _instance_order,  # pyright: ignore[reportPrivateUsage]
    _module_instance_of,  # pyright: ignore[reportPrivateUsage]
    _module_instances_in,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import ComObject, Device, ModuleInstance

_NS = "http://knx.org/xml/project/20"
APP = "M-00FA_A-0006-01-0000"
PRODUCT = "M-00FA_H-1_P-1"
HP = "M-00FA_H-1_HP-0006-01-0000"
_FLAGS = (
    'ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled" '
    'TransmitFlag="Disabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"'
)

_HARDWARE = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="{_NS}"><ManufacturerData><Manufacturer RefId="M-00FA"><Hardware>
  <Hardware Id="M-00FA_H-1" Name="Channels" SerialNumber="1" VersionNumber="1"
      HasIndividualAddress="true" HasApplicationProgram="true">
    <Products><Product Id="{PRODUCT}" Text="Channels" OrderNumber="CH-2"
        IsRailMounted="false" DefaultLanguage="en-US" /></Products>
    <Hardware2Programs><Hardware2Program Id="{HP}" MediumTypes="MT-0">
      <ApplicationProgramRef RefId="{APP}" />
    </Hardware2Program></Hardware2Programs>
  </Hardware>
</Hardware></Manufacturer></ManufacturerData></KNX>
"""

# Two instances of a channel module whose group objects are numbered from an argument.
_APPLICATION = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="{_NS}"><ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
  <ApplicationProgram Id="{APP}" ApplicationNumber="6" ApplicationVersion="1"
      ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Channels"
      LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
      DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
    <Static>
      <ParameterTypes>
        <ParameterType Id="{APP}_PT-N" Name="N">
          <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
        </ParameterType>
      </ParameterTypes>
    </Static>
    <ModuleDefs>
      <ModuleDef Id="{APP}_MD-1" Name="Channel">
        <Arguments><Argument Id="{APP}_MD-1_A-1" Name="ObjBase" /></Arguments>
        <Static>
          <Parameters>
            <Parameter Id="{APP}_MD-1_P-1" Name="Mode" ParameterType="{APP}_PT-N" Text="Mode" Value="0" />
          </Parameters>
          <ParameterRefs><ParameterRef Id="{APP}_MD-1_P-1_R-1" RefId="{APP}_MD-1_P-1" /></ParameterRefs>
          <ComObjects>
            <ComObject Id="{APP}_MD-1_O-1" Name="Switch" Text="Switch" Number="1"
                FunctionText="Switch" ObjectSize="1 Bit" BaseNumber="{APP}_MD-1_A-1" {_FLAGS} />
          </ComObjects>
          <ComObjectRefs><ComObjectRef Id="{APP}_MD-1_O-1_R-1" RefId="{APP}_MD-1_O-1" /></ComObjectRefs>
        </Static>
        <Dynamic>
          <ParameterBlock Id="{APP}_MD-1_PB-1" Text="Channel">
            <ParameterRefRef RefId="{APP}_MD-1_P-1_R-1" />
            <ComObjectRefRef RefId="{APP}_MD-1_O-1_R-1" />
          </ParameterBlock>
        </Dynamic>
      </ModuleDef>
    </ModuleDefs>
    <Dynamic>
      <ChannelIndependentBlock><ParameterBlock Id="{APP}_PB-1" Text="General" /></ChannelIndependentBlock>
      <Module Id="{APP}_MD-1_M-1" RefId="{APP}_MD-1" Name="A">
        <NumericArg RefId="{APP}_MD-1_A-1" Value="10" />
      </Module>
      <Module Id="{APP}_MD-1_M-2" RefId="{APP}_MD-1" Name="B">
        <NumericArg RefId="{APP}_MD-1_A-1" Value="20" />
      </Module>
    </Dynamic>
  </ApplicationProgram>
</ApplicationPrograms></Manufacturer></ManufacturerData></KNX>
"""

_PRODUCT_FILES = {
    "M-00FA/Hardware.xml": _HARDWARE.encode(),
    f"M-00FA/{APP}.xml": _APPLICATION.encode(),
}

FIRST = f"{APP}_MD-1_M-1_MI-1"
SECOND = f"{APP}_MD-1_M-2_MI-1"
# The device's module instances as its evaluated application has them.
EVALUATED = {FIRST: [("MD-1_A-1", "10")], SECOND: [("MD-1_A-1", "20")]}


def _project(path: Path, *, stored: tuple[str, ...] = ()) -> int:
    """A device with a linked group object of the second channel and a value of the first,
    and the module instances ``stored`` (as an import keeps them: relative, with their
    arguments and RepeatIndex) - none for a project saved before they were stored."""
    svc = ProjectService()
    pid = svc.create(path, "P-MOD")
    line = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        ln.segments[0].id
        for area in svc.topology(pid, 0).areas
        for ln in area.lines
        if ln.id == line
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        PRODUCT,
        address=1,
        name="Channels",
        hardware2program_ref_id=HP,
        parameters=[(f"{FIRST}_P-1_R-1", "3")],
    )
    ga_id = svc.create_group_address(pid, 0, 0x0801, "Light")
    svc.close(pid)
    with Session(make_engine(url_for(path))) as session:
        device = session.get(Device, device_id)
        assert device is not None
        device.application_program_ref_id = APP
        co = ComObject(
            device_id=device_id,
            ref_id=f"{APP}_MD-1_O-1_R-1",
            instance_ref_id="MD-1_M-2_MI-1_O-1_R-1",
        )
        session.add(co)
        for instance_id in stored:
            relative = instance_id.removeprefix(f"{APP}_")
            session.add(
                ModuleInstance(
                    device_id=device_id,
                    instance_id=relative,
                    ref_id="MD-1",
                    repeat_index="99x1",
                    arguments=[
                        {"ref_id": ref, "value": value}
                        for ref, value in EVALUATED[instance_id]
                    ],
                )
            )
        session.commit()
        co_id = co.id
    svc = ProjectService()
    svc.open(path)
    svc.link_com_object(pid, co_id, ga_id, sending=True)
    svc.close(pid)
    return device_id


def _zero_xml(knxproj: Path) -> str:
    with zipfile.ZipFile(knxproj) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        return zf.read(zero).decode("utf-8")


def _module_instances(xml: str) -> list[str]:
    return re.findall(r"<ModuleInstance\b[^>]*?(?:/>|>.*?</ModuleInstance>)", xml, re.S)


def test_a_project_without_module_instances_keeps_its_module_objects_and_links(
    tmp_path: Path,
) -> None:
    src = tmp_path / "old.xknx"
    device_id = _project(src)
    out = tmp_path / "out.knxproj"
    export_knxproj(
        src, out, extra_files=_PRODUCT_FILES, module_arguments={device_id: EVALUATED}
    )
    xml = _zero_xml(out)
    # The group object with its link, and both instances it and the value are in, numbered
    # and with their arguments from the evaluation, in its order.
    assert re.search(
        r'<ComObjectInstanceRef RefId="MD-1_M-2_MI-1_O-1_R-1"[^>]*Links="[^"]+"', xml
    )
    first, second = _module_instances(xml)
    assert 'Id="MD-1_M-1_MI-1" RefId="MD-1_M-1" RepeatIndex="3x1"' in first
    assert '<Argument RefId="MD-1_A-1" Value="10"' in first
    assert 'Id="MD-1_M-2_MI-1" RefId="MD-1_M-2" RepeatIndex="5x1"' in second
    assert '<Argument RefId="MD-1_A-1" Value="20"' in second
    # The file reads back: the object is numbered from its instance's argument and keeps
    # its group address.
    project = XKNXProj(out).parse()
    (device,) = project["devices"].values()
    (number,) = device["communication_object_ids"]
    com_object = project["communication_objects"][number]
    assert com_object["number"] == 21
    assert com_object["group_address_links"] == ["1/0/1"]


def _instance_ids(knxproj: Path) -> list[str]:
    return [
        re.search(r'Id="([^"]+)"', mi).group(1)  # type: ignore[union-attr]
        for mi in _module_instances(_zero_xml(knxproj))
    ]


def test_every_instance_the_application_reaches_is_written(tmp_path: Path) -> None:
    # As in a project file: also an instance the device has no value or object of.
    src = tmp_path / "old.xknx"
    device_id = _project(src)
    out = tmp_path / "out.knxproj"
    evaluated = {**EVALUATED, f"{APP}_MD-1_M-3_MI-1": [("MD-1_A-1", "30")]}
    export_knxproj(
        src, out, extra_files=_PRODUCT_FILES, module_arguments={device_id: evaluated}
    )
    assert _instance_ids(out) == ["MD-1_M-1_MI-1", "MD-1_M-2_MI-1", "MD-1_M-3_MI-1"]


def test_added_instances_are_in_the_order_of_a_project_file(tmp_path: Path) -> None:
    # By module definition and module number, then by instance number as text (as genuine
    # files list a repeat's instances), whatever order the evaluation has them in; the
    # submodule instances of a module's instances come before the instances.
    src = tmp_path / "old.xknx"
    device_id = _project(src, stored=(SECOND,))
    out = tmp_path / "out.knxproj"
    evaluated = {
        f"{APP}_MD-2_M-5_MI-1": [],
        f"{APP}_MD-1_M-10_MI-2": [],
        f"{APP}_MD-1_M-10_MI-10": [],
        f"{APP}_MD-1_M-10_MI-1_SM-1_M-1_MI-1-1": [],
        f"{APP}_MD-1_M-10_MI-1": [],
        **EVALUATED,
    }
    export_knxproj(src, out, module_arguments={device_id: evaluated})
    assert _instance_ids(out) == [
        "MD-1_M-1_MI-1",
        "MD-1_M-2_MI-1",
        "MD-1_M-10_MI-1_SM-1_M-1_MI-1-1",
        "MD-1_M-10_MI-1",
        "MD-1_M-10_MI-10",
        "MD-1_M-10_MI-2",
        "MD-2_M-5_MI-1",
    ]


def test_the_values_of_an_instance_the_application_no_longer_reaches_stay_out(
    tmp_path: Path,
) -> None:
    # The first channel is not reached any more: a project file has no value without its
    # module instance, so its value is not written; the second one's object stays.
    src = tmp_path / "old.xknx"
    device_id = _project(src)
    out = tmp_path / "out.knxproj"
    export_knxproj(
        src,
        out,
        extra_files=_PRODUCT_FILES,
        module_arguments={device_id: {SECOND: EVALUATED[SECOND]}},
    )
    xml = _zero_xml(out)
    assert f"{FIRST}_P-1_R-1" not in xml
    assert _instance_ids(out) == ["MD-1_M-2_MI-1"]
    assert "MD-1_M-2_MI-1_O-1_R-1" in xml
    XKNXProj(out).parse()
    # Without an evaluation nothing tells the export the instance ended: the value stays.
    export_knxproj(src, out, extra_files=_PRODUCT_FILES)
    assert f"{FIRST}_P-1_R-1" in _zero_xml(out)


def test_stored_instances_stay_as_stored_and_a_missing_one_follows(
    tmp_path: Path,
) -> None:
    src = tmp_path / "part.xknx"
    device_id = _project(src, stored=(FIRST,))
    out = tmp_path / "out.knxproj"
    export_knxproj(
        src, out, extra_files=_PRODUCT_FILES, module_arguments={device_id: EVALUATED}
    )
    first, second = _module_instances(_zero_xml(out))
    # The stored one keeps its RepeatIndex; the one the stored rows miss is added.
    assert 'Id="MD-1_M-1_MI-1" RefId="MD-1_M-1" RepeatIndex="99x1"' in first
    assert 'Id="MD-1_M-2_MI-1" RefId="MD-1_M-2" RepeatIndex="5x1"' in second


def _device_instance(knxproj: Path) -> str:
    match = re.search(
        r"<DeviceInstance\b.*?</DeviceInstance>", _zero_xml(knxproj), re.S
    )
    assert match is not None
    return match.group(0)


def test_a_device_with_complete_instances_is_written_as_stored(tmp_path: Path) -> None:
    src = tmp_path / "full.xknx"
    device_id = _project(src, stored=(FIRST, SECOND))
    with_evaluation = tmp_path / "a.knxproj"
    without = tmp_path / "b.knxproj"
    export_knxproj(
        src,
        with_evaluation,
        extra_files=_PRODUCT_FILES,
        module_arguments={device_id: EVALUATED},
    )
    export_knxproj(src, without, extra_files=_PRODUCT_FILES)
    assert _device_instance(with_evaluation) == _device_instance(without)
    assert _instance_ids(without) == ["MD-1_M-1_MI-1", "MD-1_M-2_MI-1"]
    assert 'RepeatIndex="99x1"' in _device_instance(without)


def test_without_an_evaluation_an_instance_the_device_has_not_stays_out(
    tmp_path: Path,
) -> None:
    # Nothing tells the export the instance's arguments: the object stays out, as one the
    # importers could not number.
    src = tmp_path / "old.xknx"
    _project(src)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, extra_files=_PRODUCT_FILES)
    xml = _zero_xml(out)
    assert "MD-1_M-2_MI-1_O-1_R-1" not in xml
    assert _module_instances(xml) == []
    XKNXProj(out).parse()


def test_the_instances_a_ref_is_in() -> None:
    assert _module_instances_in("O-3_R-4") == []
    assert _module_instances_in(f"{APP}_P-1_R-1") == []
    assert _module_instances_in(f"{APP}_MD-1_M-2_MI-1_P-3_R-1") == ["MD-1_M-2_MI-1"]
    assert _module_instances_in("MD-1_M-2_MI-10_O-2-0_R-1") == ["MD-1_M-2_MI-10"]
    sub = "MD-1_M-2_MI-1_SM-1_M-1_MI-1-1-1"
    assert _module_instances_in(f"{APP}_{sub}_SM-1_P-1_R-1") == ["MD-1_M-2_MI-1", sub]
    assert _module_instances_in(f"{sub}_O-2-0_R-1") == ["MD-1_M-2_MI-1", sub]
    assert _module_instances_in(sub) == ["MD-1_M-2_MI-1", sub]
    assert _module_instance_of(f"{sub}_O-2-0_R-1") == sub
    assert _module_instance_of("O-3_R-4") is None


def test_submodule_instances_come_before_the_instances_they_are_in() -> None:
    # The innermost first; a project file has them so, and the importers rely on it.
    sub = "MD-1_M-2_MI-1_SM-1_M-1_MI-1-1"
    subsub = f"{sub}_SM-2_M-1_MI-1-1-1"
    ids = ["MD-1_M-2_MI-2", "MD-1_M-2_MI-1", sub, subsub, "MD-1_M-1_MI-1"]
    assert sorted(ids, key=_instance_order) == [
        "MD-1_M-1_MI-1",
        subsub,
        sub,
        "MD-1_M-2_MI-1",
        "MD-1_M-2_MI-2",
    ]


def test_a_submodule_instance_is_written_before_the_instance_it_is_in(
    tmp_path: Path,
) -> None:
    src = tmp_path / "sub.xknx"
    sub = f"{FIRST}_SM-1_M-1_MI-1-1"
    device_id = _project(src)
    with Session(make_engine(url_for(src))) as session:
        session.add(
            ComObject(
                device_id=device_id,
                ref_id=f"{APP}_MD-1_SM-1_O-1_R-1",
                instance_ref_id=f"{sub.removeprefix(APP + '_')}_O-1_R-1",
                text_override="Kept",
            )
        )
        session.commit()
    out = tmp_path / "out.knxproj"
    export_knxproj(
        src,
        out,
        module_arguments={device_id: {**EVALUATED, sub: [("SM-1_A-1", "5")]}},
    )
    root = ET.fromstring(_zero_xml(out))
    ids = [
        el.get("Id")
        for el in root.iter()
        if el.tag.rsplit("}", 1)[-1] == "ModuleInstance"
    ]
    assert ids == [sub.removeprefix(APP + "_"), "MD-1_M-1_MI-1", "MD-1_M-2_MI-1"]


SUB_APP = "M-00FA_A-0007-01-0000"
SUB_HP = "M-00FA_H-1_HP-0007-01-0000"

# A room module with a display submodule whose group object is numbered from the submodule's
# argument, which adds the room's base to its own value.
_SUB_APPLICATION = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="{_NS}"><ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
  <ApplicationProgram Id="{SUB_APP}" ApplicationNumber="7" ApplicationVersion="1"
      ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Rooms"
      LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
      DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
    <Static />
    <ModuleDefs>
      <ModuleDef Id="{SUB_APP}_MD-1" Name="Room">
        <Arguments><Argument Id="{SUB_APP}_MD-1_A-1" Name="ObjBase" /></Arguments>
        <SubModuleDefs>
          <ModuleDef Id="{SUB_APP}_MD-1_SM-1" Name="Display">
            <Arguments><Argument Id="{SUB_APP}_MD-1_SM-1_A-1" Name="ObjBase" /></Arguments>
            <Static>
              <ComObjects>
                <ComObject Id="{SUB_APP}_MD-1_SM-1_O-1" Name="Input" Text="Input" Number="1"
                    FunctionText="Input" ObjectSize="1 Bit"
                    BaseNumber="{SUB_APP}_MD-1_SM-1_A-1" {_FLAGS} />
              </ComObjects>
              <ComObjectRefs>
                <ComObjectRef Id="{SUB_APP}_MD-1_SM-1_O-1_R-1" RefId="{SUB_APP}_MD-1_SM-1_O-1" />
              </ComObjectRefs>
            </Static>
            <Dynamic>
              <ParameterBlock Id="{SUB_APP}_MD-1_SM-1_PB-1" Text="Display">
                <ComObjectRefRef RefId="{SUB_APP}_MD-1_SM-1_O-1_R-1" />
              </ParameterBlock>
            </Dynamic>
          </ModuleDef>
        </SubModuleDefs>
        <Dynamic>
          <ParameterBlock Id="{SUB_APP}_MD-1_PB-1" Text="Room">
            <Module Id="{SUB_APP}_MD-1_SM-1_M-1" RefId="{SUB_APP}_MD-1_SM-1">
              <NumericArg RefId="{SUB_APP}_MD-1_SM-1_A-1" Value="2"
                  BaseValue="{SUB_APP}_MD-1_A-1" />
            </Module>
          </ParameterBlock>
        </Dynamic>
      </ModuleDef>
    </ModuleDefs>
    <Dynamic>
      <ChannelIndependentBlock><ParameterBlock Id="{SUB_APP}_PB-1" Text="General" /></ChannelIndependentBlock>
      <Module Id="{SUB_APP}_MD-1_M-1" RefId="{SUB_APP}_MD-1" Name="Room">
        <NumericArg RefId="{SUB_APP}_MD-1_A-1" Value="10" />
      </Module>
    </Dynamic>
  </ApplicationProgram>
</ApplicationPrograms></Manufacturer></ManufacturerData></KNX>
"""

ROOM = f"{SUB_APP}_MD-1_M-1_MI-1"
DISPLAY = f"{ROOM}_SM-1_M-1_MI-1"
# As the evaluation has them: the instance first, the submodule's argument resolved in it.
SUB_EVALUATED = {
    ROOM: [("MD-1_A-1", "10")],
    DISPLAY: [("MD-1_SM-1_A-1", "12")],
}


@pytest.mark.parametrize("stored", [False, True], ids=["no rows", "rows as added"])
def test_a_submodule_object_numbered_from_its_argument_reads_back(
    tmp_path: Path, stored: bool
) -> None:
    # Rows as a device added in the editor stores them: in the order of the evaluation,
    # without arguments.
    src = tmp_path / "rooms.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-SUB")
    line = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        ln.segments[0].id
        for area in svc.topology(pid, 0).areas
        for ln in area.lines
        if ln.id == line
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        PRODUCT,
        address=1,
        name="Rooms",
        hardware2program_ref_id=SUB_HP,
    )
    ga_id = svc.create_group_address(pid, 0, 0x0801, "Display")
    svc.close(pid)
    with Session(make_engine(url_for(src))) as session:
        device = session.get(Device, device_id)
        assert device is not None
        device.application_program_ref_id = SUB_APP
        co = ComObject(
            device_id=device_id,
            ref_id=f"{SUB_APP}_MD-1_SM-1_O-1_R-1",
            instance_ref_id=f"{DISPLAY.removeprefix(SUB_APP + '_')}_SM-1_O-1_R-1",
        )
        session.add(co)
        if stored:
            for instance_id, ref_id in ((ROOM, "MD-1"), (DISPLAY, "MD-1_SM-1")):
                session.add(
                    ModuleInstance(
                        device_id=device_id,
                        instance_id=instance_id,
                        ref_id=f"{SUB_APP}_{ref_id}",
                    )
                )
        session.commit()
        co_id = co.id
    svc = ProjectService()
    svc.open(src)
    svc.link_com_object(pid, co_id, ga_id, sending=True)
    svc.close(pid)
    hardware = _HARDWARE.replace(HP, SUB_HP).replace(APP, SUB_APP)
    out = tmp_path / "out.knxproj"
    export_knxproj(
        src,
        out,
        extra_files={
            "M-00FA/Hardware.xml": hardware.encode(),
            f"M-00FA/{SUB_APP}.xml": _SUB_APPLICATION.encode(),
        },
        module_arguments={device_id: SUB_EVALUATED},
    )
    project = XKNXProj(out).parse()
    (device,) = project["devices"].values()
    (number,) = device["communication_object_ids"]
    com_object = project["communication_objects"][number]
    # 1 + the display's 2 + the room's 10.
    assert com_object["number"] == 13
    assert com_object["group_address_links"] == ["1/0/1"]
    # The submodule instance with its argument, before the instance it is in.
    display, room = _module_instances(_zero_xml(out))
    assert 'Id="MD-1_M-1_MI-1_SM-1_M-1_MI-1"' in display
    assert '<Argument RefId="MD-1_SM-1_A-1" Value="12"' in display
    assert 'Id="MD-1_M-1_MI-1"' in room

"""Tests for :class:`ChangeDeviceApplication` ("Change Application Program")."""

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle

OLD = "M-0001_A-0010-01-AAAA"
NEW = "M-0001_A-0020-01-BBBB"
PRODUCT = "M-0001_H-x-1_P-1"
OLD_H2P = "M-0001_H-x-1_HP-0010-01-AAAA"
NEW_H2P = "M-0001_H-x-1_HP-0020-01-BBBB"


def _project(tmp_path: Path) -> tuple[ProjectService, str, int, int]:
    svc = ProjectService()
    pid = svc.create(
        tmp_path / "p.xknx", "P-0001", group_address_style=GroupAddressStyle.THREE_LEVEL
    )
    seg = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    device_id = svc.add_device(
        pid,
        seg,
        PRODUCT,
        address=7,
        name="Kitchen",
        hardware2program_ref_id=OLD_H2P,
        application_program_ref_id=OLD,
        parameters=[(f"{OLD}_P-1_R-1", "42")],
        com_objects=[(f"{OLD}_O-1_R-1", None)],
        module_instances=[("MD-1_M-1_MI-1", "MD-1_M-1")],
    )
    assert device_id is not None
    ga = svc.create_group_address(pid, 0, 1, "GA")
    svc.link_com_object(pid, svc.devices(pid)[0].com_objects[0].id, ga, sending=True)
    svc.set_device_commissioning(
        pid,
        device_id,
        serial_number="AAECAwQF",
        individual_address_loaded=True,
        application_program_loaded=True,
        parameters_loaded=True,
        communication_part_loaded=True,
    )
    return svc, pid, device_id, ga


def _change(svc: ProjectService, pid: str, device_id: int) -> None:
    svc.change_device_application(
        pid,
        device_id,
        hardware2program_ref_id=NEW_H2P,
        new_app_id=NEW,
        com_objects=[(f"{NEW}_O-1_R-1", None), (f"{NEW}_O-2_R-2", "CH-1")],
        module_instances=[("MD-2_M-1_MI-1", "MD-2_M-1")],
    )


def test_change_starts_from_the_new_application_defaults(tmp_path: Path) -> None:
    svc, pid, device_id, ga = _project(tmp_path)
    _change(svc, pid, device_id)

    device = svc.devices(pid)[0]
    assert device.id == device_id
    assert (device.name, device.address, device.serial_number) == (
        "Kitchen",
        7,
        "AAECAwQF",
    )
    assert device.product_ref_id == PRODUCT
    assert device.hardware2program_ref_id == NEW_H2P
    assert device.application_program_ref_id == NEW
    assert device.parameters == []
    assert [(c.ref_id, c.channel_id, c.links) for c in device.com_objects] == [
        (f"{NEW}_O-1_R-1", None, []),
        (f"{NEW}_O-2_R-2", "CH-1", []),
    ]
    assert [(m.instance_id, m.ref_id) for m in device.module_instances] == [
        ("MD-2_M-1_MI-1", "MD-2_M-1")
    ]
    assert device.individual_address_loaded
    assert not device.application_program_loaded
    assert not device.parameters_loaded
    assert not device.communication_part_loaded
    assert svc.group_address(pid, ga) is not None


def test_change_undo_restores_the_device(tmp_path: Path) -> None:
    svc, pid, device_id, ga = _project(tmp_path)
    _change(svc, pid, device_id)
    assert svc.undo(pid)

    device = svc.devices(pid)[0]
    assert device.hardware2program_ref_id == OLD_H2P
    assert device.application_program_ref_id == OLD
    assert {p.ref_id: p.value for p in device.parameters} == {f"{OLD}_P-1_R-1": "42"}
    (co,) = device.com_objects
    assert co.ref_id == f"{OLD}_O-1_R-1"
    assert [(link.group_address_id, link.is_sending) for link in co.links] == [
        (ga, True)
    ]
    assert [m.instance_id for m in device.module_instances] == ["MD-1_M-1_MI-1"]
    assert device.application_program_loaded and device.parameters_loaded

    assert svc.redo(pid)
    device = svc.devices(pid)[0]
    assert device.hardware2program_ref_id == NEW_H2P
    assert [c.ref_id for c in device.com_objects] == [
        f"{NEW}_O-1_R-1",
        f"{NEW}_O-2_R-2",
    ]
    assert not device.application_program_loaded


def test_update_carries_values_settings_and_links(tmp_path: Path) -> None:
    svc, pid, device_id, ga = _project(tmp_path)
    svc.change_device_application(
        pid,
        device_id,
        hardware2program_ref_id=NEW_H2P,
        new_app_id=NEW,
        com_objects=[(f"{NEW}_O-1_R-1", None), (f"{NEW}_O-2_R-2", None)],
        parameters=[(f"{NEW}_P-1_R-1", "42")],
        com_object_data=[
            {"fields": {"priority": "High"}, "links": [[ga, True]]},
            None,
        ],
    )
    device = svc.devices(pid)[0]
    assert {p.ref_id: p.value for p in device.parameters} == {f"{NEW}_P-1_R-1": "42"}
    first, second = device.com_objects
    assert first.priority == "High"
    assert [(link.group_address_id, link.is_sending) for link in first.links] == [
        (ga, True)
    ]
    assert second.links == []

    assert svc.undo(pid)
    device = svc.devices(pid)[0]
    assert [c.ref_id for c in device.com_objects] == [f"{OLD}_O-1_R-1"]
    assert {p.ref_id for p in device.parameters} == {f"{OLD}_P-1_R-1"}
    assert svc.redo(pid)
    device = svc.devices(pid)[0]
    assert [len(c.links) for c in device.com_objects] == [1, 0]


def test_commissioning_records_the_mac_address(tmp_path: Path) -> None:
    svc, pid, device_id, _ = _project(tmp_path)
    svc.set_device_commissioning(pid, device_id, mac_address="E8:F6:0A:E0:62:3D")
    assert svc.devices(pid)[0].ip_config == {"MACAddress": "E8:F6:0A:E0:62:3D"}
    assert svc.undo(pid)
    assert svc.devices(pid)[0].ip_config is None


def test_undoing_a_change_keeps_the_tool_keys(tmp_path: Path) -> None:
    svc, pid, device_id, _ga = _project(tmp_path)
    svc.set_project_password(pid, "geheim")
    _change(svc, pid, device_id)
    # A download after the change gives the device its Tool Key.
    tool_key = svc.ensure_tool_key(pid, device_id)
    svc.set_tool_key_loaded(pid, device_id)
    assert svc.undo(pid)  # the application change
    keys = svc.device_keys(pid, device_id)
    assert keys is not None
    assert (keys.tool_key, keys.loaded_tool_key) == (tool_key, tool_key)
    svc.close(pid)

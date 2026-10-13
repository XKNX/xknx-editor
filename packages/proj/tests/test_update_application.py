"""Tests for :class:`UpdateDeviceApplication` (ETS-style application update)."""

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle

OLD = "M-0001_A-0010-01-AAAA"
NEW = "M-0001_A-0010-02-BBBB"
OLD_PRODUCT = "M-0001_H-x-1_P-1"
NEW_PRODUCT = "M-0001_H-y-2_P-2"
OLD_H2P = "M-0001_H-x-1_HP-1"
NEW_H2P = "M-0001_H-y-2_HP-2"


def _new(tmp_path: Path) -> tuple[ProjectService, str]:
    svc = ProjectService()
    pid = svc.create(
        tmp_path / "p.xknx", "P-0001", group_address_style=GroupAddressStyle.THREE_LEVEL
    )
    return svc, pid


def _seg(svc: ProjectService, pid: str) -> int:
    return svc.topology(pid, 0).areas[0].lines[0].segments[0].id


def _device_with_config(svc: ProjectService, pid: str) -> int:
    seg = _seg(svc, pid)
    device_id = svc.add_device(
        pid,
        seg,
        OLD_PRODUCT,
        address=1,
        name="D",
        hardware2program_ref_id=OLD_H2P,
        application_program_ref_id=OLD,
        parameters=[
            (f"{OLD}_P-1_R-1", "42"),  # carried
            (f"{OLD}_P-9_R-9", "7"),  # dropped (not in new app)
        ],
        com_objects=[
            (f"{OLD}_O-1_R-1", None),  # carried
            (f"{OLD}_O-5_R-5", None),  # dropped
        ],
    )
    assert device_id is not None
    return device_id


def _update(svc: ProjectService, pid: str, device_id: int) -> tuple[int, int]:
    return svc.update_device_application(
        pid,
        device_id,
        product_ref_id=NEW_PRODUCT,
        hardware2program_ref_id=NEW_H2P,
        old_app_id=OLD,
        new_app_id=NEW,
        valid_ref_ids=[f"{NEW}_P-1_R-1", f"{NEW}_O-1_R-1"],
        order_number="ORD2",
    )


def test_update_keeps_params_links_and_drops_incompatible(tmp_path: Path):
    svc, pid = _new(tmp_path)
    device_id = _device_with_config(svc, pid)

    assert svc.devices(pid)[0].application_program_ref_id == OLD  # captured on add

    ga = svc.create_group_address(pid, 0, 1, "GA")
    ga2 = svc.create_group_address(pid, 0, 2, "GA2")
    device = svc.devices(pid)[0]
    co_keep = next(c for c in device.com_objects if c.ref_id == f"{OLD}_O-1_R-1")
    co_drop = next(c for c in device.com_objects if c.ref_id == f"{OLD}_O-5_R-5")
    svc.link_com_object(pid, co_keep.id, ga, sending=True)
    svc.link_com_object(pid, co_drop.id, ga2)

    kept, dropped = _update(svc, pid, device_id)
    assert (kept, dropped) == (2, 2)  # P-1 + O-1 kept; P-9 + O-5 dropped

    device = svc.devices(pid)[0]
    assert device.product_ref_id == NEW_PRODUCT
    assert device.hardware2program_ref_id == NEW_H2P
    assert device.application_program_ref_id == NEW  # fallback ref follows the repoint
    assert device.order_number == "ORD2"

    params = {p.ref_id: p.value for p in device.parameters}
    assert params == {f"{NEW}_P-1_R-1": "42"}  # carried + re-prefixed, dropped gone

    cos = {c.ref_id: c for c in device.com_objects}
    assert set(cos) == {f"{NEW}_O-1_R-1"}  # carried + re-prefixed, dropped gone
    # link on the carried com-object is preserved (still sending, same GA)
    kept_links = cos[f"{NEW}_O-1_R-1"].links
    assert [(ln.group_address_id, ln.is_sending) for ln in kept_links] == [(ga, True)]


def test_update_undo_restores_everything(tmp_path: Path):
    svc, pid = _new(tmp_path)
    device_id = _device_with_config(svc, pid)
    ga = svc.create_group_address(pid, 0, 1, "GA")
    device = svc.devices(pid)[0]
    co_drop = next(c for c in device.com_objects if c.ref_id == f"{OLD}_O-5_R-5")
    svc.link_com_object(pid, co_drop.id, ga, sending=True)

    _update(svc, pid, device_id)
    assert svc.undo(pid)

    device = svc.devices(pid)[0]
    assert device.product_ref_id == OLD_PRODUCT
    assert device.hardware2program_ref_id == OLD_H2P
    assert device.application_program_ref_id == OLD  # fallback ref restored on undo
    assert device.order_number == ""  # display restored
    assert {p.ref_id for p in device.parameters} == {
        f"{OLD}_P-1_R-1",
        f"{OLD}_P-9_R-9",
    }
    cos = {c.ref_id: c for c in device.com_objects}
    assert set(cos) == {f"{OLD}_O-1_R-1", f"{OLD}_O-5_R-5"}
    # the dropped com-object's link is restored too
    restored_links = cos[f"{OLD}_O-5_R-5"].links
    assert [(ln.group_address_id, ln.is_sending) for ln in restored_links] == [
        (ga, True)
    ]


def test_module_refs_carried_unchanged(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _seg(svc, pid)
    device_id = svc.add_device(
        pid,
        seg,
        OLD_PRODUCT,
        address=1,
        name="D",
        hardware2program_ref_id=OLD_H2P,
        application_program_ref_id=OLD,
        parameters=[("M-100_MI-1_P-2_R-2", "9")],  # module-scoped, not app-prefixed
    )
    assert device_id is not None
    _update(svc, pid, device_id)
    params = {p.ref_id: p.value for p in svc.devices(pid)[0].parameters}
    assert params == {"M-100_MI-1_P-2_R-2": "9"}  # untouched


def test_update_clears_the_application_loaded_flags(tmp_path: Path):
    svc, pid = _new(tmp_path)
    device_id = _device_with_config(svc, pid)
    svc.set_device_commissioning(
        pid,
        device_id,
        individual_address_loaded=True,
        application_program_loaded=True,
        parameters_loaded=True,
        communication_part_loaded=True,
    )
    _update(svc, pid, device_id)
    device = svc.devices(pid)[0]
    assert device.individual_address_loaded
    assert not device.application_program_loaded
    assert not device.parameters_loaded
    assert not device.communication_part_loaded

    assert svc.undo(pid)
    device = svc.devices(pid)[0]
    assert device.application_program_loaded and device.parameters_loaded
    assert device.communication_part_loaded

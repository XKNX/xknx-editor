from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import (
    GroupAddressStyle,
    format_ga,
    format_ia,
    parse_ga,
    parse_ia,
    ranges_for,
)
from xknxeditor.proj.models import ComObject, Device

PRODUCT = "M-0001_H-x-1_P-1"


def _backbone_segment(svc: ProjectService, pid: str) -> int:
    """The default backbone segment (Area 0 / Line 0) the skeleton ships with."""
    return svc.topology(pid, 0).areas[0].lines[0].segments[0].id


def _new(
    tmp_path: Path,
    name: str = "p",
    project_id: str = "P-0001",
    style: GroupAddressStyle = GroupAddressStyle.THREE_LEVEL,
) -> tuple[ProjectService, str]:
    svc = ProjectService()
    pid = svc.create(tmp_path / f"{name}.xknx", project_id, group_address_style=style)
    return svc, pid


def test_skeleton_mirrors_ets(tmp_path: Path):
    svc, pid = _new(tmp_path)
    # installation 0 with the default KNX topology: IP backbone (0.0), plus Area 1 with an IP main
    # line (1.0) and a TP line (1.1) that hosts end devices; empty group addresses
    assert [i.index for i in svc.installations(pid)] == [0]
    topo = svc.topology(pid, 0)
    assert [a.address for a in topo.areas] == [0, 1]
    # backbone: Area 0 / Line 0 on the IP medium
    assert topo.areas[0].address == 0
    assert topo.areas[0].lines[0].address == 0
    assert topo.areas[0].lines[0].segments[0].medium_type == "MT-5"
    # Area 1: IP main line (1.0) + TP device line (1.1)
    assert [ln.address for ln in topo.areas[1].lines] == [0, 1]
    assert topo.areas[1].lines[0].segments[0].medium_type == "MT-5"
    assert topo.areas[1].lines[1].segments[0].medium_type == "MT-0"
    assert svc.group_addresses(pid) == []
    # a default building named after the project file ("p.xknx" -> "p")
    assert [(s.name, s.space_type) for s in svc.space_tree(pid, 0)] == [
        ("p", "Building")
    ]
    project = svc.project(pid)
    assert project.name == "New project"
    assert project.group_address_style == "ThreeLevel"


def test_topology_edits(tmp_path: Path):
    svc, pid = _new(tmp_path)
    area = svc.create_area(pid, 0, 2, "Area")
    svc.create_line(pid, area, 0, "Line")
    seg = svc.topology(pid, 0).areas[2].lines[0].segments[0].id
    dev = svc.add_device(pid, seg, PRODUCT, address=1, name="D")
    ga = svc.create_group_address(pid, 0, 1, "GA")
    # the user-created area sits beside the seeded backbone (0) and device (1) areas
    assert [a.address for a in svc.topology(pid, 0).areas] == [0, 1, 2]
    assert [d.id for d in svc.devices(pid)] == [dev]
    assert [g.id for g in svc.group_addresses(pid)] == [ga]


def test_duplicate_individual_address_rejected(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(pid, seg, PRODUCT, address=5, name="A")
    with pytest.raises(ValueError, match="already used"):
        svc.add_device(pid, seg, PRODUCT, address=5, name="B")


def test_three_level_group_addresses(tmp_path: Path):
    svc, pid = _new(tmp_path)
    g1 = svc.create_group_address(pid, 0, 1, "Switching")  # main 0 / middle 0
    g2 = svc.create_group_address(pid, 0, 2, "Status")  # same ranges -> reused
    g3 = svc.create_group_address(
        pid, 0, 256, "Other"
    )  # main 0 / middle 1 -> new middle
    assert [g.id for g in svc.group_addresses(pid)] == [g1, g2, g3]

    mains = [r for r in svc.topology(pid, 0).group_ranges if r.parent_id is None]
    assert len(mains) == 1
    assert mains[0].name == "Main group 0"  # auto-generated from the address
    middles = mains[0].children
    assert len(middles) == 2
    assert sorted(len(m.group_addresses) for m in middles) == [1, 2]

    # undoing the GA that created the second middle also removes that middle
    svc.undo(pid)
    main = next(r for r in svc.topology(pid, 0).group_ranges if r.parent_id is None)
    assert len(main.children) == 1


def test_add_undo_redo(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)

    dev = svc.add_device(pid, seg, PRODUCT, address=5, name="Switch")
    svc.set_parameter(pid, dev, "P-1", "42")
    assert [d.id for d in svc.devices(pid)] == [dev]

    assert svc.undo(pid)  # undo set_parameter
    assert svc.undo(pid)  # undo add_device
    assert svc.devices(pid) == []

    assert svc.redo(pid)  # redo add_device
    assert [d.id for d in svc.devices(pid)] == [dev]
    assert svc.redo(pid)  # redo set_parameter
    assert svc.redo(pid) is False


def test_sync_device_com_objects_add_remove_keep_undo(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(
        pid,
        seg,
        PRODUCT,
        address=1,
        name="D",
        com_objects=[("O-1_R-1", None), ("O-2_R-1", None)],
    )
    ga = svc.create_group_address(pid, 0, 1, "GA")

    def cos() -> dict[str, int]:
        return {c.ref_id: c.id for c in svc.devices(pid)[0].com_objects}

    co1 = cos()["O-1_R-1"]
    co2 = cos()["O-2_R-1"]
    link = svc.link_com_object(pid, co1, ga)  # O-1 gets a link (will be removed)
    svc.set_com_object_flag(pid, co2, "read_flag", True)  # O-2 keeps a flag override

    # sync to a new should-exist set: drop O-1, keep O-2, add O-3
    svc.sync_device_com_objects(pid, dev, [("O-2_R-1", None), ("O-3_R-1", None)])
    assert set(cos()) == {"O-2_R-1", "O-3_R-1"}
    assert cos()["O-2_R-1"] == co2  # survivor kept its identity
    o2 = next(c for c in svc.devices(pid)[0].com_objects if c.ref_id == "O-2_R-1")
    assert o2.read_flag is True  # flag preserved
    assert svc.group_address_links(pid, ga) == []  # O-1's link gone with it

    # undo restores O-1 (with its link) and drops O-3; O-2 unchanged
    svc.undo(pid)
    assert set(cos()) == {"O-1_R-1", "O-2_R-1"}
    assert cos()["O-1_R-1"] == co1  # original id restored
    restored = svc.group_address_links(pid, ga)
    assert len(restored) == 1 and restored[0].id == link

    # redo re-applies the sync
    svc.redo(pid)
    assert set(cos()) == {"O-2_R-1", "O-3_R-1"}


def test_sync_device_com_objects_preserves_text_override_on_undo(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )

    def o1() -> ComObject:
        return next(c for c in svc.devices(pid)[0].com_objects if c.ref_id == "O-1_R-1")

    # Pin a per-instance text override on the row (import-only; no editor setter).
    session = svc._state(pid).session
    row = session.get(ComObject, o1().id)
    assert row is not None
    row.text_override = "Kitchen"
    row.function_text_override = "Switch"
    row.description_override = "note"
    session.commit()

    # Sync drops O-1 (not in the target), then undo must restore it with its overrides intact.
    svc.sync_device_com_objects(pid, dev, [("O-2_R-1", None)])
    assert {c.ref_id for c in svc.devices(pid)[0].com_objects} == {"O-2_R-1"}
    svc.undo(pid)
    restored = o1()
    assert (
        restored.text_override,
        restored.function_text_override,
        restored.description_override,
    ) == (
        "Kitchen",
        "Switch",
        "note",
    )


def test_set_parameter_and_sync_com_objects_is_one_undo_step(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(
        pid,
        seg,
        PRODUCT,
        address=1,
        name="D",
        com_objects=[("O-1_R-1", None), ("O-2_R-1", None)],
    )

    def state() -> tuple[str | None, set[str]]:
        d = svc.devices(pid)[0]
        value = next((p.value for p in d.parameters if p.ref_id == "P-1"), None)
        return value, {c.ref_id for c in d.com_objects}

    # A function change: set P-1 AND reconcile objects (drop O-1, keep O-2, add O-3) as ONE event.
    svc.set_parameter_and_sync_com_objects(
        pid, dev, "P-1", "1", [("O-2_R-1", None), ("O-3_R-1", None)]
    )
    assert state() == ("1", {"O-2_R-1", "O-3_R-1"})
    assert svc.peek_undo(pid)[0] == "Composite"  # type: ignore[index]

    # ONE undo reverts BOTH the parameter and the com-object set.
    assert svc.undo(pid)
    assert state() == (None, {"O-1_R-1", "O-2_R-1"})

    # ONE redo re-applies both.
    assert svc.redo(pid)
    assert state() == ("1", {"O-2_R-1", "O-3_R-1"})


def test_set_parameters_is_one_labelled_undo_step(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=1, name="D")
    svc.set_parameters(
        pid, dev, [("P-1", "1"), ("P-2", "2")], label="Button B executed."
    )

    def values() -> dict[str, str]:
        return {p.ref_id: p.value for p in svc.devices(pid)[0].parameters}

    assert values() == {"P-1": "1", "P-2": "2"}
    peek = svc.peek_undo(pid)
    assert peek is not None and peek[0] == "Composite"
    assert peek[1]["label"] == "Button B executed."
    assert svc.undo(pid)
    assert values() == {}
    svc.set_parameters(pid, dev, [("P-1", "3")])
    assert svc.peek_undo(pid)[0] == "SetParameter"  # type: ignore[index]


def test_set_parameters_adds_new_module_instances(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(
        pid,
        seg,
        PRODUCT,
        address=1,
        name="D",
        module_instances=[("MD-1_M-1_MI-1", "MD-1")],
    )

    def instances() -> list[tuple[str, int]]:
        return sorted(
            (m.instance_id, m.id) for m in svc.devices(pid)[0].module_instances
        )

    (first,) = instances()
    svc.set_parameters(
        pid,
        dev,
        [("MD-1_M-2_MI-1_P-1_R-1", "5")],
        module_instances=[("MD-1_M-1_MI-1", "MD-1"), ("MD-1_M-2_MI-1", "MD-1")],
    )
    added = instances()
    assert [i for i, _ in added] == ["MD-1_M-1_MI-1", "MD-1_M-2_MI-1"]
    assert added[0] == first  # the saved row stays as it is
    assert svc.undo(pid)
    assert instances() == [first]
    assert svc.redo(pid)
    assert instances() == added  # with the same row ids


def test_set_parameters_removes_ended_module_instances(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(
        pid,
        seg,
        PRODUCT,
        address=1,
        name="D",
        module_instances=[
            ("MD-1_M-1_MI-1", "MD-1"),
            ("MD-1_M-2_MI-1", "MD-1"),
            ("MD-1_M-3_MI-1", "MD-1"),
        ],
    )
    # An imported instance carries its arguments: it is never removed.
    session = svc._state(pid).session
    imported = next(
        m
        for m in svc.devices(pid)[0].module_instances
        if m.instance_id == "MD-1_M-3_MI-1"
    )
    session.get(type(imported), imported.id).arguments = [  # type: ignore[union-attr]
        {"ref_id": "MD-1_A-1", "value": "5"}
    ]
    session.commit()

    def instances() -> list[tuple[str, int]]:
        return sorted(
            (m.instance_id, m.id) for m in svc.devices(pid)[0].module_instances
        )

    before = instances()
    svc.set_parameters(
        pid,
        dev,
        [("P-1", "0")],
        removed_module_instances=["MD-1_M-2_MI-1", "MD-1_M-3_MI-1"],
    )
    assert [i for i, _ in instances()] == ["MD-1_M-1_MI-1", "MD-1_M-3_MI-1"]
    assert svc.undo(pid)
    assert instances() == before  # back with the same row id
    assert svc.redo(pid)
    assert [i for i, _ in instances()] == ["MD-1_M-1_MI-1", "MD-1_M-3_MI-1"]


def test_peek_undo_redo_reports_next_event(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=1, name="D")
    svc.set_parameter(pid, dev, "P-1", "7")
    # the next undo would revert the SetParameter, carrying device_id/ref_id/old_value
    peek = svc.peek_undo(pid)
    assert peek is not None and peek[0] == "SetParameter"
    assert peek[1]["ref_id"] == "P-1" and peek[1]["value"] == "7"
    assert svc.peek_redo(pid) is None  # nothing reverted yet
    svc.undo(pid)
    redo = svc.peek_redo(pid)
    assert redo is not None and redo[0] == "SetParameter" and redo[1]["value"] == "7"


def test_set_parameter_undo_restores_old_value(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=1, name="D")
    svc.set_parameter(pid, dev, "P-1", "1")
    svc.set_parameter(pid, dev, "P-1", "2")

    def value() -> str:
        return svc.devices(pid)[0].parameters[0].value

    assert value() == "2"
    svc.undo(pid)
    assert value() == "1"  # restored, not deleted
    svc.undo(pid)  # undo the create -> parameter row gone
    assert svc.devices(pid)[0].parameters == []


def test_link_com_object(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    ga = svc.create_group_address(pid, 0, 1, "GA")
    co = svc.devices(pid)[0].com_objects[0]
    link = svc.link_com_object(pid, co.id, ga)
    assert [link_.group_address_id for link_ in co.links] == [ga]
    svc.undo(pid)
    assert co.links == []
    assert link is not None


def test_link_derives_datapoint_type_as_single_undo(tmp_path: Path):
    # ETS types a group address from the object linked to it; the link and that typing undo together.
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    ga = svc.create_group_address(pid, 0, 1, "GA")
    co = svc.devices(pid)[0].com_objects[0]
    svc.link_com_object(pid, co.id, ga, derive_datapoint_type="1.001")
    assert svc.group_address(pid, ga).datapoint_type == "DPST-1-1"

    svc.undo(pid)  # one step reverts both the link and the derived type
    assert svc.com_object_links(pid, co.id) == []
    assert svc.group_address(pid, ga).datapoint_type is None


def test_device_links_carry_the_device_and_stored_flags(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    device = svc.devices(pid)[0]
    co = device.com_objects[0]
    g1 = svc.create_group_address(pid, 0, 1, "G1")
    g2 = svc.create_group_address(pid, 0, 2, "G2")
    svc.link_com_object(pid, co.id, g1, sending=True)
    svc.link_com_object(pid, co.id, g2)
    # Not set: left to the application, which the core does not know.
    assert {
        (ln.communication, ln.read, ln.transmit) for ln in svc.device_links(pid)
    } == {(None, None, None)}
    for flag in ("communication_flag", "transmit_flag", "write_flag"):
        svc.set_com_object_flag(pid, co.id, flag, True)
    svc.set_com_object_flag(pid, co.id, "read_flag", False)

    links = svc.device_links(pid)
    assert [
        (ln.device_id, ln.group_address_id, ln.is_sending, ln.com_object_id)
        for ln in links
    ] == [
        (device.id, g1, True, co.id),
        (device.id, g2, False, co.id),
    ]
    assert all(
        ln.communication and ln.transmit and ln.write and ln.read is False
        for ln in links
    )
    assert {ln.update for ln in links} == {None}


def test_link_sending_first_then_reassign(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    co = svc.devices(pid)[0].com_objects[0]
    g1 = svc.create_group_address(pid, 0, 1, "G1")
    g2 = svc.create_group_address(pid, 0, 2, "G2")
    l1 = svc.link_com_object(pid, co.id, g1, sending=True)
    l2 = svc.link_com_object(pid, co.id, g2)  # receive-only by default

    assert {ln.id: ln.is_sending for ln in svc.com_object_links(pid, co.id)} == {
        l1: True,
        l2: False,
    }

    svc.set_com_object_sending(pid, l2)  # reassign -> exactly one sender
    assert {ln.id: ln.is_sending for ln in svc.com_object_links(pid, co.id)} == {
        l1: False,
        l2: True,
    }

    svc.undo(pid)  # back to l1 sending
    assert {ln.id: ln.is_sending for ln in svc.com_object_links(pid, co.id)} == {
        l1: True,
        l2: False,
    }

    # the GA side: every com-object assigned to g1, with the sending flag and com-object id
    [assignment] = svc.group_address_links(pid, g1)
    assert (assignment.id, assignment.com_object_id, assignment.is_sending) == (
        l1,
        co.id,
        True,
    )


def test_failed_append_leaves_session_usable(tmp_path: Path):
    """A mutation that fails inside apply (stale id -> FK violation) must not poison the session:
    the rollback keeps it usable, and the redo branch it would have truncated survives."""
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    co = svc.devices(pid)[0].com_objects[0]

    # Build a redo branch: create a GA, then undo it (it is now redoable).
    doomed = svc.create_group_address(pid, 0, 2, "Doomed")
    svc.undo(pid)
    assert svc.can_redo(pid)

    # Link to a non-existent GA -> FK violation while applying the event.
    with pytest.raises(Exception):  # noqa: B017 - any DB error, session must recover
        svc.link_com_object(pid, co.id, 999999)

    # The failed append rolled back its redo-branch truncation instead of poisoning the session.
    assert svc.can_redo(pid)
    assert svc.redo(
        pid
    )  # a session write succeeds -> session is not in pending-rollback state
    assert [g.id for g in svc.group_addresses(pid)] == [doomed]

    # And a fresh, valid link still works afterwards.
    link = svc.link_com_object(pid, co.id, doomed)
    assert link is not None
    assert [ln.group_address_id for ln in co.links] == [doomed]


def test_com_object_channel_id(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", "CH-1")]
    )
    assert svc.devices(pid)[0].com_objects[0].channel_id == "CH-1"


def test_undo_truncates_redo(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(pid, seg, PRODUCT, address=1, name="A")
    svc.undo(pid)
    d2 = svc.add_device(pid, seg, PRODUCT, address=2, name="B")
    assert not svc.redo(pid)
    assert [d.id for d in svc.devices(pid)] == [d2]


def test_redo_after_full_undo_does_not_resurrect_discarded_event(tmp_path: Path):
    # Regression: when every event is undone the cursor is 0; appending a new event must still
    # discard the reverted redo branch. Otherwise redo() resurrects the older discarded event
    # (lower id) instead of finding nothing.
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(pid, seg, PRODUCT, address=1, name="A")
    assert svc.undo(pid)  # cursor -> 0, A reverted
    d_b = svc.add_device(
        pid, seg, PRODUCT, address=2, name="B"
    )  # must discard A's branch
    assert svc.undo(pid)  # cursor -> 0 again, B reverted
    assert svc.redo(pid)  # redoes B, never the discarded A
    assert [d.id for d in svc.devices(pid)] == [d_b]
    assert not svc.redo(pid)  # nothing left; A was truly discarded


def test_save_and_reopen(tmp_path: Path):
    path = tmp_path / "project.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-0001")
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=5, name="Switch")
    svc.set_parameter(pid, dev, "P-1", "7")
    svc.create_group_address(pid, 0, 1, "GA")
    svc.close(pid)

    reopened = ProjectService()
    pid2 = reopened.open(path)
    assert pid2 == pid
    # live state is read straight from the tables (no replay)
    assert [d.name for d in reopened.devices(pid2)] == ["Switch"]
    assert [g.name for g in reopened.group_addresses(pid2)] == ["GA"]
    # the events history still drives undo across the reopen
    assert reopened.undo(pid2)  # undo create_group_address
    assert reopened.group_addresses(pid2) == []


def test_multiple_installations(tmp_path: Path):
    svc, pid = _new(tmp_path)
    assert [i.index for i in svc.installations(pid)] == [0]

    inst1 = svc.add_installation(pid, "Installation 1")
    assert inst1 == 1
    assert [i.index for i in svc.installations(pid)] == [0, 1]
    assert [a.address for a in svc.topology(pid, 1).areas] == [0]  # its own backbone

    svc.create_area(pid, 0, 2, "A in 0")
    svc.create_area(pid, 1, 7, "A in 1")
    assert [a.address for a in svc.topology(pid, 0).areas] == [0, 1, 2]
    assert [a.address for a in svc.topology(pid, 1).areas] == [0, 7]

    svc.create_group_address(pid, 0, 1, "GA in 0")
    svc.create_group_address(pid, 1, 2, "GA in 1")
    assert {g.name for g in svc.group_addresses(pid)} == {"GA in 0", "GA in 1"}

    while svc.undo(pid):
        pass
    assert [i.index for i in svc.installations(pid)] == [0]


def test_create_area_rejects_duplicate_address(tmp_path: Path):
    """The skeleton seeds Areas 0 and 1; re-creating either address must be refused so the topology
    never holds two areas with the same address (which would break coupler pass-through lookups)."""
    svc, pid = _new(tmp_path)
    with pytest.raises(ValueError, match="Area address 1 already used"):
        svc.create_area(pid, 0, 1, "dup")


def test_create_line_rejects_duplicate_address(tmp_path: Path):
    """Area 1 ships with lines 0 and 1; re-creating an existing line address must be refused."""
    svc, pid = _new(tmp_path)
    area_1 = next(a.id for a in svc.topology(pid, 0).areas if a.address == 1)
    with pytest.raises(ValueError, match="Line address 1 already used"):
        svc.create_line(pid, area_1, 1, "dup")


def test_remove_device_undo_restores_subtree(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(
        pid,
        seg,
        PRODUCT,
        address=1,
        name="D",
        parameters=[("P-1", "9")],
        com_objects=[("O-1_R-1", None)],
    )
    ga = svc.create_group_address(pid, 0, 1, "GA")
    co = svc.devices(pid)[0].com_objects[0]
    link = svc.link_com_object(pid, co.id, ga)

    svc.remove_device(pid, dev)
    assert svc.devices(pid) == []

    svc.undo(
        pid
    )  # device, its parameter, com-object and the link all come back with same ids
    restored = svc.devices(pid)[0]
    assert restored.id == dev
    assert [p.value for p in restored.parameters] == ["9"]
    assert restored.com_objects[0].id == co.id
    assert [link_.id for link_ in restored.com_objects[0].links] == [link]


def test_add_and_remove_segment(tmp_path: Path):
    svc, pid = _new(tmp_path)
    line_id = svc.topology(pid, 0).areas[0].lines[0].id
    seg2 = svc.add_segment(pid, line_id, name="TP segment")
    segments = svc.topology(pid, 0).areas[0].lines[0].segments
    assert [s.number for s in segments] == [0, 1]  # continues the backbone segment
    assert segments[1].id == seg2 and segments[1].medium_type == "MT-0"

    dev = svc.add_device(pid, seg2, PRODUCT, address=1, name="D")
    svc.remove_segment(pid, seg2)
    assert svc.devices(pid) == []  # the nested device went with the segment
    assert [s.number for s in svc.topology(pid, 0).areas[0].lines[0].segments] == [0]

    svc.undo(pid)  # restore the segment and its device
    assert [d.id for d in svc.devices(pid)] == [dev]
    assert seg2 in [s.id for s in svc.topology(pid, 0).areas[0].lines[0].segments]


def test_remove_area_cascades_and_undo(tmp_path: Path):
    svc, pid = _new(tmp_path)
    area = svc.create_area(pid, 0, 2, "Area")
    svc.create_line(pid, area, 0, "Line")
    seg = svc.topology(pid, 0).areas[2].lines[0].segments[0].id
    svc.add_device(pid, seg, PRODUCT, address=1, name="D")

    svc.remove_area(pid, area)
    assert [a.address for a in svc.topology(pid, 0).areas] == [
        0,
        1,
    ]  # seeded areas remain
    assert svc.devices(pid) == []  # the nested device went too

    svc.undo(pid)
    assert [a.address for a in svc.topology(pid, 0).areas] == [0, 1, 2]
    assert [d.name for d in svc.devices(pid)] == ["D"]


def test_rename_and_undo(tmp_path: Path):
    svc, pid = _new(tmp_path)
    area = svc.create_area(pid, 0, 2, "Area")
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=1, name="Old")

    svc.rename_area(pid, area, "Renamed")
    svc.set_device_name(pid, dev, "New")
    assert svc.topology(pid, 0).areas[2].name == "Renamed"
    assert svc.devices(pid)[0].name == "New"

    svc.undo(pid)  # device name
    svc.undo(pid)  # area name
    assert svc.topology(pid, 0).areas[2].name == "Area"
    assert svc.devices(pid)[0].name == "Old"


def test_set_device_description_and_undo(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=1, name="D")

    svc.set_device_description(pid, dev, "Feeds the workshop lights")
    assert svc.device(pid, dev).description == "Feeds the workshop lights"

    svc.undo(pid)
    assert svc.device(pid, dev).description == ""  # add_device leaves it empty
    svc.redo(pid)
    assert svc.device(pid, dev).description == "Feeds the workshop lights"


def test_move_device(tmp_path: Path):
    svc, pid = _new(tmp_path)
    backbone = _backbone_segment(svc, pid)
    area = svc.create_area(pid, 0, 2, "Area")
    svc.create_line(pid, area, 0, "Line")
    other = svc.topology(pid, 0).areas[1].lines[0].segments[0].id

    dev = svc.add_device(pid, backbone, PRODUCT, address=5, name="D")
    svc.move_device(pid, dev, other, 7)
    moved = svc.devices(pid)[0]
    assert moved.segment_id == other
    assert moved.address == 7

    svc.undo(pid)
    moved = svc.devices(pid)[0]
    assert moved.segment_id == backbone
    assert moved.address == 5


def test_move_device_rejects_duplicate_address(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(pid, seg, PRODUCT, address=1, name="A")
    b = svc.add_device(pid, seg, PRODUCT, address=2, name="B")
    with pytest.raises(ValueError, match="already used"):
        svc.move_device(pid, b, seg, 1)


def test_unlink_com_object(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    ga = svc.create_group_address(pid, 0, 1, "GA")
    co = svc.devices(pid)[0].com_objects[0]
    link = svc.link_com_object(pid, co.id, ga)

    svc.unlink_com_object(pid, link)
    assert svc.devices(pid)[0].com_objects[0].links == []
    svc.undo(pid)
    assert [link_.id for link_ in svc.devices(pid)[0].com_objects[0].links] == [link]


def test_history_and_jump_to(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=1, name="D")
    svc.set_parameter(pid, dev, "P-1", "1")
    svc.create_group_address(pid, 0, 1, "GA")

    entries = svc.history(pid)  # newest first
    assert [e.event_type for e in entries] == [
        "CreateGroupAddress",
        "SetParameter",
        "AddDevice",
    ]
    assert entries[-1].data["name"] == "D"  # payload is available for the UI to render
    assert all(not e.reverted for e in entries)
    assert svc.can_undo(pid) and not svc.can_redo(pid)

    # jump back to just after the device was added (undo the param + GA)
    add_device_event = entries[-1].id
    svc.jump_to(pid, add_device_event)
    assert svc.cursor(pid) == add_device_event
    assert svc.group_addresses(pid) == []
    assert svc.devices(pid)[0].parameters == []
    assert [e.reverted for e in svc.history(pid)] == [True, True, False]
    assert svc.can_redo(pid)


def test_ga_format_parse_round_trip():
    cases = {
        GroupAddressStyle.THREE_LEVEL: [
            (1, "0/0/1"),
            (2305, "1/1/1"),
            (0xFFFF, "31/7/255"),
        ],
        GroupAddressStyle.TWO_LEVEL: [(1, "0/1"), (2048, "1/0"), (0xFFFF, "31/2047")],
        GroupAddressStyle.FREE: [(1, "1"), (4609, "4609")],
    }
    for style, pairs in cases.items():
        for value, text in pairs:
            assert format_ga(value, style) == text
            assert parse_ga(text, style) == value


def test_ranges_for_styles():
    assert ranges_for(1, GroupAddressStyle.FREE) == [(1, 0xFFFF, "Group addresses")]
    assert ranges_for(2305, GroupAddressStyle.TWO_LEVEL) == [
        (2048, 4095, "Main group 1")
    ]
    assert ranges_for(2305, GroupAddressStyle.THREE_LEVEL) == [
        (2048, 4095, "Main group 1"),
        (2304, 2559, "Middle group 1/1"),
    ]
    # main 0 / middle 0 start at 1 because address 0 is reserved
    assert ranges_for(1, GroupAddressStyle.THREE_LEVEL) == [
        (1, 2047, "Main group 0"),
        (1, 255, "Middle group 0/0"),
    ]


def test_next_free_group_address(tmp_path: Path):
    svc, pid = _new(tmp_path)
    assert svc.next_free_group_address(pid, 0) == 1  # 0 reserved, nothing used yet
    svc.create_group_address(pid, 0, 1, "A")
    svc.create_group_address(pid, 0, 2, "B")
    assert svc.next_free_group_address(pid, 0) == 3  # skips used 1 and 2
    assert svc.next_free_group_address(pid, 0, start=2305) == 2305  # honours the hint


def test_group_address_info(tmp_path: Path):
    svc, pid = _new(tmp_path)  # ThreeLevel
    gid = svc.create_group_address(pid, 0, 2305, "Living room")
    info = svc.group_address(pid, gid)
    assert (info.id, info.address, info.text, info.name) == (
        gid,
        2305,
        "1/1/1",
        "Living room",
    )
    assert info.links == []
    assert svc.group_addresses(pid) == [info]  # the list read returns the same info


def test_free_style_creates_flat_ranges(tmp_path: Path):
    svc, pid = _new(tmp_path, style=GroupAddressStyle.FREE)
    assert svc.project(pid).group_address_style == GroupAddressStyle.FREE

    g1 = svc.create_group_address(pid, 0, 4609, "A")
    g2 = svc.create_group_address(pid, 0, 7, "B")
    ranges = svc.topology(pid, 0).group_ranges
    # one catch-all root range, no nesting, both GAs directly under it
    assert len(ranges) == 1
    assert ranges[0].parent_id is None and ranges[0].name == "Group addresses"
    assert ranges[0].children == []
    assert {ga.id for ga in ranges[0].group_addresses} == {g1, g2}


def test_set_com_object_flag_override(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    co = svc.devices(pid)[0].com_objects[0]
    assert co.communication_flag is None  # inherit from product by default

    svc.set_com_object_flag(pid, co.id, "communication_flag", True)
    assert svc.devices(pid)[0].com_objects[0].communication_flag is True
    svc.undo(pid)
    assert (
        svc.devices(pid)[0].com_objects[0].communication_flag is None
    )  # back to inherit

    with pytest.raises(ValueError, match="Unknown com-object flag"):
        svc.set_com_object_flag(pid, co.id, "bogus_flag", True)


def test_set_com_object_datapoint_type(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    co = svc.devices(pid)[0].com_objects[0]
    assert co.datapoint_type_override is None  # inherit the application/product default

    # Dotted code is normalized to ETS token form.
    svc.set_com_object_datapoint_type(pid, co.id, "5.001")
    assert svc.devices(pid)[0].com_objects[0].datapoint_type_override == "DPST-5-1"

    svc.set_com_object_datapoint_type(pid, co.id, None)  # clear -> inherit
    assert svc.devices(pid)[0].com_objects[0].datapoint_type_override is None

    svc.undo(pid)
    assert svc.devices(pid)[0].com_objects[0].datapoint_type_override == "DPST-5-1"
    svc.undo(pid)
    assert svc.devices(pid)[0].com_objects[0].datapoint_type_override is None


def test_set_com_object_priority(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    co = svc.devices(pid)[0].com_objects[0]
    assert co.priority is None

    svc.set_com_object_priority(pid, co.id, "Alert")
    assert svc.devices(pid)[0].com_objects[0].priority == "Alert"
    svc.set_com_object_priority(pid, co.id, None)
    assert svc.devices(pid)[0].com_objects[0].priority is None
    svc.undo(pid)
    assert svc.devices(pid)[0].com_objects[0].priority == "Alert"

    with pytest.raises(ValueError, match="Unknown com-object priority"):
        svc.set_com_object_priority(pid, co.id, "Bogus")


def test_set_com_object_description(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    svc.add_device(
        pid, seg, PRODUCT, address=1, name="D", com_objects=[("O-1_R-1", None)]
    )
    co = svc.devices(pid)[0].com_objects[0]
    assert co.description_override is None

    svc.set_com_object_description(pid, co.id, "free-text note")
    assert svc.devices(pid)[0].com_objects[0].description_override == "free-text note"
    svc.set_com_object_description(pid, co.id, "")  # empty clears
    assert svc.devices(pid)[0].com_objects[0].description_override is None
    svc.undo(pid)
    assert svc.devices(pid)[0].com_objects[0].description_override == "free-text note"


def test_group_address_datapoint_type(tmp_path: Path):
    svc, pid = _new(tmp_path)
    gid = svc.create_group_address(pid, 0, 1, "Switch")
    assert svc.group_address(pid, gid).datapoint_type is None

    svc.set_group_address_datapoint_type(pid, gid, "DPST-1-1")
    assert svc.group_address(pid, gid).datapoint_type == "DPST-1-1"
    svc.undo(pid)
    assert svc.group_address(pid, gid).datapoint_type is None


def test_rename_group_address(tmp_path: Path):
    svc, pid = _new(tmp_path)
    gid = svc.create_group_address(pid, 0, 1, "Switch")
    svc.rename_group_address(pid, gid, "Living room light")
    assert svc.group_address(pid, gid).name == "Living room light"
    svc.undo(pid)
    assert svc.group_address(pid, gid).name == "Switch"


def test_device_info_carries_refs(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(
        pid,
        seg,
        "M-0162_H-x-2_P-y",
        address=5,
        name="Switch",
        hardware2program_ref_id="M-0162_H-x-2_HP-0009-20-98C4",
    )
    info = svc.device(pid, dev)
    assert info.id == dev
    assert info.name == "Switch"
    assert info.individual_address == "0.0.5"
    assert info.product_ref_id == "M-0162_H-x-2_P-y"
    assert info.hardware2program_ref_id == "M-0162_H-x-2_HP-0009-20-98C4"
    assert (
        info.ip_config is None
    )  # no <IPConfig> unless imported from an IP router/interface


def test_device_info_exposes_ip_config(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=5, name="Router")
    # IPConfig is import-only provenance (no editor setter); pin it on the row directly.
    session = svc._state(pid).session
    row = session.get(Device, dev)
    assert row is not None
    row.ip_config = {
        "Assign": "Fixed",
        "IPAddress": "192.168.1.10",
        "SubnetMask": "255.255.255.0",
    }
    session.commit()
    assert svc.device(pid, dev).ip_config == {
        "Assign": "Fixed",
        "IPAddress": "192.168.1.10",
        "SubnetMask": "255.255.255.0",
    }


def test_ia_format_parse():
    assert format_ia(1, 1, 5) == "1.1.5"
    assert parse_ia("1.1.5") == (1, 1, 5)
    assert parse_ia("0.0.0") == (0, 0, 0)


def test_individual_address_compose(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)  # backbone is area 0 / line 0
    dev = svc.add_device(pid, seg, PRODUCT, address=5, name="D")
    assert svc.individual_address(pid, dev) == "0.0.5"

    unassigned = svc.add_device(pid, seg, PRODUCT, name="U")  # no octet
    assert svc.individual_address(pid, unassigned) is None


def test_next_free_individual_address(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    line_id = svc.topology(pid, 0).areas[0].lines[0].id
    assert svc.next_free_individual_address(pid, line_id) == 1
    svc.add_device(pid, seg, PRODUCT, address=1, name="A")
    svc.add_device(pid, seg, PRODUCT, address=2, name="B")
    assert svc.next_free_individual_address(pid, line_id) == 3
    # same result resolving via the segment (the add flow's entry point)
    assert svc.next_free_individual_address_for_segment(pid, seg) == 3


def test_set_individual_address_moves_to_line(tmp_path: Path):
    svc, pid = _new(tmp_path)
    backbone = _backbone_segment(svc, pid)
    # the seeded 1.1 TP line is where a device moved to "1.1.x" should land
    other = svc.topology(pid, 0).areas[1].lines[1].segments[0].id

    dev = svc.add_device(pid, backbone, PRODUCT, address=9, name="D")
    svc.set_individual_address(pid, dev, "1.1.5")  # move onto area 1 / line 1, octet 5
    assert svc.devices(pid)[0].segment_id == other
    assert svc.individual_address(pid, dev) == "1.1.5"

    # undo returns it to the backbone at 0.0.9
    svc.undo(pid)
    assert svc.individual_address(pid, dev) == "0.0.9"


def test_set_individual_address_unknown_line(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(pid, seg, PRODUCT, address=1, name="D")
    with pytest.raises(KeyError, match=r"No line 3\.4"):
        svc.set_individual_address(pid, dev, "3.4.5")


def test_multiple_concurrent_projects(tmp_path: Path):
    svc = ProjectService()
    a = svc.create(tmp_path / "a.xknxproj", "P-000A")
    b = svc.create(tmp_path / "b.xknxproj", "P-000B")
    dev = svc.add_device(
        svc.list()[0], _backbone_segment(svc, a), PRODUCT, address=1, name="A"
    )
    assert [d.id for d in svc.devices(a)] == [dev]
    assert svc.devices(b) == []  # isolated
    assert set(svc.list()) == {a, b}


def test_add_device_stores_product_fields(tmp_path: Path):
    svc, pid = _new(tmp_path)
    seg = _backbone_segment(svc, pid)
    dev = svc.add_device(
        pid,
        seg,
        PRODUCT,
        address=1,
        product_name="Switch actuator",
        hardware_name="SA 4-fold",
        order_number="SA-04",
        manufacturer_name="Acme",
    )
    svc.close(pid)
    pid = svc.open(tmp_path / "p.xknx")
    info = svc.device(pid, dev)
    assert (info.name, info.product_name, info.hardware_name) == (
        "",
        "Switch actuator",
        "SA 4-fold",
    )
    assert (info.order_number, info.manufacturer_name) == ("SA-04", "Acme")


def test_add_device_event_without_product_fields_loads():
    from xknxeditor.proj.core.events import AddDevice

    event = AddDevice.from_dict(
        {
            "segment_id": 1,
            "address": 2,
            "name": "Old",
            "product_ref_id": PRODUCT,
            "hardware2program_ref_id": None,
        }
    )
    assert event.product_name == ""
    assert event.to_dict()["manufacturer_name"] == ""

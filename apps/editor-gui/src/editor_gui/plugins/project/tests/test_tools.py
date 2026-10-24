"""Tests for the project Tools: pure helpers + the shift/copy orchestration on ProjectPlugin."""

from __future__ import annotations

from types import SimpleNamespace

from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.ui.tools import (
    apply_name_swap,
    match_by_number,
    shifted_ga,
    shifted_ia,
    topology_findings,
)
from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga


def _co(db_id, number, size="1 Bit", name="co"):
    return SimpleNamespace(db_id=db_id, number=number, object_size=size, name=name)


# --- pure helpers ---------------------------------------------------------


def test_apply_name_swap():
    assert apply_name_swap("Room 1 switch", "Room 1", "Room 2") == "Room 2 switch"
    assert apply_name_swap("keep me", "", "x") == "keep me"  # empty find = no change


def test_shifted_ia():
    assert shifted_ia("1.1.5", 3) == "1.1.8"
    assert shifted_ia("1.1.5", -4) == "1.1.1"
    assert shifted_ia("1.1.1", -1) is None  # would hit 0 (reserved)
    assert shifted_ia("1.1.255", 1) is None  # out of range
    assert shifted_ia("1.1", 1) is None  # malformed
    assert shifted_ia("a.b.c", 1) is None
    assert shifted_ia("16.1.5", 1) is None  # area out of range
    assert shifted_ia("1.16.5", 1) is None  # line out of range


def test_shifted_ga():
    three = GroupAddressStyle.THREE_LEVEL
    assert shifted_ga((1 << 11) | 1, 2, three) == "1/0/3"
    assert shifted_ga(1, -1, three) is None  # would hit 0 (reserved)
    assert shifted_ga(0xFFFF, 1, three) is None  # out of range
    # The offset is on the raw value, so it can cross a sub/middle-group boundary.
    assert shifted_ga((1 << 11) | 255, 1, three) == "1/1/0"
    assert shifted_ga(5, 3, GroupAddressStyle.FREE) == "8"


def test_topology_findings():
    found = topology_findings(
        [
            (1, "A", "1.1.1"),
            (2, "B", "1.1.1"),  # duplicate
            (3, "C", ""),  # missing
            (4, "D", "1.1"),  # malformed
            (5, "E", "1.1.2"),  # ok
        ]
    )
    by_node = {n: (sev, msg) for n, sev, msg in found}
    assert by_node[2][0] == "error" and "duplicate" in by_node[2][1]
    assert by_node[3][0] == "warning"
    assert by_node[4][0] == "error"
    assert 1 not in by_node and 5 not in by_node  # valid + first occurrence: no finding


# --- orchestration --------------------------------------------------------


class FakeLog:
    def info(self, *a, **k):
        pass


def _plugin(project) -> ProjectPlugin:
    p = ProjectPlugin.__new__(ProjectPlugin)
    p._api = SimpleNamespace(project=project, log=FakeLog())  # type: ignore[attr-defined]
    return p


class FakeShiftProject:
    def __init__(self, devices):
        self._devices = devices
        self.applied: list[tuple[int, str, str]] = []

    @property
    def devices(self):
        return self._devices

    def set_device_individual_address(self, node_id, old, new):
        self.applied.append((node_id, old, new))
        return True


def test_shift_orders_descending_for_positive_offset():
    # 1.1.1 / 1.1.2 / 1.1.3 shifted +1 must be applied 3->2->1 so no transient collision.
    devs = [
        SimpleNamespace(node_id=i, name=f"d{i}", individual_address=f"1.1.{i}")
        for i in (1, 2, 3)
    ]
    proj = FakeShiftProject(devs)
    changed, errors = _plugin(proj).tools_shift_addresses([1, 2, 3], 1)
    assert changed == 3
    assert errors == []
    assert [nid for nid, _o, _n in proj.applied] == [3, 2, 1]
    assert [new for _i, _o, new in proj.applied] == ["1.1.4", "1.1.3", "1.1.2"]


def test_shift_reports_out_of_range():
    devs = [SimpleNamespace(node_id=1, name="d", individual_address="1.1.255")]
    proj = FakeShiftProject(devs)
    changed, errors = _plugin(proj).tools_shift_addresses([1], 1)
    assert changed == 0
    assert len(errors) == 1
    assert proj.applied == []


def test_shift_aborts_on_collision_with_unselected_device():
    # Shifting 1.1.1 by +1 lands on 1.1.2, which another (unselected) device already occupies.
    devs = [
        SimpleNamespace(node_id=1, name="a", individual_address="1.1.1"),
        SimpleNamespace(node_id=2, name="b", individual_address="1.1.2"),
    ]
    proj = FakeShiftProject(devs)
    changed, errors = _plugin(proj).tools_shift_addresses([1], 1)
    assert changed == 0
    assert any("collide" in e for e in errors)
    assert proj.applied == []  # nothing written (all-or-nothing)


class FakeShiftGaProject:
    def __init__(self, gas, style=GroupAddressStyle.THREE_LEVEL):
        self._gas = gas
        self.group_address_style = style
        self.applied: list[tuple[int, str]] = []

    @property
    def group_addresses(self):
        return self._gas

    def set_group_address_address(self, ga_id, address):
        self.applied.append((ga_id, address))
        return True


def _ga(gid, raw):
    three = GroupAddressStyle.THREE_LEVEL
    return SimpleNamespace(
        id=gid, raw=raw, address=format_ga(raw, three), name=f"g{gid}"
    )


def test_shift_ga_orders_descending_for_positive_offset():
    # 1/0/1 .. 1/0/3 shifted +1 must be applied high->low so no transient collision.
    base = 1 << 11
    gas = [_ga(1, base + 1), _ga(2, base + 2), _ga(3, base + 3)]
    proj = FakeShiftGaProject(gas)
    changed, errors = _plugin(proj).tools_shift_group_addresses([1, 2, 3], 1)
    assert changed == 3
    assert errors == []
    assert [gid for gid, _a in proj.applied] == [3, 2, 1]
    assert [a for _g, a in proj.applied] == ["1/0/4", "1/0/3", "1/0/2"]


def test_shift_ga_reports_out_of_range():
    gas = [_ga(1, 0xFFFF)]
    proj = FakeShiftGaProject(gas)
    changed, errors = _plugin(proj).tools_shift_group_addresses([1], 1)
    assert changed == 0
    assert len(errors) == 1
    assert proj.applied == []


def test_shift_ga_aborts_on_collision_with_unselected():
    base = 1 << 11
    gas = [_ga(1, base + 1), _ga(2, base + 2)]  # only ga 1 is selected
    proj = FakeShiftGaProject(gas)
    changed, errors = _plugin(proj).tools_shift_group_addresses([1], 1)
    assert changed == 0
    assert any("collide" in e for e in errors)
    assert proj.applied == []


class FakeCopyProject:
    def __init__(self):
        self.names: dict[int, str] = {1: "Src"}
        self.renamed: list[tuple[int, str, str]] = []
        self._next = 100

    def clone_device(self, node_id, count):
        ids = []
        for i in range(count):
            self._next += 1
            suffix = " (copy)" if i == 0 else f" (copy {i + 1})"
            self.names[self._next] = f"{self.names[node_id]}{suffix}"
            ids.append(self._next)
        return ids

    def find_device_by_node_id(self, nid):
        if nid not in self.names:
            return None
        return SimpleNamespace(
            node_id=nid,
            name=self.names[nid],
            get_visible_com_objects=lambda: [],
        )

    def set_device_name(self, nid, old, new):
        self.renamed.append((nid, old, new))
        self.names[nid] = new


def test_match_by_number():
    old = [_co(1, 1, "1 Bit"), _co(2, 2, "1 Byte")]
    new = [_co(9, 1, "1 Bit", "x"), _co(8, 2, "4 Byte", "y")]  # #2 size mismatch
    pairs = match_by_number(old, new)
    assert pairs[0][1] is not None and pairs[0][1].name == "x"
    assert pairs[1][1] is None  # size mismatch -> no match
    # number absent in replacement
    assert match_by_number([_co(1, 9)], new)[0][1] is None


class FakeReplaceProject:
    def __init__(self):
        self.target = SimpleNamespace(
            node_id=1,
            name="Old",
            individual_address="1.1.1",
            get_visible_com_objects=lambda: [_co(1, 1, "1 Bit", "on/off")],
        )
        self.template = SimpleNamespace(
            node_id=2,
            name="Tmpl",
            individual_address="1.1.2",
            get_visible_com_objects=lambda: [_co(2, 1, "1 Bit", "switch")],
        )
        self.new_dev = SimpleNamespace(
            node_id=100,
            name="Tmpl (copy)",
            individual_address="",
            get_visible_com_objects=lambda: [_co(3, 1, "1 Bit", "switch")],
        )
        self.links = {1: [SimpleNamespace(group_address_id=10, is_sending=True)]}
        self.linked: list[tuple[int, int, bool]] = []
        self.removed: list[int] = []
        self.ia_set: list[tuple[int, str, str]] = []

    def find_device_by_node_id(self, nid):
        return {1: self.target, 2: self.template, 100: self.new_dev}.get(nid)

    def get_links_for_com_object(self, db_id):
        return self.links.get(db_id, [])

    def clone_device(self, node_id, count=1):
        return [100]

    def set_device_name(self, nid, old, new):
        if nid == 100:
            self.new_dev.name = new

    def remove_device(self, nid):
        self.removed.append(nid)

    def set_device_individual_address(self, nid, old, new):
        self.ia_set.append((nid, old, new))
        return True

    def link_com_object_to_ga(self, co, ga, is_sending=False):
        self.linked.append((co, ga, is_sending))
        return len(self.linked)


def test_replace_device_keeps_links_and_reuses_address():
    proj = FakeReplaceProject()
    mapped, errors = _plugin(proj).tools_replace_device(1, 2)
    assert mapped == 1
    assert errors == []
    assert proj.removed == [1]  # old device gone
    assert proj.ia_set == [(100, "", "1.1.1")]  # replacement took the target's address
    assert (
        3,
        10,
        True,
    ) in proj.linked  # GA 10 re-attached to the new object, still sending


def test_extended_copy_renames_each_copy():
    proj = FakeCopyProject()
    created, errors = _plugin(proj).tools_extended_copy(1, 2, "Src", "Copy", False)
    assert created == 2
    assert errors == []
    # The swap is applied to each clone's own (unique) name, so the copies stay distinct
    # instead of all collapsing to the same name.
    new_names = [new for _n, _o, new in proj.renamed]
    assert new_names == ["Copy (copy)", "Copy (copy 2)"]
    assert len(set(new_names)) == 2

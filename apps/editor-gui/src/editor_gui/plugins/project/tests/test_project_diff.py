"""Pure project-diff engine tests (no imgui, no database)."""

from __future__ import annotations

from editor_gui.plugins.project.ui.project_diff import (
    DeviceSnap,
    GaSnap,
    ProjectSnapshot,
    diff_projects,
)


def _snap(name, devices, gas):
    return ProjectSnapshot(
        name=name,
        devices={d.ia: d for d in devices},
        group_addresses={g.address: g for g in gas},
    )


def test_added_removed_and_changed_devices():
    base = _snap(
        "A",
        [
            DeviceSnap("1.1.1", "Switch", "Prod", "ORD1"),
            DeviceSnap("1.1.2", "Dimmer", "Prod", "ORD2"),
        ],
        [],
    )
    other = _snap(
        "B",
        [
            DeviceSnap("1.1.1", "Switch renamed", "Prod", "ORD1"),  # changed name
            DeviceSnap("1.1.3", "New", "Prod", "ORD3"),  # added
        ],
        [],
    )
    result = diff_projects(base, other)
    kinds = {e.key: e.kind for e in result.devices}
    assert kinds == {"1.1.1": "changed", "1.1.2": "removed", "1.1.3": "added"}
    changed = next(e for e in result.devices if e.key == "1.1.1")
    assert "name" in changed.detail and "Switch renamed" in changed.detail


def test_group_address_diff_name_and_dpt():
    base = _snap("A", [], [GaSnap("1/0/1", "Light", "DPST-1-1")])
    other = _snap("B", [], [GaSnap("1/0/1", "Light", "DPST-5-1")])  # DPT changed
    result = diff_projects(base, other)
    assert len(result.group_addresses) == 1
    entry = result.group_addresses[0]
    assert entry.kind == "changed"
    assert "DPT" in entry.detail


def test_identical_projects_have_no_differences():
    gas = [GaSnap("1/0/1", "Light", "DPST-1-1")]
    devs = [DeviceSnap("1.1.1", "Switch", "Prod", "ORD1")]
    result = diff_projects(_snap("A", devs, gas), _snap("B", devs, gas))
    assert result.total == 0


def test_entries_sorted_by_key():
    base = _snap("A", [], [])
    other = _snap(
        "B",
        [],
        [GaSnap("1/0/3", "c", ""), GaSnap("1/0/1", "a", ""), GaSnap("1/0/2", "b", "")],
    )
    result = diff_projects(base, other)
    assert [e.key for e in result.group_addresses] == ["1/0/1", "1/0/2", "1/0/3"]

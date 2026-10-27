"""Pure project-diff engine tests (no imgui, no database)."""

from __future__ import annotations

from editor_gui.plugins.project.ui.project_diff import (
    HIDDEN_SETTINGS,
    DetailRow,
    DeviceSnap,
    GaSnap,
    ProjectSnapshot,
    diff_projects,
    match_devices,
)


def _snap(name, devices, gas, project_id=""):
    return ProjectSnapshot(
        name=name,
        devices=tuple(devices),
        group_addresses={g.address: g for g in gas},
        project_id=project_id,
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


def test_a_device_with_another_address_is_one_changed_device():
    base = _snap("A", [DeviceSnap("1.1.5", "Switch", "P", "O", source_id="DI-1")], [])
    other = _snap("B", [DeviceSnap("1.1.7", "Switch", "P", "O", source_id="DI-1")], [])
    (entry,) = diff_projects(base, other).devices
    # Named by its address in the open project.
    assert (entry.key, entry.kind) == ("1.1.5", "changed")
    assert entry.detail == "address '1.1.5' -> '1.1.7'"


def test_devices_added_in_the_editor_match_by_id_within_one_project():
    base = [DeviceSnap("1.1.5", "Switch", "P", "O", id=3)]
    other = [DeviceSnap("1.1.7", "Switch", "P", "O", id=3)]
    assert match_devices(base, other, same_project=True) == [(base[0], other[0])]
    # Ids of two different projects say nothing.
    assert match_devices(base, other) == [(base[0], None), (None, other[0])]
    result = diff_projects(_snap("A", base, [], "P-1"), _snap("B", other, [], "P-1"))
    assert [e.kind for e in result.devices] == ["changed"]


def test_an_imported_device_is_not_matched_by_the_id_of_another():
    base = [DeviceSnap("1.1.5", "A", "P", "O", id=3, source_id="DI-1")]
    other = [DeviceSnap("1.1.9", "B", "P", "O", id=3, source_id="DI-2")]
    pairs = match_devices(base, other, same_project=True)
    assert pairs == [(base[0], None), (None, other[0])]


def test_an_address_two_devices_share_matches_neither():
    base = [DeviceSnap("1.1.5", "A", "P", "O"), DeviceSnap("1.1.5", "B", "P", "O")]
    other = [DeviceSnap("1.1.5", "A", "P", "O")]
    pairs = match_devices(base, other)
    assert (None, other[0]) in pairs and len(pairs) == 3


def test_location_and_application_changes_are_named():
    base = _snap(
        "A",
        [
            DeviceSnap(
                "1.1.1", "S", "P", "O", application="A-1", location="House > Hall"
            )
        ],
        [],
    )
    other = _snap(
        "B",
        [
            DeviceSnap(
                "1.1.1", "S", "P", "O", application="A-2", location="House > Room"
            )
        ],
        [],
    )
    (entry,) = diff_projects(base, other).devices
    assert entry.detail == (
        "application 'A-1' -> 'A-2', location 'House > Hall' -> 'House > Room'"
    )


def test_parameters_and_group_objects_of_a_device_whose_settings_differ():
    rows = [DetailRow("parameter", "Mode", "changed", "On", "Off")]
    asked: list[tuple[str, str]] = []

    def details(b: DeviceSnap, o: DeviceSnap) -> list[DetailRow]:
        asked.append((b.inputs, o.inputs))
        return rows

    same = DeviceSnap("1.1.2", "S", "P", "O", inputs="x")
    base = _snap("A", [DeviceSnap("1.1.1", "S", "P", "O", inputs="a"), same], [])
    other = _snap("B", [DeviceSnap("1.1.1", "S", "P", "O", inputs="b"), same], [])
    (entry,) = diff_projects(base, other, details).devices
    assert (entry.key, entry.detail, entry.rows) == ("1.1.1", "", tuple(rows))
    # Only a device whose settings differ is compared in detail.
    assert asked == [("a", "b")]


def test_progress_counts_the_devices_compared_in_detail():
    calls: list[tuple[int, int]] = []
    base = _snap(
        "A",
        [
            DeviceSnap("1.1.1", "S", "P", "O", inputs="a"),
            DeviceSnap("1.1.2", "S", "P", "O", inputs="x"),
            DeviceSnap("1.1.3", "S", "P", "O", inputs="c"),
        ],
        [],
    )
    other = _snap(
        "B",
        [
            DeviceSnap("1.1.1", "S", "P", "O", inputs="b"),
            DeviceSnap("1.1.2", "S", "P", "O", inputs="x"),
            DeviceSnap("1.1.3", "S", "P", "O", inputs="d"),
        ],
        [],
    )
    diff_projects(
        base, other, lambda _b, _o: [], lambda done, total: calls.append((done, total))
    )
    # The unchanged device is not counted.
    assert calls == [(0, 2), (1, 2), (2, 2)]


def test_a_comparison_can_be_stopped():
    import pytest

    from editor_gui.plugins.project.ui.project_diff import DiffCanceled, DiffJob

    job = DiffJob()

    def details(_b: DeviceSnap, _o: DeviceSnap) -> list[DetailRow]:
        if job.cancel_requested:
            raise DiffCanceled
        return []

    job.cancel()
    base = _snap("A", [DeviceSnap("1.1.1", "S", "P", "O", inputs="a")], [])
    other = _snap("B", [DeviceSnap("1.1.1", "S", "P", "O", inputs="b")], [])
    with pytest.raises(DiffCanceled):
        diff_projects(base, other, details, job.progress)
    assert (job.done, job.total) == (0, 1)


def test_settings_that_differ_where_nothing_shows_them_are_named():
    base = _snap("A", [DeviceSnap("1.1.1", "S", "P", "O", inputs="a")], [])
    other = _snap("B", [DeviceSnap("1.1.1", "S", "P", "O", inputs="b")], [])
    (entry,) = diff_projects(base, other, lambda _b, _o: []).devices
    assert (entry.kind, entry.detail) == ("changed", HIDDEN_SETTINGS)


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
    devs = [DeviceSnap("1.1.1", "Switch", "Prod", "ORD1", inputs="x")]
    result = diff_projects(_snap("A", devs, gas), _snap("B", devs, gas))
    assert result.total == 0


def test_entries_sorted_by_key():
    base = _snap("A", [], [])
    other = _snap(
        "B",
        [DeviceSnap("1.1.3", "c", "", ""), DeviceSnap("1.1.1", "a", "", "")],
        [GaSnap("1/0/3", "c", ""), GaSnap("1/0/1", "a", ""), GaSnap("1/0/2", "b", "")],
    )
    result = diff_projects(base, other)
    assert [e.key for e in result.group_addresses] == ["1/0/1", "1/0/2", "1/0/3"]
    assert [e.key for e in result.devices] == ["1.1.1", "1.1.3"]

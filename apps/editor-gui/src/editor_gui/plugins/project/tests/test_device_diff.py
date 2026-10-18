"""Pure tests for the device-vs-device diff (:mod:`device_diff.diff_devices`)."""

from __future__ import annotations

from editor_gui.plugins.project.ui.device_diff import (
    CoCell,
    DeviceSnapshot,
    ParamCell,
    diff_devices,
)


def _snap(
    *,
    header: dict[str, str] | None = None,
    params: dict[str, ParamCell] | None = None,
    cos: dict[str, CoCell] | None = None,
) -> DeviceSnapshot:
    return DeviceSnapshot(
        header=header or {"Name": "Dev", "Application": "App (1.0)"},
        parameters=params or {},
        com_objects=cos or {},
    )


def _kinds(rows) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {r.key: r.kind for r in rows}


def test_identical_devices_are_all_same() -> None:
    params = {"P-1_R-1": ParamCell("Delay", "5")}
    cos = {"O-1_R-1": CoCell("Switch", "1.001", "C - W T -", "Low", (("1/1/1", True),))}
    a = _snap(params=params, cos=cos)
    b = _snap(params=dict(params), cos=dict(cos))
    result = diff_devices(a, b)
    assert result.difference_count == 0
    assert all(r.kind == "same" for s in result.sections for r in s)


def test_changed_parameter_reports_both_values() -> None:
    a = _snap(params={"P-1_R-1": ParamCell("Delay", "5")})
    b = _snap(params={"P-1_R-1": ParamCell("Delay", "10")})
    (row,) = diff_devices(a, b).parameters
    assert row.kind == "changed"
    assert row.label == "Delay"
    assert (row.a, row.b) == ("5", "10")


def test_parameter_only_on_one_side_is_added_or_removed() -> None:
    a = _snap(params={"P-1_R-1": ParamCell("Only A", "1")})
    b = _snap(params={"P-2_R-1": ParamCell("Only B", "2")})
    kinds = _kinds(diff_devices(a, b).parameters)
    assert kinds == {"P-1_R-1": "removed", "P-2_R-1": "added"}


def test_com_object_flag_and_dpt_changes_are_changed() -> None:
    a = _snap(
        cos={
            "O-1_R-1": CoCell("Switch", "1.001", "C - W T -", "Low", (("1/1/1", True),))
        }
    )
    b = _snap(
        cos={
            "O-1_R-1": CoCell("Switch", "1.002", "C R W T -", "Low", (("1/1/1", True),))
        }
    )
    (row,) = diff_devices(a, b).com_objects
    assert row.kind == "changed"
    assert "1.001" in row.a and "1.002" in row.b
    assert "C - W" in row.a and "C R W" in row.b


def test_different_applications_fall_out_as_added_removed() -> None:
    a = _snap(params={"A-1_P-1_R-1": ParamCell("x", "1")})
    b = _snap(params={"A-2_P-1_R-1": ParamCell("y", "1")})
    kinds = _kinds(diff_devices(a, b).parameters)
    assert set(kinds.values()) == {"added", "removed"}


def test_equal_value_with_different_label_is_not_a_difference() -> None:
    # Same parameter (same key) and same value, but the label carries a per-device channel name
    # ("Kuechengang" vs "Arbeit"). That is NOT a difference — the value is equal.
    a = _snap(params={"P-1_R-1": ParamCell("Kuechengang > Scenes", "0")})
    b = _snap(params={"P-1_R-1": ParamCell("Arbeit > Scenes", "0")})
    (row,) = diff_devices(a, b).parameters
    assert row.kind == "same"


def test_room_mapped_link_differs_only_in_address() -> None:
    # Two mirror devices: the SAME object, same flags/dpt, each linked sending — only the room GA
    # differs (2/1/27 vs 2/1/38). The diff flags it changed (the GA target differs); the structured
    # links let a consumer see the structure is identical and only the address differs.
    a = _snap(
        cos={"O-1_R-1": CoCell("S", "1.001", "C - W T -", "Low", (("2/1/27", True),))}
    )
    b = _snap(
        cos={"O-1_R-1": CoCell("S", "1.001", "C - W T -", "Low", (("2/1/38", True),))}
    )
    (row,) = diff_devices(a, b).com_objects
    assert row.kind == "changed"
    assert a.com_objects["O-1_R-1"].links == (("2/1/27", True),)
    assert b.com_objects["O-1_R-1"].links == (("2/1/38", True),)


def test_header_difference_is_counted() -> None:
    a = _snap(header={"Name": "A", "Application": "App (1.0)"})
    b = _snap(header={"Name": "B", "Application": "App (1.0)"})
    result = diff_devices(a, b)
    kinds = _kinds(result.header)
    assert kinds["Name"] == "changed"
    assert kinds["Application"] == "same"
    assert result.difference_count == 1

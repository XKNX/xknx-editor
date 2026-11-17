"""The history names what an edit changed: the texts recorded with the edit (#309)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.events import deserialize_event
from xknxeditor.proj.models import ComObject


def _project(tmp_path: Path) -> tuple[ProjectService, str, int, int, int]:
    """A project with the device 1.1.5 "Dimmer" with one group object, and the segment."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = next(a for a in svc.topology(pid, 0).areas if a.address == 1)
    segment = next(ln for ln in area.lines if ln.address == 1).segments[0].id
    device = svc.add_device(
        pid,
        segment,
        "P-1",
        address=5,
        name="Dimmer",
        com_objects=[("O-1_R-1", None)],
    )
    com_object = (
        svc._state(pid).session.query(ComObject).filter_by(device_id=device).one().id  # type: ignore[attr-defined]
    )
    return svc, pid, device, com_object, segment


def _last(svc: ProjectService, pid: str) -> dict[str, Any]:
    """The data of the newest step."""
    return svc.history(pid)[0].data


def test_a_removal_names_what_it_removed(tmp_path: Path) -> None:
    svc, pid, device, _, _ = _project(tmp_path)
    ga = svc.create_group_address(pid, 0, (1 << 11) | (2 << 8) | 3, "Light")
    svc.remove_group_address(pid, ga)
    assert _last(svc, pid)["target"] == "1/2/3 Light"
    svc.remove_device(pid, device)
    assert _last(svc, pid)["target"] == "1.1.5 Dimmer"
    # Undo and redo keep the text, also once the device is gone.
    svc.undo(pid)
    svc.redo(pid)
    assert _last(svc, pid)["target"] == "1.1.5 Dimmer"


def test_an_unnamed_device_is_named_by_its_product(tmp_path: Path) -> None:
    svc, pid, _, _, segment = _project(tmp_path)
    other = svc.add_device(pid, segment, "P-2", address=6, product_name="Switch")
    svc.remove_device(pid, other)
    assert _last(svc, pid)["target"] == "1.1.6 Switch"


def test_a_move_names_the_device_and_its_new_address(tmp_path: Path) -> None:
    svc, pid, device, _, segment = _project(tmp_path)
    svc.move_device(pid, device, segment, 7)
    data = _last(svc, pid)
    assert (data["target"], data["detail"]) == ("1.1.5 Dimmer", "1.1.7")
    svc.park_device(pid, device)
    data = _last(svc, pid)
    assert (data["target"], data["detail"]) == ("1.1.7 Dimmer", "1.1.-")


def test_a_link_names_its_group_address(tmp_path: Path) -> None:
    svc, pid, _, co, _ = _project(tmp_path)
    ga = svc.create_group_address(pid, 0, 1, "Switch")
    link = svc.link_com_object(pid, co, ga, target="1.1.5 #1 Step")
    data = _last(svc, pid)
    assert (data["target"], data["detail"]) == ("1.1.5 #1 Step", "0/0/1 Switch")
    svc.unlink_com_object(pid, link, target="1.1.5 #1 Step")
    data = _last(svc, pid)
    assert (data["target"], data["detail"]) == ("1.1.5 #1 Step", "0/0/1 Switch")
    assert svc.com_object_of_link(pid, link) is None


def test_group_object_edits_keep_the_given_target(tmp_path: Path) -> None:
    svc, pid, _, co, _ = _project(tmp_path)
    svc.set_com_object_datapoint_type(pid, co, "1.001", target="1.1.5 #1 Step")
    assert _last(svc, pid)["target"] == "1.1.5 #1 Step"
    svc.set_com_object_priority(pid, co, "High", target="1.1.5 #1 Step")
    assert _last(svc, pid)["target"] == "1.1.5 #1 Step"
    svc.set_com_object_flag(pid, co, "read_flag", True, target="1.1.5 #1 Step")
    assert _last(svc, pid)["target"] == "1.1.5 #1 Step"
    svc.set_com_object_description(pid, co, "Note", target="1.1.5 #1 Step")
    assert _last(svc, pid)["target"] == "1.1.5 #1 Step"


def test_a_group_address_type_names_the_group_address(tmp_path: Path) -> None:
    svc, pid, _, _, _ = _project(tmp_path)
    ga = svc.create_group_address(pid, 0, 1, "Switch")
    svc.set_group_address_datapoint_type(pid, ga, "1.001")
    assert _last(svc, pid)["target"] == "0/0/1 Switch"


def test_function_and_trade_removals_name_both_elements(tmp_path: Path) -> None:
    svc, pid, device, _, _ = _project(tmp_path)
    building = svc.space_tree(pid, 0)[0].id
    floor = svc.create_space(pid, 0, "Floor", "Ground floor", building)
    space = svc.create_space(pid, 0, "Room", "Kitchen", floor)
    function = svc.create_function(pid, space, "FT-1", "Ceiling")
    ga = svc.create_group_address(pid, 0, 1, "Switch")
    link = svc.add_function_group_address(pid, function, ga)
    svc.remove_function_group_address(pid, link)
    data = _last(svc, pid)
    assert (data["target"], data["detail"]) == ("Ceiling", "0/0/1 Switch")
    trade = svc.create_trade(pid, 0, "Lighting")
    svc.add_trade_device(pid, trade, device)
    svc.remove_trade_device(pid, trade, device)
    data = _last(svc, pid)
    assert (data["target"], data["detail"]) == ("Lighting", "1.1.5 Dimmer")
    svc.remove_space(pid, space)
    assert _last(svc, pid)["target"] == "Kitchen"


def test_an_event_stored_before_has_no_texts() -> None:
    old = {"target_id": 3, "rows": []}
    event = deserialize_event("RemoveDevice", old)
    assert event.to_dict() == old

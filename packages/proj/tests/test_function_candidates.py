"""Function-Workbench: infer building-function candidates from device-channel GA wiring, then
accept them (one undoable step). Also covers the pure DPT classifier."""

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle, parse_ga
from xknxeditor.proj.core.function_detect import (
    FT_BLIND,
    FT_DIM,
    FT_SWITCH,
    classify_function,
)
from xknxeditor.proj.models import (
    ComObject,
    ComObjectLink,
    Device,
    Function,
    GroupAddress,
    Segment,
)

_STYLE = GroupAddressStyle.THREE_LEVEL


def test_classify_function_shapes() -> None:
    assert classify_function(["DPST-1-1"]) == FT_SWITCH
    assert classify_function(["DPST-1-1", "DPST-1-11"]) == FT_SWITCH
    assert classify_function(["DPST-3-7", "DPST-1-1"]) == FT_DIM
    assert classify_function(["DPST-1-1", "DPST-5-1"]) == FT_DIM
    assert classify_function(["DPST-1-8", "DPST-5-1"]) == FT_BLIND
    assert classify_function(["DPST-3-8"]) == FT_BLIND
    # Nothing recognisable -> no suggestion.
    assert classify_function(["DPST-9-1"]) is None
    assert classify_function([None, ""]) is None
    # Incoherent: a blind mixed with a light (switch or dimming) is a whole-room bundle, not a load.
    assert classify_function(["DPST-1-1", "DPST-1-8"]) is None
    assert classify_function(["DPST-3-7", "DPST-3-8"]) is None


def _mk(tmp_path: Path) -> tuple[ProjectService, str]:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-FW", group_address_style=_STYLE)
    return svc, pid


def _wire_channel(
    svc: ProjectService,
    pid: str,
    gas: list[tuple[int, str]],
    *,
    channel: str | None = "CH-1",
    place_in: int | None,
) -> Device:
    """Create a device with one channel whose com-objects link the given (ga_id, dpt) pairs; place
    it in ``place_in`` (a space id) or leave it unassigned."""
    session = svc._state(pid).session
    seg_id = session.query(Segment.id).first()[0]
    dev = Device(segment_id=seg_id, product_ref_id="test", space_id=place_in)
    session.add(dev)
    for i, (ga_id, dpt) in enumerate(gas):
        co = ComObject(ref_id=f"O-{i}", channel_id=channel)
        dev.com_objects.append(co)
        session.flush()
        ga = session.get(GroupAddress, ga_id)
        assert ga is not None
        ga.datapoint_type = dpt
        session.add(
            ComObjectLink(
                com_object_id=co.id, group_address_id=ga_id, is_sending=(i == 0)
            )
        )
    session.commit()
    return dev


def test_candidate_detected_and_accepted_single_undo(tmp_path: Path) -> None:
    svc, pid = _mk(tmp_path)
    room = svc.create_space(
        pid, 0, "Room", "Kitchen", parent_id=svc.space_tree(pid, 0)[0].id
    )
    sw = svc.create_group_address(pid, 0, parse_ga("1/0/1", _STYLE), "Light Kitchen")
    st = svc.create_group_address(
        pid, 0, parse_ga("1/0/2", _STYLE), "Light Kitchen Status"
    )
    _wire_channel(svc, pid, [(sw, "DPST-1-1"), (st, "DPST-1-11")], place_in=room)

    cands = svc.function_candidates(pid, 0)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.function_type == FT_SWITCH
    assert cand.space_id == room
    assert cand.space_name == "Kitchen"
    assert cand.name == "Light Kitchen"
    assert set(cand.group_address_ids) == {sw, st}

    before = svc.cursor(pid)
    fid = svc.accept_function_candidate(pid, cand)
    session = svc._state(pid).session
    fn = session.get(Function, fid)
    assert fn is not None and fn.space_id == room
    assert {link.group_address_id for link in fn.group_addresses} == {sw, st}

    # Accepting is one undoable step; it also removes the candidate from the next suggestion.
    assert svc.function_candidates(pid, 0) == []
    svc.undo(pid)
    assert svc.cursor(pid) == before
    assert session.get(Function, fid) is None
    assert len(svc.function_candidates(pid, 0)) == 1


def test_blind_and_dim_candidates(tmp_path: Path) -> None:
    svc, pid = _mk(tmp_path)
    room = svc.create_space(
        pid, 0, "Room", "Living", parent_id=svc.space_tree(pid, 0)[0].id
    )
    up = svc.create_group_address(pid, 0, parse_ga("2/0/1", _STYLE), "Blind")
    svc.create_group_address(pid, 0, parse_ga("2/0/2", _STYLE), "Dim")
    _wire_channel(svc, pid, [(up, "DPST-1-8")], channel="CH-1", place_in=room)
    _wire_channel(svc, pid, [(up + 1, "DPST-3-7")], channel="CH-2", place_in=room)

    by_type = {c.function_type for c in svc.function_candidates(pid, 0)}
    assert by_type == {FT_BLIND, FT_DIM}


def test_unplaced_device_yields_no_candidate(tmp_path: Path) -> None:
    svc, pid = _mk(tmp_path)
    sw = svc.create_group_address(pid, 0, parse_ga("1/0/1", _STYLE), "Light")
    _wire_channel(svc, pid, [(sw, "DPST-1-1")], place_in=None)
    assert svc.function_candidates(pid, 0) == []


def test_central_ga_is_dropped_and_not_used_as_name(tmp_path: Path) -> None:
    """A GA linked across many devices (time/date/day-night) must not pollute a load's address set
    nor become its name; the load's own sending GA names the function."""
    svc, pid = _mk(tmp_path)
    room = svc.create_space(
        pid, 0, "Room", "Hall", parent_id=svc.space_tree(pid, 0)[0].id
    )
    clock = svc.create_group_address(pid, 0, parse_ga("1/3/0", _STYLE), "Uhrzeit")
    light = svc.create_group_address(pid, 0, parse_ga("2/1/1", _STYLE), "Light Hall")
    # Device under test: sends on the switch, also receives the central clock.
    _wire_channel(svc, pid, [(light, "DPST-1-1"), (clock, "DPST-10-1")], place_in=room)
    # Three more devices link the clock -> 4 devices total -> clock counts as central.
    for _ in range(3):
        _wire_channel(svc, pid, [(clock, "DPST-10-1")], place_in=room)

    cands = svc.function_candidates(pid, 0)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.function_type == FT_SWITCH
    assert cand.name == "Light Hall"
    assert set(cand.group_address_ids) == {light}


def test_channel_independent_objects_yield_no_candidate(tmp_path: Path) -> None:
    svc, pid = _mk(tmp_path)
    room = svc.create_space(
        pid, 0, "Room", "Hall", parent_id=svc.space_tree(pid, 0)[0].id
    )
    sw = svc.create_group_address(pid, 0, parse_ga("1/0/1", _STYLE), "Light")
    _wire_channel(svc, pid, [(sw, "DPST-1-1")], channel=None, place_in=room)
    assert svc.function_candidates(pid, 0) == []

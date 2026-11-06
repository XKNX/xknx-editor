"""The buildings as Home Assistant's floors and areas: what becomes what, and what Home
Assistant has already."""

from __future__ import annotations

from editor_gui.plugins.home_assistant.areas import (
    AreaRow,
    AreaState,
    compare,
    plan_spaces,
    request,
    space_key,
)
from xknxeditor.proj.core.service import SpaceInfo


def _space(
    space_id: int,
    name: str,
    space_type: str,
    *children: SpaceInfo,
    number: str = "",
) -> SpaceInfo:
    return SpaceInfo(
        id=space_id,
        name=name,
        space_type=space_type,
        number=number,
        usage_text="",
        description="",
        children=list(children),
        devices=[],
        functions=[],
    )


def _house(space_id: int = 1, name: str = "House") -> SpaceInfo:
    return _space(
        space_id,
        name,
        "Building",
        _space(
            space_id + 1,
            "Ground Floor",
            "Floor",
            _space(
                space_id + 2,
                "Kitchen",
                "Room",
                _space(space_id + 3, "Cabinet", "DistributionBoard"),
            ),
            _space(space_id + 4, "Hall", "Corridor"),
            number="0",
        ),
        _space(
            space_id + 5,
            "Wing",
            "BuildingPart",
            _space(space_id + 6, "Stairs", "Stairway"),
        ),
    )


def test_floors_and_the_areas_on_them() -> None:
    planned = plan_spaces([_house()], "g")
    assert [(p.kind, p.name, p.path, p.floor_key, p.level) for p in planned] == [
        ("floor", "Ground Floor", "House / Ground Floor", None, 0),
        ("area", "Kitchen", "House / Ground Floor / Kitchen", "g:2", None),
        ("area", "Hall", "House / Ground Floor / Hall", "g:2", None),
        # A stairway on no floor.
        ("area", "Stairs", "House / Wing / Stairs", None, None),
    ]
    assert planned[0].key == space_key("g", 2)


def test_with_several_buildings_a_floor_names_its_building() -> None:
    planned = plan_spaces([_house(1, "House"), _house(10, "Garage")], "g")
    floors = [p.name for p in planned if p.kind == "floor"]
    assert floors == ["House Ground Floor", "Garage Ground Floor"]
    # Areas keep their names.
    assert "Kitchen" in [p.name for p in planned if p.kind == "area"]


def test_what_home_assistant_has_already() -> None:
    planned = plan_spaces([_house()], "g")
    state = {
        "floors": [{"floor_id": "f1", "name": "Ground Floor", "level": 0}],
        "areas": [
            {"area_id": "kitchen", "name": "kitchen", "floor_id": None},
            {"area_id": "corridor", "name": "Corridor", "floor_id": "f1"},
            # Not an area of Home Assistant's shape: left out.
            {"name": "Stairs"},
        ],
        "links": {"g:2": "f1", "g:5": "corridor", "g:7": "gone"},
    }
    rows = compare(planned, state)
    assert [(r.planned.name, r.state, r.current_name) for r in rows] == [
        ("Ground Floor", AreaState.LINKED, "Ground Floor"),
        # Matched by its name, whatever the case.
        ("Kitchen", AreaState.MATCH, "kitchen"),
        # Linked, but renamed in the project since.
        ("Hall", AreaState.RENAMED, "Corridor"),
        # Linked to an area Home Assistant no longer has.
        ("Stairs", AreaState.NEW, ""),
    ]
    assert [r.state for r in compare(planned, None)] == [AreaState.NEW] * 4


def test_what_is_sent_for_the_chosen_ones() -> None:
    planned = plan_spaces([_house()], "g")
    rows = [AreaRow(p, AreaState.NEW) for p in planned[:2]]
    assert request(rows) == {
        "floors": [{"key": "g:2", "name": "Ground Floor", "level": 0}],
        "areas": [{"key": "g:3", "name": "Kitchen", "floor_key": "g:2"}],
    }

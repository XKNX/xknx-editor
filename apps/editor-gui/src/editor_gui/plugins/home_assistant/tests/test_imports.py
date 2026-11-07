"""What Home Assistant has and the project lacks: floors and areas, group addresses."""

from __future__ import annotations

from typing import Any, Literal

from editor_gui.plugins.home_assistant.areas import PlannedSpace
from editor_gui.plugins.home_assistant.imports import (
    HomeAssistantAddress,
    HomeAssistantSpace,
    addresses_to_add,
    spaces_to_add,
)
from editor_gui.plugins.home_assistant.service import GroupAddressUsage


def _planned(kind: Literal["floor", "area"], name: str) -> PlannedSpace:
    return PlannedSpace(key=f"g:{name}", space_id=1, kind=kind, name=name, path=name)


def test_floors_and_areas_the_project_has_no_space_for() -> None:
    state: dict[str, Any] = {
        "floors": [
            {"floor_id": "attic", "name": "Attic", "level": None},
            {"floor_id": "og", "name": "OG", "level": 1},
            {"floor_id": "eg", "name": "eg", "level": 0},
        ],
        "areas": [
            {"area_id": "garden", "name": "Garden", "floor_id": None},
            {"area_id": "bath", "name": "Bath", "floor_id": "og"},
            {"area_id": "hall", "name": "Hall", "floor_id": "eg"},
            {"area_id": "kitchen", "name": "Kitchen", "floor_id": "eg"},
            {"area_id": "shed", "name": "Shed", "floor_id": "gone"},
        ],
        # Linked to a space of any project: not offered.
        "links": {"other:3": "hall"},
    }
    # The project has a floor EG (by name, whatever the case) and a room Kitchen.
    planned = [_planned("floor", "EG"), _planned("area", "Kitchen")]
    found = spaces_to_add(planned, state)
    assert found == [
        # Floors by level, one without a level last.
        HomeAssistantSpace("floor", "og", "OG", level=1),
        HomeAssistantSpace("floor", "attic", "Attic"),
        # Areas by their floor's order, those on no floor (or a floor gone) last.
        HomeAssistantSpace("area", "bath", "Bath", floor_id="og", floor_name="OG"),
        HomeAssistantSpace("area", "garden", "Garden"),
        HomeAssistantSpace("area", "shed", "Shed"),
    ]
    assert spaces_to_add(planned, None) == []
    # Links that are not a mapping count as none.
    assert len(spaces_to_add([], {**state, "links": None})) == 8


def test_group_addresses_the_project_lacks() -> None:
    light = {"kind": "entity", "entity_id": "light.kitchen", "name": "Kitchen"}
    expose = {"kind": "expose", "name": "sensor.outside"}
    usage = {
        0x0A05: GroupAddressUsage(users=(expose, light), dpt="1.001"),
        0x0A03: GroupAddressUsage(users=(light,), dpt="1.001"),
        0x0A04: GroupAddressUsage(users=({"kind": "time", "name": "Time"},)),
        0x0A06: GroupAddressUsage(),
    }
    found = addresses_to_add(usage, known={0x0A03})
    assert found == [
        HomeAssistantAddress(0x0A04, "Time", None, ("Time",)),
        # Named after an entity first, even when an expose comes before it.
        HomeAssistantAddress(
            0x0A05, "Kitchen", "1.001", ("sensor.outside", "light.kitchen")
        ),
        # Used by nothing with a name: none.
        HomeAssistantAddress(0x0A06, "", None, ()),
    ]

"""The project's buildings as Home Assistant's floors and areas.

A floor of a building becomes a floor of Home Assistant; a room, corridor or stairway becomes an
area, on the floor it is on. Buildings and building parts only group them, and cabinets hold
the devices that switch, not the places people use, so they become nothing. Home Assistant has
one home: with more than one building, a floor's name starts with its building's.

Each space is known to the integration by a key of the project and the space; the integration
remembers which floor or area a key became (its "links"), so a renamed space renames its floor
or area. A space whose key is not linked yet is matched to a floor or area of the same name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, cast

from xknxeditor.proj.core.service import SpaceInfo
from xknxeditor.proj.core.space_rules import (
    BUILDING,
    CORRIDOR,
    FLOOR,
    ROOM,
    STAIRWAY,
)

# The spaces that become an area.
_AREA_TYPES = frozenset({ROOM, CORRIDOR, STAIRWAY})


@dataclass(frozen=True)
class PlannedSpace:
    """A space and what it becomes in Home Assistant."""

    key: str
    space_id: int
    kind: Literal["floor", "area"]
    # The name in Home Assistant.
    name: str
    # Where it is in the buildings, for showing it ("House / Ground Floor / Kitchen").
    path: str
    # An area: the key of its floor (None: on no floor). A floor: its level (None: unknown).
    floor_key: str | None = None
    level: int | None = None


class AreaState(StrEnum):
    # Home Assistant has none of this name: it is created.
    NEW = "new"
    # Home Assistant has one of this name, not linked yet: they are linked.
    MATCH = "match"
    # Linked, with the same name.
    LINKED = "linked"
    # Linked, but Home Assistant's has another name: it is renamed.
    RENAMED = "renamed"


@dataclass(frozen=True)
class AreaRow:
    planned: PlannedSpace
    state: AreaState
    # The name Home Assistant's floor or area has (MATCH, LINKED, RENAMED).
    current_name: str = ""


def space_key(project_guid: str, space_id: int) -> str:
    return f"{project_guid}:{space_id}"


def _level(number: str, name: str) -> int | None:
    """A floor's level: its number, else a whole number its name starts with ("0 - UG",
    "-1 Keller"); None for neither."""
    for text in (number.strip(), name.strip()):
        if (match := _LEADING_NUMBER.match(text)) is not None:
            return int(match.group(1))
    return None


_LEADING_NUMBER = re.compile(r"(-?\d+)(?!\d)(?![.,]\d)")


def plan_spaces(tree: Sequence[SpaceInfo], project_guid: str) -> list[PlannedSpace]:
    """What the buildings of ``tree`` become, floors before the areas on them, in tree order."""
    buildings = [space for space in tree if space.space_type == BUILDING]
    several = len(buildings) > 1
    floors: list[PlannedSpace] = []
    areas: list[PlannedSpace] = []

    def walk(
        space: SpaceInfo,
        path: tuple[str, ...],
        building: str,
        floor_key: str | None,
    ) -> None:
        here = (*path, space.name)
        key = space_key(project_guid, space.id)
        if space.space_type == FLOOR:
            floor_key = key
            floors.append(
                PlannedSpace(
                    key=key,
                    space_id=space.id,
                    kind="floor",
                    name=f"{building} {space.name}"
                    if several and building
                    else space.name,
                    path=" / ".join(here),
                    level=_level(space.number, space.name),
                )
            )
        elif space.space_type in _AREA_TYPES:
            areas.append(
                PlannedSpace(
                    key=key,
                    space_id=space.id,
                    kind="area",
                    name=space.name,
                    path=" / ".join(here),
                    floor_key=floor_key,
                )
            )
        if space.space_type == BUILDING:
            building = space.name
        for child in space.children:
            walk(child, here, building, floor_key)

    for space in tree:
        walk(space, (), "", None)
    return [*floors, *areas]


def by_id(items: object, id_key: str) -> dict[str, dict[str, Any]]:
    """Home Assistant's floors or areas (as the integration reports them) by id."""
    if not isinstance(items, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in cast("list[object]", items):
        if isinstance(item, dict):
            entry = cast("dict[str, Any]", item)
            if isinstance(entry.get(id_key), str):
                result[str(entry[id_key])] = dict(entry)
    return result


def compare(
    planned: Sequence[PlannedSpace], home_assistant: Mapping[str, Any] | None
) -> list[AreaRow]:
    """What each planned floor and area is in Home Assistant now (``home_assistant``: its
    floors, areas and the links, as the integration reports them)."""
    state = home_assistant or {}
    floors = by_id(state.get("floors"), "floor_id")
    areas = by_id(state.get("areas"), "area_id")
    links_value = state.get("links")
    links: dict[str, str] = (
        {str(k): str(v) for k, v in cast("dict[object, object]", links_value).items()}
        if isinstance(links_value, dict)
        else {}
    )
    rows: list[AreaRow] = []
    for space in planned:
        existing = floors if space.kind == "floor" else areas
        linked = existing.get(links.get(space.key, ""))
        if linked is not None:
            name = str(linked.get("name", ""))
            rows.append(
                AreaRow(
                    space,
                    AreaState.LINKED if name == space.name else AreaState.RENAMED,
                    name,
                )
            )
            continue
        wanted = space.name.casefold()
        same = next(
            (
                item
                for item in existing.values()
                if str(item.get("name", "")).casefold() == wanted
            ),
            None,
        )
        if same is not None:
            rows.append(AreaRow(space, AreaState.MATCH, str(same.get("name", ""))))
        else:
            rows.append(AreaRow(space, AreaState.NEW))
    return rows


def request(
    rows: Sequence[AreaRow], targets: Mapping[str, str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """The floors and areas to send for ``rows`` (the chosen ones): their keys, names, levels and
    floors; the integration creates, links or renames them. ``targets``: the floor or area of
    Home Assistant a space's key is to be linked to as it is (the space was made from it)."""
    chosen = targets or {}

    def target(key: str, id_key: str) -> dict[str, str]:
        return {id_key: chosen[key]} if key in chosen else {}

    return {
        "floors": [
            {
                "key": r.planned.key,
                "name": r.planned.name,
                "level": r.planned.level,
                **target(r.planned.key, "floor_id"),
            }
            for r in rows
            if r.planned.kind == "floor"
        ],
        "areas": [
            {
                "key": r.planned.key,
                "name": r.planned.name,
                "floor_key": r.planned.floor_key,
                **target(r.planned.key, "area_id"),
            }
            for r in rows
            if r.planned.kind == "area"
        ],
    }

"""What Home Assistant has and the project lacks: its floors and areas, for the project's
buildings, and the group addresses its KNX integration uses, for the project's group addresses.

A floor or area of Home Assistant that no space is linked to (see :mod:`.areas`), and that no
floor or room of the project has the name of, can become a floor or a room of the project; a group
address that Home Assistant's KNX entities, exposes or time server use and that the project does
not have can become one of the project's, named after what uses it, with the datapoint type Home
Assistant uses it with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from editor_gui.plugins.home_assistant.areas import PlannedSpace, by_id


@dataclass(frozen=True)
class HomeAssistantSpace:
    """A floor or area of Home Assistant that the project has no space for."""

    kind: Literal["floor", "area"]
    # Home Assistant's floor_id or area_id.
    ha_id: str
    name: str
    # A floor's level (None: unknown).
    level: int | None = None
    # An area's floor (None: on no floor), and its name.
    floor_id: str | None = None
    floor_name: str = ""


class _Usage(Protocol):
    @property
    def users(self) -> tuple[dict[str, Any], ...]: ...

    @property
    def dpt(self) -> str | None: ...


@dataclass(frozen=True)
class HomeAssistantAddress:
    """A group address Home Assistant uses that the project does not have."""

    raw: int
    # The name it gets: of the first entity (else expose, time server) using it.
    name: str
    # The datapoint type Home Assistant uses it with ("9.001"; None: unknown).
    dpt: str | None
    # What uses it: entity ids, or the names of exposes and the time server.
    users: tuple[str, ...]


def _links(state: Mapping[str, Any]) -> set[str]:
    """The floors and areas of Home Assistant a space (of any project) is linked to."""
    links = state.get("links")
    if not isinstance(links, dict):
        return set()
    return {str(v) for v in cast("dict[object, object]", links).values()}


def _level(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def spaces_to_add(
    planned: Sequence[PlannedSpace], home_assistant: Mapping[str, Any] | None
) -> list[HomeAssistantSpace]:
    """Home Assistant's floors (by level, then name) and areas (by their floor, then name) that
    no space is linked to and that no planned floor or area (of ``planned``) has the name of."""
    state = home_assistant or {}
    floors = by_id(state.get("floors"), "floor_id")
    areas = by_id(state.get("areas"), "area_id")
    linked = _links(state)
    names = {
        kind: {p.name.casefold() for p in planned if p.kind == kind}
        for kind in ("floor", "area")
    }

    def missing(kind: str, ha_id: str, entry: Mapping[str, Any]) -> bool:
        return (
            ha_id not in linked
            and str(entry.get("name", "")).casefold() not in names[kind]
        )

    floor_order = sorted(
        floors,
        key=lambda f: (
            _level(floors[f].get("level")) is None,
            _level(floors[f].get("level")) or 0,
            str(floors[f].get("name", "")).casefold(),
        ),
    )
    found = [
        HomeAssistantSpace(
            "floor",
            ha_id,
            str(floors[ha_id].get("name", "")),
            level=_level(floors[ha_id].get("level")),
        )
        for ha_id in floor_order
        if missing("floor", ha_id, floors[ha_id])
    ]
    position = {ha_id: index for index, ha_id in enumerate(floor_order)}

    def area_order(ha_id: str) -> tuple[int, str]:
        floor_id = areas[ha_id].get("floor_id")
        return (
            position.get(str(floor_id), len(position)),
            str(areas[ha_id].get("name", "")).casefold(),
        )

    for ha_id in sorted(areas, key=area_order):
        area = areas[ha_id]
        if not missing("area", ha_id, area):
            continue
        floor_id = area.get("floor_id")
        floor = floors.get(str(floor_id)) if floor_id else None
        found.append(
            HomeAssistantSpace(
                "area",
                ha_id,
                str(area.get("name", "")),
                floor_id=str(floor_id) if floor is not None else None,
                floor_name=str(floor.get("name", "")) if floor is not None else "",
            )
        )
    return found


def _user_name(user: Mapping[str, Any]) -> str:
    return str(user.get("name") or user.get("entity_id") or "")


def addresses_to_add(
    usage: Mapping[int, _Usage], known: Set[int]
) -> list[HomeAssistantAddress]:
    """The group addresses of ``usage`` (what Home Assistant does with each, by raw address)
    that ``known`` (the project's, raw) lacks, in address order."""
    found: list[HomeAssistantAddress] = []
    for raw in sorted(usage):
        if raw in known:
            continue
        users = usage[raw].users
        entities = [u for u in users if u.get("kind") == "entity"]
        named = next((n for u in (*entities, *users) if (n := _user_name(u))), "")
        found.append(
            HomeAssistantAddress(
                raw,
                named,
                usage[raw].dpt,
                tuple(
                    str(u.get("entity_id") or _user_name(u))
                    for u in users
                    if u.get("entity_id") or _user_name(u)
                ),
            )
        )
    return found

"""Home Assistant entities for the project's building functions.

A building function sits in a room and names the role of each of its group addresses (its
function points, see :mod:`xknxeditor.proj.core.function_roles`). That is what an entity of Home
Assistant's KNX integration needs: a switchable light becomes a light with its switching
address, a dimmable one a light with its brightness, sun protection a cover, a switch a switch.
The entity gets the function's name and the area its room became (see :mod:`.areas`).

Whether Home Assistant takes a configuration is for Home Assistant to say: the integration checks
each candidate with the KNX integration's own validation before it is offered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from editor_gui.plugins.home_assistant.areas import space_key
from xknxeditor.proj.core.function_roles import function_roles
from xknxeditor.proj.core.service import FunctionInfo, SpaceInfo

# Function type -> platform and the configuration of each role: (config key, "write" or
# "state"). A role not in the map is not used; the first role is required.
_SWITCH = (("SwitchOnOff", "ga_switch", "write"), ("InfoOnOff", "ga_switch", "state"))
_TYPES: dict[str, tuple[str, tuple[tuple[str, str, str], ...]]] = {
    # Switchable light.
    "FT-1": ("light", _SWITCH),
    # Dimmable light: its dimming (relative) is not Home Assistant's brightness.
    "FT-2": ("light", _SWITCH),
    # Dimmable light with an absolute value.
    "FT-6": (
        "light",
        (
            *_SWITCH,
            ("DimmingValue", "ga_brightness", "write"),
            ("InfoDimmingValue", "ga_brightness", "state"),
        ),
    ),
    # Sun protection.
    "FT-3": (
        "cover",
        (("MoveUpDown", "ga_up_down", "write"), ("StopStepUpDown", "ga_step", "write")),
    ),
    "FT-7": (
        "cover",
        (
            ("MoveUpDown", "ga_up_down", "write"),
            ("StopStepUpDown", "ga_step", "write"),
            ("CurrentAbsolutePositionBlindsPercentage", "ga_position_state", "state"),
            ("CurrentAbsolutePositionSlatPercentage", "ga_angle", "state"),
        ),
    ),
    # Switch.
    "FT-10": ("switch", _SWITCH),
}


@dataclass(frozen=True)
class EntityCandidate:
    """A building function and the entity it becomes."""

    key: str
    function_id: int
    name: str
    platform: str
    # The ``knx`` part of the entity's configuration (group addresses as printed).
    knx: dict[str, Any]
    # The group addresses it uses, as printed.
    group_addresses: tuple[str, ...]
    # Where the function is in the buildings, and the key of its space (for the area).
    path: str
    space_key: str
    # The group address it switches with (raw): an entity using it exists already.
    main_raw: int | None = None
    # The ids of the group addresses it uses (their devices are the ones to download).
    group_address_ids: tuple[int, ...] = ()


def function_key(project_guid: str, function_id: int) -> str:
    return f"{project_guid}:function:{function_id}"


def _candidate(
    function: FunctionInfo,
    space: SpaceInfo,
    path: str,
    project_guid: str,
    lookup: Callable[[int], tuple[str | None, int] | None],
) -> EntityCandidate | None:
    kind = _TYPES.get(function.function_type)
    if kind is None:
        return None
    platform, roles = kind
    links = function.group_addresses
    known = [lookup(link.group_address_id) for link in links]
    named = function_roles(
        function.function_type,
        [
            (link.role, k[0] if k else None)
            for link, k in zip(links, known, strict=True)
        ],
    )
    by_role = {
        role: (link.text, k[1] if k else None)
        for role, link, k in zip(named, links, known, strict=True)
        if role
    }
    required = roles[0][0]
    if required not in by_role:
        return None
    knx: dict[str, Any] = {}
    used: list[str] = []
    for role, key, slot in roles:
        if (found := by_role.get(role)) is not None:
            address = found[0]
            knx.setdefault(key, {"write": None, "state": None, "passive": []})[slot] = (
                address
            )
            if address not in used:
                used.append(address)
    return EntityCandidate(
        key=function_key(project_guid, function.id),
        function_id=function.id,
        name=function.name,
        platform=platform,
        knx=knx,
        group_addresses=tuple(used),
        path=f"{path} / {function.name}",
        space_key=space_key(project_guid, space.id),
        main_raw=by_role[required][1],
        group_address_ids=tuple(
            dict.fromkeys(
                link.group_address_id
                for role, link in zip(named, links, strict=True)
                if role and any(role == r for r, _key, _slot in roles)
            )
        ),
    )


def addresses_differ(
    wanted: Mapping[str, Any], current: Mapping[str, Any] | None
) -> bool:
    """Whether the group addresses of an entity's stored configuration (``current``) are others
    than its function's now (``wanted``): those it writes to and reads its state from, of each
    key the function sets (keys added in Home Assistant stay as they are)."""
    if current is None:
        return False
    for key, want in wanted.items():
        have: object = current.get(key)
        stored: Mapping[str, Any] = (
            cast("Mapping[str, Any]", have) if isinstance(have, Mapping) else {}
        )
        if any(want.get(slot) != stored.get(slot) for slot in ("write", "state")):
            return True
    return False


def entity_candidates(
    tree: Sequence[SpaceInfo],
    project_guid: str,
    lookup: Callable[[int], tuple[str | None, int] | None],
) -> list[EntityCandidate]:
    """The entities the building functions of ``tree`` become, in tree order; a function of a
    type without an entity, or without the address it needs, becomes none. ``lookup`` gives a
    group address's datapoint type and raw address by its id (None: not there)."""
    found: list[EntityCandidate] = []

    def walk(space: SpaceInfo, path: tuple[str, ...]) -> None:
        here = (*path, space.name)
        for function in space.functions:
            candidate = _candidate(
                function, space, " / ".join(here), project_guid, lookup
            )
            if candidate is not None:
                found.append(candidate)
        for child in space.children:
            walk(child, here)

    for space in tree:
        walk(space, ())
    return found


def entity_request(candidate: EntityCandidate, area_id: str | None) -> dict[str, Any]:
    """What the integration gets to check or create ``candidate`` (in the area ``area_id``)."""
    return {
        "key": candidate.key,
        "platform": candidate.platform,
        "name": candidate.name,
        "knx": candidate.knx,
        "area_id": area_id,
    }


def existing_entities(
    candidate: EntityCandidate, users_by_raw: Mapping[int, Sequence[dict[str, Any]]]
) -> list[str]:
    """The entities of Home Assistant using the address ``candidate`` switches with."""
    if candidate.main_raw is None:
        return []
    return [
        str(user["entity_id"])
        for user in users_by_raw.get(candidate.main_raw, ())
        if user.get("kind") == "entity" and user.get("entity_id")
    ]

"""Which building parts a building part can hold, and which of them hold devices.

The types are the project's ``Space/@Type`` values. The Buildings view holds buildings only;
a building or building part holds building parts, floors, stairways, rooms and corridors; a
floor holds building parts, rooms and corridors; a stairway, room or corridor holds cabinets
(``DistributionBoard``), and a cabinet nothing. Stairways, rooms, corridors and cabinets hold
devices. A type is the one a building part was made with: it never changes.
"""

from __future__ import annotations

BUILDING = "Building"
BUILDING_PART = "BuildingPart"
FLOOR = "Floor"
STAIRWAY = "Stairway"
ROOM = "Room"
CORRIDOR = "Corridor"
CABINET = "DistributionBoard"

# Every type, in the order the "Add" menus list them.
SPACE_TYPES = (BUILDING, BUILDING_PART, FLOOR, STAIRWAY, ROOM, CORRIDOR, CABINET)

# The types a building part of each type holds; None stands for the Buildings view itself.
_CHILDREN: dict[str | None, tuple[str, ...]] = {
    None: (BUILDING,),
    BUILDING: (BUILDING_PART, FLOOR, STAIRWAY, ROOM, CORRIDOR),
    BUILDING_PART: (BUILDING_PART, FLOOR, STAIRWAY, ROOM, CORRIDOR),
    FLOOR: (BUILDING_PART, ROOM, CORRIDOR),
    STAIRWAY: (CABINET,),
    ROOM: (CABINET,),
    CORRIDOR: (CABINET,),
    CABINET: (),
}

# The types that hold devices.
DEVICE_TYPES = frozenset({STAIRWAY, ROOM, CORRIDOR, CABINET})


class SpaceRuleError(ValueError):
    """A building part that cannot go where it was put. ``reason``: ``type_not_here`` (its type
    is not allowed there), ``cabinet_not_here`` (the same for a cabinet), ``target_is_child``
    (moved into itself or a building part below it), ``no_devices`` (the building part holds no
    devices)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(SPACE_ERRORS[reason])


# The English text of each reason; the editor shows them translated.
SPACE_ERRORS = {
    "type_not_here": "A building part of this type cannot be added here.",
    "cabinet_not_here": (
        "A building part of this type cannot be added here.\nCabinets can be added inside of "
        "rooms, corridors, or stairways only. If applicable the corridors have to be moved "
        "into the corresponding building parts."
    ),
    "target_is_child": "The target is a child building part.",
    "no_devices": "Devices can only be added to stairways, rooms, corridors and cabinets.",
}


def child_types(parent_type: str | None) -> tuple[str, ...]:
    """The types a building part of ``parent_type`` holds (None: the Buildings view), in the
    order the "Add" menu lists them; none for a type this editor does not know."""
    return _CHILDREN.get(parent_type, ())


def holds_devices(space_type: str) -> bool:
    """Whether a building part of ``space_type`` holds devices."""
    return space_type in DEVICE_TYPES


def check_child(parent_type: str | None, space_type: str) -> None:
    """Raise a ``SpaceRuleError`` unless a building part of ``space_type`` can go below one of
    ``parent_type`` (None: the Buildings view)."""
    if space_type in child_types(parent_type):
        return
    raise SpaceRuleError(
        "cabinet_not_here" if space_type == CABINET else "type_not_here"
    )

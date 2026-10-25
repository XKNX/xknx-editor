"""The building part types as the Buildings view shows them: their names, their "Add" menu
items and dialog titles, the name a new one gets, and the translated reason a building part
cannot go somewhere."""

from __future__ import annotations

from editor_gui.plugins.project.strings import S
from xknxeditor.proj.core.space_rules import (
    BUILDING,
    BUILDING_PART,
    CABINET,
    CORRIDOR,
    FLOOR,
    ROOM,
    STAIRWAY,
    SpaceRuleError,
)


def space_type_name(space_type: str) -> str:
    """The name of a type ("Floor"), translated; an unknown type as it is."""
    names = {
        BUILDING: S.SPACES_TYPE_BUILDING,
        BUILDING_PART: S.SPACES_TYPE_BUILDINGPART,
        FLOOR: S.SPACES_TYPE_FLOOR,
        STAIRWAY: S.SPACES_TYPE_STAIRWAY,
        ROOM: S.SPACES_TYPE_ROOM,
        CORRIDOR: S.SPACES_TYPE_CORRIDOR,
        CABINET: S.SPACES_TYPE_DISTRIBUTION,
    }
    return names.get(space_type, space_type)


def add_item_text(space_type: str) -> str:
    """The "Add" submenu item of a type ("Floors…")."""
    return {
        BUILDING: S.SPACES_ADD_BUILDINGS,
        BUILDING_PART: S.SPACES_ADD_BUILDING_PARTS,
        FLOOR: S.SPACES_ADD_FLOORS,
        STAIRWAY: S.SPACES_ADD_STAIRWAYS,
        ROOM: S.SPACES_ADD_ROOMS,
        CORRIDOR: S.SPACES_ADD_CORRIDORS,
        CABINET: S.SPACES_ADD_CABINETS,
    }[space_type]


def add_title(space_type: str) -> str:
    """The title of the dialog that adds building parts of a type ("Add Floors")."""
    return {
        BUILDING: S.SPACES_TITLE_BUILDINGS,
        BUILDING_PART: S.SPACES_TITLE_BUILDING_PARTS,
        FLOOR: S.SPACES_TITLE_FLOORS,
        STAIRWAY: S.SPACES_TITLE_STAIRWAYS,
        ROOM: S.SPACES_TITLE_ROOMS,
        CORRIDOR: S.SPACES_TITLE_CORRIDORS,
        CABINET: S.SPACES_TITLE_CABINETS,
    }[space_type]


def default_name(space_type: str) -> str:
    """The name a new building part of a type gets when none is entered ("New floor")."""
    return {
        BUILDING: S.SPACES_DEFAULT_BUILDING,
        BUILDING_PART: S.SPACES_DEFAULT_BUILDING_PART,
        FLOOR: S.SPACES_DEFAULT_FLOOR,
        STAIRWAY: S.SPACES_DEFAULT_STAIRWAY,
        ROOM: S.SPACES_DEFAULT_ROOM,
        CORRIDOR: S.SPACES_DEFAULT_CORRIDOR,
        CABINET: S.SPACES_DEFAULT_CABINET,
    }.get(space_type, space_type)


def space_error_text(error: SpaceRuleError) -> str:
    """The reason of ``error`` in the language shown."""
    return {
        "type_not_here": S.SPACE_ERROR_TYPE_NOT_HERE,
        "cabinet_not_here": S.SPACE_ERROR_CABINET_NOT_HERE,
        "target_is_child": S.SPACE_ERROR_TARGET_IS_CHILD,
        "no_devices": S.SPACE_ERROR_NO_DEVICES,
    }.get(error.reason, str(error))

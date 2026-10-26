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


# The type a new function starts with: a switchable light.
DEFAULT_FUNCTION_TYPE = "FT-1"


def function_type_presets() -> list[tuple[str, str]]:
    """(label, KNX FunctionType code) for the type picker: every current (non-deprecated) type
    of knx_master.xml, in its order; role stays free text since group addresses are accepted
    without one."""
    return [
        (S.SPACES_FN_TYPE_CUSTOM, "FT-0"),
        (S.SPACES_FN_TYPE_SWITCH, "FT-1"),
        (S.SPACES_FN_TYPE_DIM, "FT-6"),
        (S.SPACES_FN_TYPE_SUN_PROTECTION, "FT-7"),
        (S.SPACES_FN_TYPE_HEATING_SWITCHING, "FT-8"),
        (S.SPACES_FN_TYPE_HEATING_CONTINUOUS, "FT-9"),
        (S.SPACES_FN_TYPE_SOCKET, "FT-10"),
    ]


def function_type_label(code: str) -> str:
    """The text of a function type (a KNX FunctionType code)."""
    for label, c in function_type_presets():
        if c == code:
            return label
    return code or S.SPACES_FN_TYPE_CUSTOM


def default_function_name(code: str) -> str:
    """The name a new function of a type gets when none is entered: its type's, "New
    function" for a custom one."""
    if code and code != "FT-0":
        return function_type_label(code)
    return S.SPACES_DEFAULT_FUNCTION


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

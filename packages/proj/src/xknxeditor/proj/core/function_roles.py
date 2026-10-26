"""The roles of the group addresses of a building function, from its type.

Each KNX function type has function points (``knx_master.xml``, master data version 308): the
role a group address has in a function of that type (the ``Name`` of the point's
``DatapointRole``) and the datapoint type it carries, in their order. A function's
``<GroupAddressRef>`` names its role in ``@Role``.
"""

from __future__ import annotations

from collections.abc import Sequence

# Function type -> (role, datapoint type) of each function point, in the master data's order.
FUNCTION_POINTS: dict[str, tuple[tuple[str, str], ...]] = {
    "FT-0": (),
    "FT-1": (("SwitchOnOff", "DPST-1-1"), ("InfoOnOff", "DPST-1-1")),
    "FT-2": (
        ("SwitchOnOff", "DPST-1-1"),
        ("DimmingControl", "DPST-3-7"),
        ("InfoDimmingValue", "DPST-5-1"),
        ("InfoOnOff", "DPST-1-1"),
    ),
    "FT-3": (
        ("MoveUpDown", "DPST-1-8"),
        ("StopStepUpDown", "DPST-1-7"),
        ("WindAlarm", "DPST-1-5"),
        ("RainAlarm", "DPST-1-5"),
    ),
    "FT-4": (
        ("TempRoom", "DPST-9-1"),
        ("TempRoomSetpoint", "DPST-9-1"),
        ("ValvePosition", "DPST-5-1"),
        ("HVACMode", "DPST-20-102"),
        ("WindowStatus", "DPST-1-19"),
    ),
    "FT-5": (
        ("TempRoom", "DPST-9-1"),
        ("TempRoomSetpoint", "DPST-9-1"),
        ("ValvePosition", "DPST-5-1"),
        ("HVACMode", "DPST-20-102"),
        ("WindowStatus", "DPST-1-19"),
    ),
    "FT-6": (
        ("SwitchOnOff", "DPST-1-1"),
        ("DimmingControl", "DPST-3-7"),
        ("DimmingValue", "DPST-5-1"),
        ("InfoOnOff", "DPST-1-1"),
        ("InfoDimmingValue", "DPST-5-1"),
    ),
    "FT-7": (
        ("MoveUpDown", "DPST-1-8"),
        ("StopStepUpDown", "DPST-1-7"),
        ("WindAlarm", "DPST-1-5"),
        ("RainAlarm", "DPST-1-5"),
        ("CurrentAbsolutePositionBlindsPercentage", "DPST-5-1"),
        ("CurrentAbsolutePositionSlatPercentage", "DPST-5-1"),
    ),
    "FT-8": (
        ("TempRoom", "DPST-9-1"),
        ("TempRoomSetpoint", "DPST-9-1"),
        ("ValveSwitch", "DPST-1-1"),
        ("HVACMode", "DPST-20-102"),
        ("WindowStatus", "DPST-1-19"),
    ),
    "FT-9": (
        ("TempRoom", "DPST-9-1"),
        ("TempRoomSetpoint", "DPST-9-1"),
        ("ValvePosition", "DPST-5-1"),
        ("HVACMode", "DPST-20-102"),
        ("WindowStatus", "DPST-1-19"),
    ),
    "FT-10": (("SwitchOnOff", "DPST-1-1"), ("InfoOnOff", "DPST-1-1")),
}


def _main_type(datapoint_type: str) -> str:
    """The main type of ``DPST-1-1`` or ``DPT-1``: ``1``; "" for anything else."""
    parts = datapoint_type.split("-")
    return parts[1] if len(parts) >= 2 and parts[0] in ("DPT", "DPST") else ""


def function_roles(
    function_type: str | None, links: Sequence[tuple[str, str | None]]
) -> list[str]:
    """The role of each group address linked with a function of ``function_type``; ``links``
    are each one's stored role and its group address's datapoint type, in their order.

    A stored role of the type stays. Every other link gets the first function point not taken
    yet that carries its datapoint type, else one of the same main type; a group address
    without a datapoint type gets the first point not taken yet. "" when no point is left, and
    for a type without points (custom) or not known, the stored roles as they are."""
    points = FUNCTION_POINTS.get(function_type or "")
    if not points:
        return [role for role, _dpt in links]
    names = {name for name, _dpt in points}
    roles = [role if role in names else "" for role, _dpt in links]
    taken = {role for role in roles if role}

    def first(exact: str | None = None, main: str | None = None) -> str:
        """The first point not taken yet (of the datapoint type ``exact``, of the main type
        ``main``)."""
        for name, point_dpt in points:
            if name in taken or exact not in (None, point_dpt):
                continue
            if main is not None and _main_type(point_dpt) != main:
                continue
            return name
        return ""

    for index, (_role, dpt) in enumerate(links):
        if roles[index]:
            continue
        if dpt:
            main = _main_type(dpt)
            role = first(exact=dpt) or (first(main=main) if main else "")
        else:
            role = first()
        roles[index] = role
        if role:
            taken.add(role)
    return roles

"""The effective KNX Data Secure state of group addresses and devices.

A device is commissioned securely ("Secure Commissioning" activated) when the project holds a Tool
Key for it (``<Security @ToolKey>``). A group address with Security ``On`` is secured and one with
``Off`` is not. In the default ``Auto`` mode it is secured when it is linked and every device linked
to it is commissioned securely: a device commissioned in the clear could not take part in its
secured communication. The state is derived from the links whenever it is needed, so a change of a
link or of a device's secure commissioning counts everywhere at once.

A main or middle group with Security ``On`` or ``Off`` forces it on every group address and group
below it: the nearest such group above an address decides, the address's own mode only counts
while none does. The own modes stay stored, so they count again once the group is back on ``Auto``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy.orm import Session

from ..models import ComObject, ComObjectLink, Device, GroupAddress, GroupRange

#: The ``@Security`` values of a group address that let its links decide (``None`` = default).
AUTO_MODES: frozenset[str | None] = frozenset({None, "Auto"})
#: The ``@Security`` values of a group that force it on everything below it.
FORCED_MODES: frozenset[str | None] = frozenset({"On", "Off"})


def forcing_range(group_range: GroupRange | None) -> GroupRange | None:
    """The nearest group from ``group_range`` up (itself included) that forces its Security;
    None when none does."""
    while group_range is not None:
        if group_range.security_mode in FORCED_MODES:
            return group_range
        group_range = group_range.parent
    return None


def effective_mode(group_address: GroupAddress) -> str | None:
    """The Security mode that counts for a group address: the one of the nearest group above it
    that forces one, else its own."""
    forcing = forcing_range(group_address.group_range)
    return forcing.security_mode if forcing is not None else group_address.security_mode


def effective_modes(session: Session) -> dict[int, str | None]:
    """The Security mode that counts for every group address (see :func:`effective_mode`)."""
    ranges = {
        range_id: (parent_id, mode)
        for range_id, parent_id, mode in session.query(
            GroupRange.id, GroupRange.parent_id, GroupRange.security_mode
        )
    }
    forced: dict[int, str | None] = {}

    def forced_by(range_id: int | None) -> str | None:
        if range_id is None or range_id not in ranges:
            return None
        if range_id not in forced:
            parent_id, mode = ranges[range_id]
            forced[range_id] = mode if mode in FORCED_MODES else forced_by(parent_id)
        return forced[range_id]

    return {
        ga_id: forced_by(range_id) or mode
        for ga_id, range_id, mode in session.query(
            GroupAddress.id, GroupAddress.group_range_id, GroupAddress.security_mode
        )
    }


def security_attributes(device: Device) -> dict[str, str]:
    """The attributes of the device's stored ``<Security>`` (empty when it has none)."""
    raw = (device.extra_subtrees or {}).get("Security")
    if not raw:
        return {}
    try:
        return dict(ET.fromstring(raw).attrib)
    except ET.ParseError:
        return {}


def secure_commissioned(device: Device) -> bool:
    """Whether the device is commissioned securely: the project holds a Tool Key for it."""
    return bool(security_attributes(device).get("ToolKey"))


def is_secured(
    mode: str | None, devices: Iterable[int], commissioned: set[int]
) -> bool:
    """Whether a group address with Security ``mode``, linked to ``devices``, is secured, given
    the securely commissioned devices."""
    if mode == "On":
        return True
    if mode not in AUTO_MODES:
        return False
    linked = set(devices)
    return bool(linked) and linked <= commissioned


def commissioned_devices(session: Session) -> set[int]:
    """The ids of the devices commissioned securely."""
    return {
        device_id
        for device_id, subtrees in session.query(Device.id, Device.extra_subtrees)
        if subtrees and _has_tool_key(subtrees.get("Security"))
    }


def linked_devices(session: Session) -> dict[int, set[int]]:
    """Each linked group address's id -> the ids of the devices linked to it."""
    linked: dict[int, set[int]] = defaultdict(set)
    rows = session.query(ComObjectLink.group_address_id, ComObject.device_id).join(
        ComObject, ComObject.id == ComObjectLink.com_object_id
    )
    for group_address_id, device_id in rows:
        linked[group_address_id].add(device_id)
    return linked


def secured_group_addresses(session: Session) -> set[int]:
    """The ids of the group addresses that are secured now."""
    commissioned = commissioned_devices(session)
    linked = linked_devices(session)
    return {
        ga_id
        for ga_id, mode in effective_modes(session).items()
        if is_secured(mode, linked.get(ga_id, ()), commissioned)
    }


def exports_key(group_address: GroupAddress, secured: bool) -> bool:
    """Whether the group address's key is exported: while it is secured, or unlinked in ``Auto``
    mode. One that communicates in the clear because of its links or its mode is exported without
    a key; the project keeps it, so securing the address again brings back the same key."""
    if secured:
        return True
    return effective_mode(group_address) in AUTO_MODES and not group_address.links


def _has_tool_key(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return bool(ET.fromstring(raw).get("ToolKey"))
    except ET.ParseError:
        return False

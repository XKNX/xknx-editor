"""The effective KNX Data Secure state of group addresses and devices.

A device is commissioned securely ("Secure Commissioning" activated) when the project holds a Tool
Key for it (``<Security @ToolKey>``). A group address with Security ``On`` is secured and one with
``Off`` is not. In the default ``Auto`` mode it is secured when it is linked and every device linked
to it is commissioned securely: a device commissioned in the clear could not take part in its
secured communication. The state is derived from the links whenever it is needed, so a change of a
link or of a device's secure commissioning counts everywhere at once.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy.orm import Session

from ..models import ComObject, ComObjectLink, Device, GroupAddress

#: The ``@Security`` values of a group address that let its links decide (``None`` = default).
AUTO_MODES: frozenset[str | None] = frozenset({None, "Auto"})


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
        for ga_id, mode in session.query(GroupAddress.id, GroupAddress.security_mode)
        if is_secured(mode, linked.get(ga_id, ()), commissioned)
    }


def exports_key(group_address: GroupAddress, secured: bool) -> bool:
    """Whether the group address's key is exported: while it is secured, or unlinked in ``Auto``
    mode. One that communicates in the clear because of its links or its mode is exported without
    a key; the project keeps it, so securing the address again brings back the same key."""
    if secured:
        return True
    return group_address.security_mode in AUTO_MODES and not group_address.links


def _has_tool_key(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return bool(ET.fromstring(raw).get("ToolKey"))
    except ET.ParseError:
        return False

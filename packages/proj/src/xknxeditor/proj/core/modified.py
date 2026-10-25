"""A device's "Last Modified" time: when its configuration last changed.

It is stamped (UTC, as the project stores it) by a change of a parameter value, of a group
object's flags (also when undone or redone), by adding the device, and by a change of what was
downloaded to it (its "loaded" ticks: linking a group address marks its group communication not
loaded), of its serial number or MAC address, or of its application. Undoing a parameter
change leaves it, and so does a download. It is not part of the undo history: undo never sets it
back.
"""

from __future__ import annotations

import datetime
from typing import cast

from sqlalchemy.orm import Session

from xknxeditor.proj.core.events import (
    AddDevice,
    ChangeDeviceApplication,
    CompositeEvent,
    Event,
    SetComObjectFlag,
    SetDeviceCommissioning,
    SetParameter,
    UpdateDeviceApplication,
)
from xknxeditor.proj.models import ComObject, Device


def project_timestamp(moment: datetime.datetime | None = None) -> str:
    """``moment`` (now by default) as the project stores times: UTC, up to seven fractional
    digits without trailing zeros, "Z" ("2026-01-24T11:38:26.8111136Z")."""
    moment = (moment or datetime.datetime.now(datetime.UTC)).astimezone(datetime.UTC)
    fraction = f"{moment.microsecond:06d}0".rstrip("0")
    whole = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{whole}.{fraction}Z" if fraction else f"{whole}Z"


def modified_devices(event: Event, session: Session, *, reverting: bool) -> set[int]:
    """The devices whose configuration ``event`` changed when applied (or, with ``reverting``,
    when undone)."""
    if isinstance(event, CompositeEvent):
        return {
            device
            for inner in event.events
            for device in modified_devices(inner, session, reverting=reverting)
        }
    devices: set[int] = set()
    cleared = cast("dict[str, bool] | None", getattr(event, "cleared", None))
    if cleared:
        # Its group communication was marked not loaded (and loaded again on undo).
        devices |= {int(device) for device in cleared}
    if isinstance(event, SetComObjectFlag):
        com_object = session.get(ComObject, event.com_object_id)
        if com_object is not None:
            devices.add(com_object.device_id)
    device = _own_device(event, reverting=reverting)
    if device is not None:
        devices.add(device)
    return devices


def _own_device(event: Event, *, reverting: bool) -> int | None:
    """The device a parameter change, the adding of a device, a change of its application or of
    its commissioning state modified; None for other events."""
    if isinstance(event, SetParameter | AddDevice):
        return None if reverting else event.device_id
    if isinstance(event, ChangeDeviceApplication | UpdateDeviceApplication):
        return event.device_id
    if isinstance(event, SetDeviceCommissioning) and not event.download:
        return event.device_id if event.changed() else None
    return None


def stamp_modified(session: Session, event: Event, *, reverting: bool = False) -> None:
    """Set "Last Modified" of the devices ``event`` changed to now."""
    devices = modified_devices(event, session, reverting=reverting)
    if not devices:
        return
    now = project_timestamp()
    for device_id in devices:
        device = session.get(Device, device_id)
        if device is not None:
            device.last_modified = now

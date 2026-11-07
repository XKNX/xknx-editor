"""Forensic snapshots of recovered devices.

Turns the raw and decoded data read off a device into a JSON-serialisable record:
the mask version, application id, descriptive dossier, decoded group communication,
recovered parameters, and the raw code-segment and property bytes (hex). This is a faithful,
loss-free capture for archiving, diffing or debugging, independent of whether the
data was mapped into a project, and :func:`devices_from_snapshot` reads it back, so a device
can be searched again or added to a project without the bus.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .dossier import DeviceDossier
from .parameters import RecoveredParameters, RecoveryEffort
from .tables_decode import DecodedGroupObject, DecodedLink

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .recover import RecoveredDevice


def device_snapshot(device: RecoveredDevice) -> dict[str, Any]:
    """A JSON-serialisable snapshot of one recovered device."""
    return {
        "address": device.address,
        "mask_version": f"{device.mask_version:#06x}",
        "application_id": device.application_id,
        "device_address": device.device_address,
        "dossier": asdict(device.dossier),
        "group_addresses": device.group_addresses,
        "links": [
            {
                "group_address": link.group_address,
                "group_object_number": link.group_object_number,
                "sending": link.sending,
            }
            for link in device.links
        ],
        "group_objects": {
            str(number): asdict(group_object)
            for number, group_object in device.group_objects.items()
        },
        "parameters": {
            "values": device.parameters.values,
            "unknown": device.parameters.unknown,
            "lossy": device.parameters.lossy,
            "differing_bytes": device.parameters.differing_bytes,
            "differing_objects": device.parameters.differing_objects,
            "limited": device.parameters.limited,
        },
        "search_effort": device.effort.value,
        "raw_segments": {
            segment_id: data.hex()
            for segment_id, data in (device.parameter_segments or {}).items()
        },
        "raw_properties": [
            {
                "object_index": object_index,
                "property_id": property_id,
                "occurrence": occurrence,
                "data": data.hex(),
            }
            for (object_index, property_id, occurrence), data in (
                device.property_values or {}
            ).items()
        ],
    }


def devices_from_snapshot(text: str) -> list[RecoveredDevice]:
    """The devices of a snapshot document (see :func:`snapshots_json`), as they were read.

    Raises ``ValueError`` when the text is no snapshot. The group object references are left
    to the caller, which resolves them against the application in its catalog (see
    :func:`~xknxeditor.recover.recover.com_object_ref_by_number`). Snapshots written before
    the property values were kept have none.
    """
    from .recover import RecoveredDevice

    try:
        document: dict[str, Any] = json.loads(text)
        devices: list[RecoveredDevice] = []
        records: list[dict[str, Any]] = document["devices"]
        for record in records:
            parameters: dict[str, Any] = record.get("parameters") or {}
            links: list[dict[str, Any]] = record.get("links", [])
            group_objects: dict[str, dict[str, Any]] = record.get("group_objects", {})
            dossier: dict[str, Any] = record.get("dossier") or {}
            segments: dict[str, str] = record.get("raw_segments", {})
            properties: list[dict[str, Any]] = record.get("raw_properties", [])
            devices.append(
                RecoveredDevice(
                    address=str(record["address"]),
                    application_id=str(record["application_id"]),
                    device_address=record.get("device_address"),
                    group_addresses=[
                        int(ga) for ga in record.get("group_addresses", [])
                    ],
                    links=[DecodedLink(**link) for link in links],
                    group_objects={
                        int(number): DecodedGroupObject(**group_object)
                        for number, group_object in group_objects.items()
                    },
                    parameters=RecoveredParameters(
                        values=dict(parameters.get("values", {})),
                        unknown=list(parameters.get("unknown", [])),
                        lossy=list(parameters.get("lossy", [])),
                        differing_bytes=int(parameters.get("differing_bytes", 0)),
                        differing_objects=int(parameters.get("differing_objects", 0)),
                        limited=bool(parameters.get("limited", False)),
                    ),
                    mask_version=int(str(record.get("mask_version", "0")), 0),
                    dossier=DeviceDossier(**dossier),
                    parameter_segments={
                        segment_id: bytes.fromhex(data)
                        for segment_id, data in segments.items()
                    },
                    property_values={
                        (
                            entry["object_index"],
                            int(entry["property_id"]),
                            int(entry["occurrence"]),
                        ): bytes.fromhex(entry["data"])
                        for entry in properties
                    },
                    effort=RecoveryEffort(record.get("search_effort", "fast")),
                )
            )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"not a recovery snapshot: {exc}") from exc
    return devices


def snapshots_json(devices: Sequence[RecoveredDevice]) -> str:
    """Serialise recovered devices to a pretty-printed JSON snapshot document."""
    return json.dumps(
        {"devices": [device_snapshot(device) for device in devices]},
        indent=2,
        ensure_ascii=False,
    )

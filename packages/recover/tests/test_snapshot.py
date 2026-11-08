"""A snapshot holds everything read off a device, and reads back into the same device."""

from __future__ import annotations

import json

import pytest

from xknxeditor.recover import (
    DeviceDossier,
    RecoveredDevice,
    RecoveredParameters,
    RecoveryEffort,
    devices_from_snapshot,
    snapshots_json,
)
from xknxeditor.recover.tables_decode import DecodedGroupObject, DecodedLink


def _device() -> RecoveredDevice:
    return RecoveredDevice(
        address="1.1.7",
        application_id="M-0083_A-008A-28-7B00",
        device_address=0x1107,
        group_addresses=[0x0A03, 0x0A04],
        links=[DecodedLink(group_address=0x0A03, group_object_number=3, sending=True)],
        group_objects={
            3: DecodedGroupObject(
                number=3,
                priority="low",
                communication=True,
                read=False,
                write=True,
                transmit=True,
                update=False,
                read_on_init=None,
                size_code=7,
                object_size="1 Byte",
            )
        },
        parameters=RecoveredParameters(
            values={"R-1": "8"},
            unknown=["P-2", "P-3"],
            lossy=["P-3"],
            differing_bytes=2,
            differing_objects=1,
            limited=True,
        ),
        mask_version=0x07B0,
        dossier=DeviceDossier(serial_number="0083:12345678", order_info="BE-GT2Tx.01"),
        parameter_segments={"M-0083_A-008A-28-7B00_RS-04-00000": bytes([1, 2, 255])},
        property_values={
            (4, None, 54, 0): bytes([0x10, 0x20]),
            (None, 50001, 51, 1): b"\x01",
        },
        effort=RecoveryEffort.MAXIMUM,
    )


def test_a_snapshot_reads_back_into_the_same_device() -> None:
    device = _device()
    assert devices_from_snapshot(snapshots_json([device])) == [device]


def test_a_snapshot_without_property_values_still_reads() -> None:
    # Written before the property values and the search effort were kept.
    document = json.loads(snapshots_json([_device()]))
    for record in document["devices"]:
        del record["raw_properties"], record["search_effort"]
    (device,) = devices_from_snapshot(json.dumps(document))
    assert device.property_values == {}
    assert device.effort is RecoveryEffort.FAST
    assert device.parameters.values == {"R-1": "8"}


def test_a_snapshot_without_object_types_still_reads() -> None:
    # Written before the object types were kept: only objects addressed by index were read.
    document = json.loads(snapshots_json([_device()]))
    for record in document["devices"]:
        record["raw_properties"] = [
            entry
            for entry in record["raw_properties"]
            if entry["object_index"] is not None
        ]
        for entry in record["raw_properties"]:
            del entry["object_type"]
    (device,) = devices_from_snapshot(json.dumps(document))
    assert device.property_values == {(4, None, 54, 0): bytes([0x10, 0x20])}


@pytest.mark.parametrize(
    "text", ["", "{}", '{"devices": [{"address": "1.1.1"}]}', "[1]"]
)
def test_text_that_is_no_snapshot_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="not a recovery snapshot"):
        devices_from_snapshot(text)

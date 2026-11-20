"""Complete gateway restore plans built from saved physical device data."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from xknxeditor.dali.configuration import Configuration, compile_configuration, integer
from xknxeditor.dali.connection import DaliCommissioningError
from xknxeditor.dali.effects import require_effects
from xknxeditor.dali.input_configuration import input_type
from xknxeditor.dali.properties import DeviceCommand, PropertyType
from xknxeditor.dali.structs import EcgProp, EcgStatic

if TYPE_CHECKING:
    from xknxeditor.dali.commissioning import MdtDaliCommissioner


@dataclass(frozen=True)
class RestorePlan:
    configuration: Configuration
    static: bytes
    properties: bytes
    inputs: bytes
    instances: tuple[tuple[int, bytes], ...]
    gtin: bytes
    calibration: bytes


def prepare_restore(data: bytes) -> RestorePlan:
    """Require a saved physical inventory with no pending assignment edits."""
    configuration = compile_configuration(data)
    root = ET.fromstring(data)
    if root.get("type") != "e64pro" or root.get("CanRestore") != "True":
        raise DaliCommissioningError(
            "configuration has no restorable gateway inventory"
        )
    for item in root.findall("ecgs/ECG"):
        if item.get("flags") in ("2", "3", "6"):
            raise DaliCommissioningError(
                "program pending ECG assignments before restoring"
            )
    for section in ("mds", "pbs", "gis"):
        for item in root.findall(section + "/*/Instances/EtsInstance"):
            if item.get("ref-idx", "-1") != item.get("origin", "-1"):
                raise DaliCommissioningError(
                    "program pending input assignments before restoring"
                )
    physical = root.findall("devecgs/DevECG")
    if not physical and not root.findall("inputs/DevInput"):
        raise DaliCommissioningError("configuration contains no physical devices")
    static = bytearray(EcgStatic(0xFFFFFF, 0, 0, 0).encode() * 64)
    properties = bytearray(EcgProp(-1, 0, 255, 255).encode() * 64)
    gtin = bytearray(128 * 6)
    seen: set[int] = set()
    mapped = {assignment.slot: assignment for assignment in configuration.assignments}
    for item in physical:
        slot = integer(item.get("index", ""), 63)
        if slot in seen:
            raise DaliCommissioningError("duplicate physical ECG address")
        seen.add(slot)
        kind = int(item.get("type", "-1"))
        if not -1 <= kind <= 255:
            raise DaliCommissioningError("invalid physical ECG type")
        logical = integer(item.get("short-address", "255"), 255)
        group = integer(item.get("groupindex", "255"), 255)
        assignment = mapped.get(slot)
        if logical != 255 and (
            assignment is None
            or (assignment.ets_index, assignment.group_index) != (logical, group)
        ):
            raise DaliCommissioningError("physical ECG mapping is inconsistent")
        long_address = int(item.get("long-address", "FFFFFF"), 16)
        if not 0 <= long_address <= 0xFFFFFF:
            raise DaliCommissioningError("invalid DALI long address")
        static[slot * 8 : slot * 8 + 8] = EcgStatic(
            long_address,
            integer(item.get("min-value", "0"), 255),
            integer(item.get("min-ctemp", "0"), 65535),
            integer(item.get("max-ctemp", "0"), 65535),
        ).encode()
        properties[slot * 4 : slot * 4 + 4] = EcgProp(
            kind,
            integer(item.get("subtype", "0"), 255),
            group,
            logical,
        ).encode()
        gtin[slot * 6 : slot * 6 + 6] = integer(
            item.get("gtin", "0"), 2**48 - 1
        ).to_bytes(6, "big")
    if not mapped.keys() <= seen:
        raise DaliCommissioningError("mapped ECG missing from physical inventory")
    inputs = bytearray(b"\xff" * 384)
    instances: list[tuple[int, bytes]] = []
    seen.clear()
    assert configuration.inputs is not None
    for item in root.findall("inputs/DevInput"):
        slot = integer(item.get("index", ""), 63)
        if slot in seen:
            raise DaliCommissioningError("duplicate physical input address")
        seen.add(slot)
        start = integer(item.get("start-index", ""), 255)
        count = integer(item.get("noi", ""), 254, 1)
        long_address = int(item.get("long-address", ""), 16)
        if not 0 <= long_address <= 0xFFFFFF:
            raise DaliCommissioningError("invalid input long address")
        inputs[slot * 6 : slot * 6 + 6] = (
            long_address.to_bytes(3, "big") + bytes([count]) + struct.pack("<H", start)
        )
        rows = {
            integer(instance.get("index", ""), 255): instance
            for instance in item.findall("Instances/DevInputInstance")
        }
        if set(rows) != set(range(start, start + count)):
            raise DaliCommissioningError("incomplete input instance inventory")
        records: list[bytes] = []
        for index in range(start, start + count):
            row = rows[index]
            mapping = configuration.inputs[index * 3 : index * 3 + 3]
            if mapping[0] == 255:
                mapping = bytes((255, 0, 0))
            records.append(
                bytes(
                    (
                        slot,
                        input_type(row),
                        integer(row.get("number", ""), 254),
                        integer(row.get("resolution", "0"), 255),
                    )
                )
                + mapping
                + b"\0"
            )
        instances.append((start + 1, b"".join(records)))
        offset = (64 + slot) * 6
        gtin[offset : offset + 6] = integer(item.get("gtin", "0"), 2**48 - 1).to_bytes(
            6, "big"
        )
    calibration = bytearray(b"\xff" * 80)
    for item in root.findall("mds/MdInput"):
        index = integer(item.get("index", ""), 7)
        minimum = integer(item.get("mmin", "65535"), 65535)
        maximum = integer(item.get("mmax", "65535"), 65535)
        if maximum == 65535 or minimum == maximum:
            continue
        calibration[index * 10 : index * 10 + 10] = struct.pack(
            ">BHHBHH",
            round(integer(item.get("vmin", "0"), 100) * 255 / 100),
            minimum,
            integer(item.get("bmin", "0"), 65535),
            round(integer(item.get("vmax", "0"), 100) * 255 / 100),
            maximum,
            integer(item.get("bmax", "0"), 65535),
        )
    return RestorePlan(
        configuration,
        bytes(static),
        bytes(properties),
        bytes(inputs),
        tuple(instances),
        bytes(gtin),
        bytes(calibration),
    )


async def restore(commissioner: MdtDaliCommissioner, plan: RestorePlan) -> None:
    connection = commissioner.connection
    require_effects(connection)
    await connection.send_command(DeviceCommand.PREPARE_RESTORE)
    await asyncio.sleep(0.1)
    await connection.write_table(PropertyType.EVG_STATIC, plan.static, 64)
    await connection.write_table(PropertyType.EVG_PROP, plan.properties, 64)
    await asyncio.sleep(1)
    if plan.configuration.assignments:
        await commissioner.commission(plan.configuration.assignments)
    await connection.write_table(PropertyType.INPUT_TABLE, plan.inputs, 64)
    for start, records in plan.instances:
        await connection.write_table(
            PropertyType.INSTANCE_TABLE, records, len(records) // 8, start_index=start
        )
    await connection.write_table(PropertyType.GTIN, plan.gtin, 128)
    await connection.write_table(PropertyType.CONSTANT_LIGHT, plan.calibration, 8)
    await commissioner.write_configuration(plan.configuration)
    for prop, expected, count in (
        (PropertyType.EVG_STATIC, plan.static, 64),
        (PropertyType.INPUT_TABLE, plan.inputs, 64),
        (PropertyType.GTIN, plan.gtin, 128),
        (PropertyType.CONSTANT_LIGHT, plan.calibration, 8),
    ):
        if await connection.read_table(prop, count) != expected:
            raise DaliCommissioningError(
                f"restore readback mismatch for property {int(prop)}"
            )
    await connection.send_command(DeviceCommand.RESET_DEVICE)

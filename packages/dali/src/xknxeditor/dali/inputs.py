"""Input discovery, assignment and live instance status."""

from __future__ import annotations

from dataclasses import dataclass

from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.effects import require_effects
from xknxeditor.dali.properties import DeviceCommand, PropertyType
from xknxeditor.dali.structs import decode_table


@dataclass(frozen=True)
class InputInstance:
    index: int
    address: int
    kind: int
    number: int
    resolution: int
    controller: int
    detail: int
    count: int
    status: int
    value: int


@dataclass(frozen=True)
class InputDevice:
    address: int
    long_address: int
    start: int
    instances: tuple[InputInstance, ...]


@dataclass(frozen=True, order=True)
class InputIdentity:
    index: int
    address: int
    long_address: int
    kind: int
    number: int
    resolution: int


async def read_inputs(connection: MdtDaliConnection) -> tuple[InputDevice, ...]:
    require_effects(connection)
    table = decode_table(await connection.read_table(PropertyType.INPUT_TABLE, 64), 6)
    devices: list[InputDevice] = []
    seen: set[int] = set()
    for address, record in enumerate(table):
        count, start = record[3], int.from_bytes(record[4:6], "little")
        if count in (0, 255):
            continue
        if (
            start >= 255
            or start + count > 256
            or any(i in seen for i in range(start, start + count))
        ):
            raise DaliCommissioningError("invalid input instance range")
        seen.update(range(start, start + count))
        props = decode_table(
            await connection.read_table(
                PropertyType.INSTANCE_TABLE, count, start_index=start + 1
            ),
            8,
        )
        states = decode_table(
            await connection.read_table(
                PropertyType.INPUT_STATUS, count, start_index=start + 1
            ),
            5,
        )
        instances: list[InputInstance] = []
        numbers: set[int] = set()
        for offset, (prop, state) in enumerate(zip(props, states, strict=True)):
            if prop[0] != address or prop[2] in numbers:
                raise DaliCommissioningError(
                    "input address mismatch or duplicate instance number"
                )
            numbers.add(prop[2])
            instances.append(
                InputInstance(
                    start + offset,
                    address,
                    prop[1],
                    prop[2],
                    prop[3],
                    prop[4],
                    prop[5],
                    prop[6],
                    state[0],
                    int.from_bytes(state[1:3], "big"),
                )
            )
        devices.append(
            InputDevice(
                address, int.from_bytes(record[:3], "big"), start, tuple(instances)
            )
        )
    return tuple(devices)


def validate_assignments(assignments: bytes) -> None:
    if len(assignments) != 256 * 3:
        raise DaliCommissioningError("invalid input assignment table size")
    for index, record in enumerate(decode_table(assignments, 3)):
        if record[0] not in (254, 255) and (
            record[0] & 7 > 2 or record[0] >> 3 >= 8 or index >= 254
        ):
            raise DaliCommissioningError("invalid input assignment")


async def check_input_inventory(
    connection: MdtDaliConnection,
    assignments: bytes,
    expected: tuple[InputIdentity, ...] | None = None,
) -> None:
    """Reject disappeared or replaced input instances before writing mappings."""
    validate_assignments(assignments)
    devices = await read_inputs(connection)
    received = tuple(
        InputIdentity(
            instance.index,
            device.address,
            device.long_address,
            instance.kind,
            instance.number,
            instance.resolution,
        )
        for device in devices
        for instance in device.instances
    )
    indexes = {item.index for item in received}
    if (expected is not None and sorted(received) != sorted(expected)) or any(
        record[0] != 255 and index not in indexes
        for index, record in enumerate(decode_table(assignments, 3))
    ):
        raise DaliCommissioningError("input inventory changed; read inputs again")


async def write_inputs(
    connection: MdtDaliConnection,
    assignments: bytes,
    *,
    expected_inventory: tuple[InputIdentity, ...] | None = None,
) -> tuple[InputDevice, ...]:
    """Write the complete mapping and verify the linked instance readback."""
    require_effects(connection)
    await check_input_inventory(connection, assignments, expected_inventory)
    await connection.write_table(PropertyType.INPUT_COMMISSIONING, assignments, 256)
    await connection.run_command(DeviceCommand.INPUT_SYNC, timeout=180)
    devices = await read_inputs(connection)
    received = {
        instance.index: instance for device in devices for instance in device.instances
    }
    for index, record in enumerate(decode_table(assignments, 3)):
        instance = received.get(index)
        if record[0] < 254 and (
            instance is None
            or (instance.controller, instance.detail, instance.count) != tuple(record)
        ):
            raise DaliCommissioningError(
                "input assignment readback does not match requested mapping"
            )
        if record[0] == 254 and instance is not None and instance.controller != 255:
            raise DaliCommissioningError(
                "input removal readback does not match requested mapping"
            )
    return devices

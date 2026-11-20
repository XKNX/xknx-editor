"""Gateway diagnostics and maintenance operations."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Literal

from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.effects import require_effects
from xknxeditor.dali.properties import DeviceCommand, PropertyType

EmergencyKind = Literal["function", "duration", "battery"]
NAME_TABLES = {
    "ecgs": (PropertyType.ECG_NAME, 64),
    "groups": (PropertyType.GROUP_NAME, 16),
    "mds": (PropertyType.INPUT_NAME, 8),
}
CALIBRATION_LEVELS = (*range(10, 30, 5), *range(70, 101, 5))


def validate_maintenance(
    action: str, index: int, value: int, secondary: int, bank: int, section: str
) -> None:
    if action in ("function", "duration", "battery"):
        valid = 0 <= index < 64
    elif action in ("read_calibration", "set_calibration", "save_calibration"):
        valid = 0 <= index < 8
        if action != "read_calibration":
            valid = valid and value in CALIBRATION_LEVELS
        if action == "save_calibration":
            valid = valid and 0 <= secondary <= 65535
    elif action in ("read_memory", "write_memory"):
        valid = 0 <= index < 8 and 2 <= bank <= 199 and 0 <= secondary <= 255
        if action == "write_memory":
            valid = valid and 0 <= value <= 255
    elif action in ("read_names", "write_names"):
        valid = section in NAME_TABLES
    else:
        valid = action in ("read_motion", "inhibit", "replace")
    if not valid:
        raise DaliCommissioningError("invalid maintenance operation or arguments")


async def emergency_test(
    connection: MdtDaliConnection, slot: int, kind: EmergencyKind
) -> None:
    require_effects(connection)
    if not 0 <= slot < 64 or kind not in ("function", "duration", "battery"):
        raise DaliCommissioningError("invalid emergency test or ECG slot")
    prop = await connection.read_table(PropertyType.EVG_PROP, 1, start_index=slot + 1)
    if prop[0] not in (1, 129):
        raise DaliCommissioningError("selected ECG is not an emergency converter")
    status = await connection.read_table(
        PropertyType.CONVERTER_STATUS, 1, start_index=slot + 1
    )
    if kind != "battery" and (status[0] & 15 in (7, 8) or status[0] & 192):
        raise DaliCommissioningError(
            "an emergency test is already scheduled or running"
        )
    cmd = {
        "function": DeviceCommand.CONVERTER_TEST,
        "duration": DeviceCommand.LONG_DURATION_TEST,
        "battery": DeviceCommand.BATTERY_TEST,
    }[kind]
    await connection.send_command(cmd, slot)


@dataclass(frozen=True)
class LightCalibration:
    minimum_level: int
    minimum_lux: int
    minimum_sensor: int
    maximum_level: int
    maximum_lux: int
    maximum_sensor: int


async def read_calibration(
    connection: MdtDaliConnection, index: int
) -> LightCalibration:
    require_effects(connection)
    if not 0 <= index < 8:
        raise DaliCommissioningError("invalid motion controller index")
    data = await connection.read_table(
        PropertyType.CONSTANT_LIGHT, 1, start_index=index + 1
    )
    low, lux_low, sensor_low, high, lux_high, sensor_high = struct.unpack(
        ">BHHBHH", data
    )
    return LightCalibration(
        round(low * 100 / 255),
        lux_low,
        sensor_low,
        round(high * 100 / 255),
        lux_high,
        sensor_high,
    )


async def set_calibration_level(
    connection: MdtDaliConnection, index: int, percent: int
) -> None:
    require_effects(connection)
    if not 0 <= index < 8 or percent not in CALIBRATION_LEVELS:
        raise DaliCommissioningError(
            "calibration level must be 10-25 or 70-100 in steps of 5"
        )
    await connection.send_command(
        DeviceCommand.SET_CALIBRATION_VALUE, index, round(percent * 255 / 100)
    )


async def save_calibration(
    connection: MdtDaliConnection, index: int, percent: int, lux: int
) -> None:
    require_effects(connection)
    if not 0 <= index < 8 or percent not in CALIBRATION_LEVELS or not 0 <= lux <= 65535:
        raise DaliCommissioningError("invalid calibration value")
    data = (
        bytes((48, round(percent * 255 / 100), 0))
        + struct.pack("<H", lux)
        + bytes((3, index, 0))
    )
    await connection.write_table(PropertyType.EXT_COMMAND, data, 1)


async def memory_bank(
    connection: MdtDaliConnection,
    index: int,
    bank: int,
    address: int,
    value: int | None = None,
    *,
    lock: bool = False,
) -> int:
    """Read or write one byte of a motion controller's custom memory bank."""
    require_effects(connection)
    if (
        not 0 <= index < 8
        or not 2 <= bank <= 199
        or not 0 <= address <= 255
        or (value is not None and not 0 <= value <= 255)
    ):
        raise DaliCommissioningError("invalid memory bank request")
    data = bytes(
        (
            34 if value is None else 33,
            bank,
            address,
            128 if value is None else int(lock),
            value or 0,
            3,
            index,
            0,
        )
    )
    await connection.write_table(PropertyType.EXT_COMMAND, data, 1)
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
        response = await connection.read_table(PropertyType.EXT_COMMAND, 1)
        if response[0] == 255:
            raise DaliCommissioningError("memory bank operation failed")
        if response[0] == 0:
            return response[4]
    raise DaliCommissioningError("memory bank operation timed out")


async def read_names(connection: MdtDaliConnection, section: str) -> list[str]:
    require_effects(connection)
    if section not in NAME_TABLES:
        raise DaliCommissioningError("unsupported device name category")
    prop, count = NAME_TABLES[section]
    data = await connection.read_table(prop, count * 2)
    try:
        return [
            data[offset : offset + 20].split(b"\0", 1)[0].decode("utf-8")
            for offset in range(0, len(data), 20)
        ]
    except UnicodeDecodeError as exc:
        raise DaliCommissioningError("invalid UTF-8 device name") from exc


def encode_names(section: str, names: list[str]) -> bytes:
    if section not in NAME_TABLES or len(names) != NAME_TABLES[section][1]:
        raise DaliCommissioningError("invalid device name table")
    encoded = [name.encode("utf-8") for name in names]
    if any(len(name) > 20 or b"\0" in name for name in encoded):
        raise DaliCommissioningError(
            "device names must fit 20 UTF-8 bytes without truncation"
        )
    return b"".join(name.ljust(20, b"\0") for name in encoded)


async def write_names(
    connection: MdtDaliConnection, section: str, names: list[str]
) -> None:
    require_effects(connection)
    data = encode_names(section, names)
    prop, count = NAME_TABLES[section]
    await connection.write_table(prop, data, count * 2)
    if await read_names(connection, section) != names:
        raise DaliCommissioningError("device name readback does not match")


@dataclass(frozen=True)
class MotionState:
    index: int
    kind: int
    error: int
    motion: int
    brightness: int
    status: int
    movement_flags: int
    brightness_flags: int
    setpoint: int
    output: int


async def read_motion(connection: MdtDaliConnection) -> tuple[MotionState, ...]:
    require_effects(connection)
    data = await connection.read_table(PropertyType.MOTION_INPUT, 8)
    return tuple(
        MotionState(
            i,
            data[o] & 15,
            data[o] & 240,
            data[o + 1],
            int.from_bytes(data[o + 2 : o + 4], "little"),
            data[o + 4],
            data[o + 5],
            data[o + 6],
            int.from_bytes(data[o + 7 : o + 9], "little"),
            data[o + 9],
        )
        for i, o in enumerate(range(0, len(data), 10))
    )


async def maintenance(
    connection: MdtDaliConnection,
    action: str,
    index: int,
    value: int,
    secondary: int,
    bank: int,
    lock: bool,
    section: str,
    names: list[str] | None,
) -> dict[str, object]:
    from dataclasses import asdict

    require_effects(connection)
    validate_maintenance(action, index, value, secondary, bank, section)
    if action in ("function", "duration", "battery"):
        await emergency_test(connection, index, action)
        return {"started": action, "slot": index}
    if action == "read_calibration":
        return asdict(await read_calibration(connection, index))
    if action == "set_calibration":
        await set_calibration_level(connection, index, value)
        return {"level": value}
    if action == "save_calibration":
        await save_calibration(connection, index, value, secondary)
        actual = await read_calibration(connection, index)
        level, lux = (
            (actual.minimum_level, actual.minimum_lux)
            if value <= 25
            else (actual.maximum_level, actual.maximum_lux)
        )
        if (level, lux) != (value, secondary):
            raise DaliCommissioningError("calibration readback does not match")
        return asdict(actual)
    if action == "read_memory":
        return {"value": await memory_bank(connection, index, bank, secondary)}
    if action == "write_memory":
        await memory_bank(connection, index, bank, secondary, value, lock=lock)
        actual = await memory_bank(connection, index, bank, secondary)
        if actual != value:
            raise DaliCommissioningError("memory bank readback does not match")
        return {"value": actual}
    if action == "read_names":
        return {"names": await read_names(connection, section)}
    if action == "write_names":
        if names is None:
            raise DaliCommissioningError("no device names supplied")
        await write_names(connection, section, names)
        return {"names": names}
    if action == "read_motion":
        return {
            "controllers": [asdict(state) for state in await read_motion(connection)]
        }
    if action == "inhibit":
        await connection.send_command(DeviceCommand.CONVERTER_INHIBIT)
        return {"sent": action}
    if action == "replace":
        result = await connection.run_command(
            DeviceCommand.EASY_REPLACEMENT, timeout=60
        )
        return {"result": result.param1, "detail": result.param2}
    raise DaliCommissioningError("unknown maintenance operation")

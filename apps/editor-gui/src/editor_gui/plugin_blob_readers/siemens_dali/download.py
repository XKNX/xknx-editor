"""Compile and download classic Siemens DALI configuration."""

from __future__ import annotations

import asyncio
import math
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from editor_gui.plugin_blob_readers.siemens_dali.encoder import encode_date, encode_time
from editor_gui.plugin_blob_readers.siemens_dali.layout import (
    Property,
    enum_values,
    line_count,
    properties,
)
from xknxeditor.dali.siemens import read_table
from xknxeditor.download import load_state

if TYPE_CHECKING:
    from xknxeditor.download import DeviceProgrammer

_NS = "{http://www.siemens.com/knx/N141_21_Parameters}"


@dataclass(frozen=True, slots=True)
class PropertyWrite:
    """A complete property array, with its element count."""

    odx: int
    pid: int
    data: bytes
    count: int


@dataclass(frozen=True, slots=True)
class Configuration:
    """Validated configuration ready for the bus."""

    application_id: bytes
    lines: int
    writes: tuple[PropertyWrite, ...]


def _encode(prop: Property, value: str) -> bytes:
    if prop.kind == "hex":
        result = bytes.fromhex(value)
    elif prop.kind == "date":
        result = encode_date(value)
    elif prop.kind == "time":
        result = encode_time(value)
    elif prop.kind == "float":
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Invalid Siemens {prop.leaf} value")
        result = struct.pack("!f", number)
    else:
        number = (
            enum_values(prop.kind[5:])[value]
            if prop.kind.startswith("enum:")
            else int(value)
        )
        result = number.to_bytes(prop.size, "big", signed=prop.kind == "int")
    if len(result) != prop.size:
        raise ValueError(f"Invalid Siemens {prop.leaf} length")
    if prop.leaf.endswith("ShortAddress") and result[0] not in (*range(64), 255):
        raise ValueError("Invalid DALI short address")
    if prop.leaf.endswith("GroupAddress") and result[0] not in (*range(16), 255):
        raise ValueError("Invalid DALI group address")
    return result


def compile_configuration(blob_xml: bytes, app_id: str) -> Configuration:
    """Compile every writable container before starting a bus operation.

    Physical discovery caches and project-only descriptions are not property images.
    The scene overwrite setting also applies to a complete parameter download.
    """
    lines = line_count(app_id)
    parts = app_id.upper().split("-")
    if not lines or len(parts) < 4 or len(parts[3]) != 2:
        raise ValueError("Unsupported Siemens DALI application")
    application_id = bytes.fromhex("0001" + parts[2] + parts[3])
    root = ET.fromstring(zlib.decompress(blob_xml, -15).decode("utf-16-le"))
    blobs = list(root.iter(_NS + "Blob"))
    if len(blobs) != 1:
        raise ValueError("Expected one current Siemens DALI configuration")
    blob = blobs[0]
    overwrite = blob.find(_NS + "ScenePluginInfo/" + _NS + "ScenePluginInfoOverwrite")
    scene_mode = overwrite.get("Value", "true") if overwrite is not None else "true"
    if scene_mode not in ("true", "false", "1", "0"):
        raise ValueError("Invalid Siemens scene overwrite setting")
    stamp = datetime.now()
    writes: list[PropertyWrite] = []
    for prop in properties():
        if prop.container == "ConnectedSensors":
            continue
        if prop.container == "Scenes" and scene_mode in ("false", "0"):
            continue
        containers = blob.findall(_NS + prop.container)
        for line in range(lines if prop.per_line else 1):
            if line >= len(containers):
                raise ValueError(f"Missing Siemens {prop.container} line {line + 1}")
            container = containers[line]
            items = container.findall(_NS + prop.item) if prop.item else [container]
            if len(items) != prop.count:
                raise ValueError(f"Incomplete Siemens {prop.container}/{prop.item}")
            values: list[bytes] = []
            for item in items:
                leaves = item.findall(_NS + prop.leaf)
                if len(leaves) <= prop.occurrence:
                    raise ValueError(f"Missing Siemens {prop.leaf}")
                value = leaves[prop.occurrence].get("Value")
                if value is None:
                    raise ValueError(f"Missing Siemens {prop.leaf} value")
                try:
                    encoded = _encode(prop, value)
                except (ValueError, KeyError, OverflowError) as exc:
                    raise ValueError(f"Invalid Siemens {prop.leaf}: {value!r}") from exc
                if prop.pid == 51:
                    encoded = encode_time(stamp)
                elif prop.pid == 52:
                    encoded = encode_date(stamp)
                values.append(encoded)
            writes.append(
                PropertyWrite(
                    prop.object_index + line, prop.pid, b"".join(values), prop.count
                )
            )
    return Configuration(application_id, lines, tuple(writes))


async def _update_devices(
    programmer: DeviceProgrammer, line: int, command: int
) -> None:
    odx = 12 + line
    async with asyncio.timeout(300):
        result = await programmer.function_property_raw(
            odx, 52, bytes([command]), command=True
        )
        if len(result) < 2 or result[0] != 0 or result[1] not in (1, 2):
            raise ValueError(
                f"Siemens DALI line {line + 1} rejected update {command}: {result.hex()}"
            )
        while True:
            await asyncio.sleep(1)
            result = await programmer.function_property_raw(
                odx, 52, b"\x00", command=False
            )
            if len(result) < 2 or result[0] != 0:
                raise ValueError(
                    f"Invalid Siemens DALI update response: {result.hex()}"
                )
            if result[1] == 2:
                return
            if result[1] != 1 or len(result) < 5:
                raise ValueError(
                    f"Siemens DALI line {line + 1} update {command} failed: {result.hex()}"
                )


async def program_configuration(
    programmer: DeviceProgrammer, config: Configuration
) -> int:
    """Write parameters, apply them to control gear and sensors, and confirm completion.

    Requires the matching KNX application to be present. Its group communication
    tables are retained. A failed stage never sends the download-finished command.
    Returns the number of configuration property arrays written.
    """
    identity = await read_table(programmer, 4, 246, 1, 7)
    variant = 0x058B0301 if config.lines == 1 else 0x058B0101
    if int.from_bytes(identity[:4], "big") != variant or identity[4:6] != b"\x04\x05":
        raise ValueError(
            "Unsupported Siemens DALI firmware variant or interface version"
        )
    if await programmer.read_property(4, 13) != config.application_id:
        raise ValueError(
            "Program the matching KNX application before its DALI configuration"
        )
    if await programmer.read_load_state(4) not in (
        load_state.LoadState.LOADED,
        load_state.LoadState.LOADING,
    ):
        raise ValueError("Siemens KNX application is not loaded")
    # Validate every element size before changing the device's load state.
    programmer.property_chunk_size(10)
    for write in config.writes:
        programmer.property_chunk_size(len(write.data) // write.count)
    await programmer.write_property(6, 205, b"\x5a")
    await programmer.send_load_event(
        4, load_state.start_loading(), load_state.LoadState.LOADING
    )
    objects = dict.fromkeys(w.odx for w in config.writes)
    for odx in objects:
        if odx:
            await programmer.write_property(odx, 53, load_state.unload(), verify=False)
            await programmer.write_property(
                odx, 53, load_state.start_loading(), verify=False
            )
        for write in config.writes:
            if write.odx == odx:
                await programmer.write_property(
                    odx, write.pid, write.data, count=write.count
                )
        if odx:
            await programmer.write_property(
                odx, 53, load_state.load_complete(), verify=False
            )
    for odx in objects:
        if odx and await programmer.read_property(odx, 53) != b"\x01":
            raise ValueError(f"Siemens DALI object {odx} did not finish loading")
    await programmer.write_property(6, 205, b"\x09")
    await programmer.send_load_event(
        4, load_state.load_complete(), load_state.LoadState.LOADED
    )
    for line in range(config.lines):
        await _update_devices(programmer, line, 3)
        await _update_devices(programmer, line, 4)
    await programmer.write_property(6, 205, b"\x10")
    return len(config.writes)

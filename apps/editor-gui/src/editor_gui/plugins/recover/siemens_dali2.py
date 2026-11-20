"""Recover cached Siemens DALI-2 discovery data alongside KNX parameters."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.download import DeviceProgrammer
    from xknxeditor.prod import Application
    from xknxeditor.recover import RecoveredDevice
    from xknxeditor.recover.recover import AddinReader

_LOG = logging.getLogger(__name__)
_CAPTURE = "siemens-dali2-recovery.json"
_BLOB = "Dali2DcaBinaryData"
_APP = re.compile(r"M-0001_A-9A1([BC])-(02|03|30)-[0-9A-F]{4}(?:-.*)?$")
_MAX_IMAGE = 1024 * 1024


def supports(app_id: str | None) -> bool:
    """Whether the application uses the supported DALI-2 discovery format."""
    return bool(app_id and _APP.fullmatch(app_id.upper()))


def _line_count(app_id: str) -> int:
    match = _APP.fullmatch(app_id.upper())
    if match is None:
        raise ValueError("Unsupported Siemens DALI-2 application")
    return 2 if match[1] == "C" else 1


async def _read_image(
    programmer: DeviceProgrammer, line: int, property_id: int
) -> bytes | None:
    pointer = await programmer.read_property(16 + line, property_id)
    if len(pointer) != 4:
        raise ValueError("Incomplete Siemens DALI-2 image pointer")
    address = int.from_bytes(pointer, "big")
    if not address:
        _LOG.warning(
            "Siemens DALI-2 discovery cache unavailable: line=%s property=%s",
            line + 1,
            property_id,
        )
        return None
    if address + 4 > _MAX_IMAGE:
        raise ValueError("Invalid Siemens DALI-2 image address")
    size_data = await programmer.read_user_memory(address, 4)
    if len(size_data) != 4:
        raise ValueError("Incomplete Siemens DALI-2 image size")
    size = int.from_bytes(size_data, "little")
    if not size or address + 4 + size > _MAX_IMAGE:
        raise ValueError("Invalid Siemens DALI-2 image size")
    data = await programmer.read_user_memory(address + 4, size)
    if len(data) != size:
        raise ValueError("Incomplete Siemens DALI-2 discovery image")
    if await programmer.read_property(16 + line, property_id) != pointer:
        raise ValueError("Siemens DALI-2 discovery cache changed during recovery")
    return data


def addin_reader(application: Application) -> AddinReader | None:
    """Read existing discovery caches without starting scans or changing addresses."""
    if not supports(application.id):
        return None
    count = _line_count(application.id)

    async def read(programmer: DeviceProgrammer) -> dict[str, dict[str, bytes]]:
        channels: list[dict[str, str | None]] = []
        for line in range(count):
            ecgs = await _read_image(programmer, line, 55)
            sensors = await _read_image(programmer, line, 56)
            if ecgs is not None:
                _ecg_data(ecgs)
            if sensors is not None:
                _sensor_data(sensors, line)
            channels.append(
                {
                    "ecgs": ecgs.hex() if ecgs is not None else None,
                    "sensors": sensors.hex() if sensors is not None else None,
                }
            )
        return {application.id: {_CAPTURE: json.dumps(channels).encode()}}

    return read


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        value = data[offset]
        offset += 1
        if shift == 63 and value > 1:
            break
        result |= (value & 127) << shift
        if not value & 128:
            return result, offset
    raise ValueError("Invalid Siemens DALI-2 integer")


@dataclass
class _Message:
    fields: dict[int, list[tuple[int, int | bytes]]]

    @classmethod
    def parse(cls, data: bytes) -> _Message:
        fields: dict[int, list[tuple[int, int | bytes]]] = {}
        offset = 0
        while offset < len(data):
            key, offset = _varint(data, offset)
            number, wire = key >> 3, key & 7
            if not 1 <= number < 1 << 29:
                raise ValueError("Invalid Siemens DALI-2 field")
            value: int | bytes
            if wire == 0:
                value, offset = _varint(data, offset)
            elif wire in (1, 2, 5):
                if wire == 2:
                    size, offset = _varint(data, offset)
                else:
                    size = 8 if wire == 1 else 4
                if offset + size > len(data):
                    raise ValueError("Truncated Siemens DALI-2 field")
                value, offset = data[offset : offset + size], offset + size
            else:
                raise ValueError("Unsupported Siemens DALI-2 field encoding")
            fields.setdefault(number, []).append((wire, value))
        return cls(fields)

    def uint(self, field: int, maximum: int = 0xFFFFFFFF) -> int:
        values = self.fields.get(field, [(0, 0)])
        wire, value = values[-1]
        if wire != 0 or not isinstance(value, int) or not 0 <= value <= maximum:
            raise ValueError("Invalid Siemens DALI-2 field value")
        return value

    def children(self, field: int) -> list[_Message]:
        children: list[_Message] = []
        for wire, value in self.fields.get(field, []):
            if wire != 2 or not isinstance(value, bytes):
                raise ValueError("Invalid Siemens DALI-2 nested message")
            children.append(self.parse(value))
        return children

    def integers(self, field: int) -> list[int]:
        result: list[int] = []
        for wire, value in self.fields.get(field, []):
            if wire == 0 and isinstance(value, int):
                result.append(value)
            elif wire == 2 and isinstance(value, bytes):
                offset = 0
                while offset < len(value):
                    number, offset = _varint(value, offset)
                    result.append(number)
            else:
                raise ValueError("Invalid Siemens DALI-2 device type encoding")
        if any(value > 255 for value in result):
            raise ValueError("Invalid Siemens DALI-2 device type")
        return result


def _scan(data: bytes) -> tuple[_Message, list[_Message]]:
    message = _Message.parse(data)
    devices = message.children(2)
    error = message.uint(3)
    if error not in (0, 3) or (error == 3 and devices) or len(devices) > 64:
        raise ValueError("Incomplete Siemens DALI-2 discovery result")
    addresses = [row.uint(2, 63) for row in devices]
    if len(set(addresses)) != len(addresses):
        raise ValueError("Duplicate Siemens DALI-2 short address")
    return message, devices


def _ecg_data(data: bytes) -> dict[str, Any]:
    _, devices = _scan(data)
    result: dict[str, Any] = {}
    for row in devices:
        row.uint(3, 0xFFFFFF)
        level = row.uint(6, 255)
        result[str(row.uint(2))] = {
            "EcgName": "",
            "MinDimmValue": str((level * 100 + 127) // 255),
            "TypeOfEcgList": row.integers(5),
            "SearchFlags": row.uint(1, 255),
        }
    return result


def _sensor_data(data: bytes, line: int) -> dict[str, Any]:
    message, devices = _scan(data)
    sensors: dict[str, Any] = {}
    for row in devices:
        address, random, flags = row.uint(2), row.uint(3, 0xFFFFFF), row.uint(1, 255)
        instances: dict[str, Any] = {}
        for child in row.children(6):
            number = child.uint(1, 255)
            if number == 255 or str(number) in instances:
                _LOG.warning(
                    "Ignoring invalid Siemens DALI-2 sensor instance: line=%s address=%s instance=%s",
                    line + 1,
                    address,
                    number,
                )
                continue
            instances[str(number)] = {
                "InstanceNumber": number,
                "InstanceType": {"InstanceNumber": child.uint(2, 255)},
                "InstanceResolution": child.uint(3),
                "NameFromEts": "",
                "LinkedToSensorNumber": 255,
                "PhysicalSensorNumber": address or 64,
                "PhysicalSensorRandomAddress": random,
                "PhysicalSensorSearchFlags": flags,
            }
        sensors[str(address)] = {
            "ShortAddress": address,
            "Random": random,
            "SearchFlags": flags,
            "Version": row.uint(4),
            "ListOfInstances": instances,
            "NameFromEts": "",
            "HasUnlinkedInstances": bool(instances),
        }
    return {
        "LineIndex": line,
        "InterfaceId": message.uint(1),
        "FoundSensorsWithInstancesSortedByShortAddress": sensors,
    }


def _link_sensors(data: dict[str, Any], device: Device, line: int) -> None:
    ui = device.dynamic_ui
    if ui is None:
        return
    sensors = data["FoundSensorsWithInstancesSortedByShortAddress"]

    def value(name: str) -> int | None:
        ref = ui.find_parameter_ref("name", name)
        raw = ui.get_value(ref) if ref is not None else None
        return int(raw) if raw is not None else None

    for prefix, kind in (("BSens", 4), ("PSens", 3), ("Key", 1)):
        for index in range(64):
            base = f"DaliLine.{line}.{prefix}Mapping.{index}."
            address = value(base + "ShortAddress")
            channel = value(base + "ChannelIdx")
            number = value(base + "InstanceNumber")
            if address is None or channel is None or not 0 <= channel < 64:
                continue
            sensor = sensors.get(str(address))
            if sensor is None or value(base + "Rnd") != sensor["Random"]:
                continue
            instance = sensor["ListOfInstances"].get(str(number))
            if instance is None or instance["InstanceType"]["InstanceNumber"] != kind:
                continue
            if instance["LinkedToSensorNumber"] != 255:
                raise ValueError("Duplicate recovered Siemens DALI-2 input assignment")
            instance["LinkedToSensorNumber"] = channel + 1
    for sensor in sensors.values():
        sensor["HasUnlinkedInstances"] = any(
            instance["LinkedToSensorNumber"] == 255
            for instance in sensor["ListOfInstances"].values()
        )


def apply_recovered(
    project: ProjectService, device: Device, recovered: RecoveredDevice
) -> None:
    """Persist cached runtime metadata; configuration stays in recovered KNX parameters."""
    app = device.app
    if app is None or not supports(app.id) or not recovered.addin_data:
        return
    if set(recovered.addin_data) != {app.id} or set(recovered.addin_data[app.id]) != {
        _CAPTURE
    }:
        raise ValueError(
            "Recovered Siemens DALI-2 data does not match this application"
        )
    raw: object = json.loads(recovered.addin_data[app.id][_CAPTURE])
    if not isinstance(raw, list):
        raise ValueError("Incomplete recovered Siemens DALI-2 lines")
    channels = cast(list[object], raw)
    if len(channels) != _line_count(app.id):
        raise ValueError("Incomplete recovered Siemens DALI-2 lines")
    payload: dict[str, Any] = {}
    for line, entry in enumerate(channels):
        if not isinstance(entry, dict):
            raise ValueError("Invalid recovered Siemens DALI-2 cache")
        row = cast(dict[str, object], entry)
        if set(row) != {"ecgs", "sensors"}:
            raise ValueError("Incomplete recovered Siemens DALI-2 cache")
        for field in ("ecgs", "sensors"):
            value = row[field]
            if value is None:
                continue
            if not isinstance(value, str) or not value or len(value) > 2 * _MAX_IMAGE:
                raise ValueError("Invalid recovered Siemens DALI-2 image")
            try:
                data = bytes.fromhex(value)
            except ValueError as exc:
                raise ValueError("Invalid recovered Siemens DALI-2 image") from exc
            if field == "ecgs":
                payload[f"RuntimeDataEcgLine{line + 1}"] = _ecg_data(data)
            else:
                sensor_data = _sensor_data(data, line)
                _link_sensors(sensor_data, device, line)
                payload[f"RuntimeDataSensorLine{line + 1}"] = sensor_data
    if payload:
        project.persist_device_blob(device, _BLOB, json.dumps(payload).encode())

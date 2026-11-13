"""Inspect saved plugin projects using a shared, versioned product catalogue."""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

from .model import PowerToolConfig
from .nrbf import Document, Record, Value, read_document

ORIGINAL_BLOB_NAME = "PT2EtsPlugin"
_PREFIX = "BJE.toolB.PT2Logic."


@dataclass(frozen=True)
class SavedParameter:
    path: str
    label: str
    value: bool | int | float | str | None
    kind: str
    options: tuple[str, ...] = ()
    group: str = ""
    group_key: str = ""
    internal: bool = False
    display_value: bool | int | float | str | None = None
    default_value: bool | int | float | str | None = None
    display_order: int = 0


@dataclass(frozen=True)
class SavedChannel:
    path: str
    label: str
    application: str
    option: int
    parameters: tuple[SavedParameter, ...]
    native_function: str | None = None
    object_flags: tuple[int, ...] = ()
    internal_objects: bool = False


@dataclass(frozen=True)
class SavedDevice:
    path: str
    address: int
    disabled: bool
    product_key: str | None = None
    channel_count: int = 0


@dataclass(frozen=True)
class SavedProject:
    database: str
    database_version: str
    code: int
    product_type: str
    channels: tuple[SavedChannel, ...]
    parameters: tuple[SavedParameter, ...]
    metadata_verified: bool
    serialized_length: int
    devices: tuple[SavedDevice, ...]


@lru_cache(maxsize=1)
def _catalog() -> list[dict[str, Any]]:
    return json.loads(
        gzip.decompress(files(__package__).joinpath("catalog.json.gz").read_bytes())
    )


def _record(document: Document, value: Value, kind: str | None = None) -> Record:
    result = document.resolve(value)
    if not isinstance(result, Record) or (
        kind is not None and result.type_name != _PREFIX + kind
    ):
        raise ValueError(f"Invalid PowerTool {kind or 'record'}")
    return result


def _integer(value: Value) -> int:
    if type(value) is not int:
        raise ValueError("Invalid PowerTool integer")
    return value


def _string(value: Value) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid PowerTool string")
    return value


def _display_value(
    value: bool | int | float | str | None, node: dict[str, Any]
) -> bool | int | float | str | None:
    kind = node.get("value_kind")
    if type(value) is int and kind in ("V_SIGNED", "V_UNSIGNED"):
        size = node["size"]
        if kind == "V_SIGNED" and size in (1, 2, 4, 8):
            bits = size * 8
            value &= (1 << bits) - 1
            if value & (1 << (bits - 1)):
                return value - (1 << bits)
        return ((value & int(node["mask"])) >> node["shift"]) + node.get(
            "display_offset", 0
        )
    return value


class _ProjectReader:
    def __init__(self, document: Document, metadata: dict[str, Any] | None) -> None:
        self.document = document
        self.metadata = metadata
        self.channels: list[SavedChannel] = []
        self.parameters: list[SavedParameter] = []
        self.devices: list[SavedDevice] = []
        self.visited: set[int] = set()

    def member(self, record: Record, key: str) -> Value:
        if key not in record.members:
            raise ValueError(f"Missing PowerTool member {key}")
        return self.document.resolve(record.members[key])

    def array(self, value: Value) -> list[Value]:
        result = self.document.resolve(value)
        if result is None:
            return []
        if not isinstance(result, list):
            raise ValueError("Invalid PowerTool collection")
        return result

    def node(self, key: str | None) -> dict[str, Any]:
        if self.metadata is None or key is None:
            return {}
        if key not in self.metadata["nodes"]:
            raise ValueError("Missing PowerTool product metadata")
        return self.metadata["nodes"][key]

    def enter(self, record: Record) -> None:
        identity = id(record)
        if identity in self.visited:
            raise ValueError("Repeated or cyclic PowerTool configuration node")
        self.visited.add(identity)
        if len(self.visited) > 100_000:
            raise ValueError("PowerTool configuration limit exceeded")

    def paired(
        self, values: list[Value], keys: list[str]
    ) -> list[tuple[Value, str | None]]:
        if self.metadata is not None and values and len(values) != len(keys):
            raise ValueError("PowerTool configuration does not match product metadata")
        return [
            (value, keys[i] if i < len(keys) else None)
            for i, value in enumerate(values)
        ]

    def read_parameters(
        self,
        values: Value,
        keys: list[str],
        path: str,
        depth: int,
        group: str = "",
        group_key: str = "",
        internal: bool = False,
    ) -> list[SavedParameter]:
        if depth > 64:
            raise ValueError("PowerTool configuration nesting limit exceeded")
        result: list[SavedParameter] = []
        for index, (value, key) in enumerate(self.paired(self.array(values), keys)):
            record = _record(self.document, value)
            if not record.type_name.startswith(
                _PREFIX
            ) or not record.type_name.endswith("Parameter"):
                raise ValueError("Invalid PowerTool parameter record")
            self.enter(record)
            node = self.node(key)
            parameter_path = f"{path}/{index}"
            hidden = internal or node.get("visibility") == "hidden"
            label = node.get("label") or (
                key.rsplit("+", 1)[-1] if key else f"Parameter {index + 1}"
            )
            stored = self.member(record, "ParameterValue")
            if not isinstance(stored, (type(None), bool, int, float, str)):
                raise ValueError("Unsupported PowerTool parameter value")
            if isinstance(stored, float) and not math.isfinite(stored):
                raise ValueError("Invalid PowerTool numeric value")
            result.append(
                SavedParameter(
                    parameter_path,
                    label,
                    stored,
                    node.get("value_kind", record.type_name.removeprefix(_PREFIX)),
                    tuple(node.get("options", [])),
                    group,
                    group_key,
                    hidden,
                    _display_value(stored, node),
                    node.get("default"),
                    node.get("display_order", 0),
                )
            )
            result.extend(
                self.read_parameters(
                    self.member(record, "serializedParameters"),
                    node.get("parameters", []),
                    parameter_path,
                    depth + 1,
                    " / ".join(filter(None, (group, label)))
                    if node.get("value_kind") == "V_PAGE"
                    else group,
                    parameter_path if node.get("value_kind") == "V_PAGE" else group_key,
                    hidden,
                )
            )
        return result

    def read_channel(
        self, value: Value, key: str | None, path: str, depth: int
    ) -> None:
        if depth > 64:
            raise ValueError("PowerTool configuration nesting limit exceeded")
        record = _record(self.document, value, "Channel")
        self.enter(record)
        node = self.node(key)
        option = _integer(self.member(record, "ApplicationOptionIndex"))
        if option < 0:
            raise ValueError("Invalid PowerTool application option")
        app = self.member(record, "Application")
        app_node: dict[str, Any] = {}
        object_flags: list[int] = []
        internal_objects = False
        parameters = self.read_parameters(
            self.member(record, "serializedParameters"),
            node.get("parameters", []),
            path + "/parameters",
            depth + 1,
        )
        if app is None and option != 0:
            raise ValueError(
                "Unexpected option for an uninitialized PowerTool application"
            )
        if self.metadata is not None:
            options = node.get("applications", [])
            if option >= len(options):
                raise ValueError(
                    "PowerTool application option exceeds product metadata"
                )
            app_node = self.node(options[option])
        if app is not None:
            application = _record(self.document, app, "Application")
            self.enter(application)
            internal_objects = bool(
                self.array(
                    self.member(application, "serializedInternalCommunicationObjects")
                )
            )
            for item in self.array(
                self.member(application, "serializedCommunicationObjects")
            ):
                obj = _record(self.document, item, "CommunicationObject")
                flags = _integer(self.member(obj, "priority"))
                if not 0 <= flags <= 3:
                    raise ValueError("Invalid PowerTool object priority")
                for name, bit in (
                    ("communicationEnable", 4),
                    ("readEnable", 8),
                    ("writeEnable", 16),
                    ("readOnInitEnable", 32),
                    ("transmitEnable", 64),
                    ("responseUpdateEnable", 128),
                ):
                    enabled = self.member(obj, name)
                    if type(enabled) is not bool:
                        raise ValueError("Invalid PowerTool object flag")
                    if enabled:
                        flags |= bit
                object_flags.append(flags)
            parameters.extend(
                self.read_parameters(
                    self.member(application, "serializedParameters"),
                    app_node.get("parameters", []),
                    path + "/application",
                    depth + 1,
                )
            )
        name = self.member(record, "name")
        if name is not None and not isinstance(name, str):
            raise ValueError("Invalid PowerTool channel name")
        self.channels.append(
            SavedChannel(
                path,
                name or node.get("label") or path,
                app_node.get("label", ""),
                option,
                tuple(parameters),
                app_node.get("native_function"),
                tuple(object_flags),
                internal_objects,
            )
        )
        for index, (child, child_key) in enumerate(
            self.paired(
                self.array(self.member(record, "serializedChannels")),
                node.get("channels", []),
            )
        ):
            self.read_channel(child, child_key, f"{path}/channels/{index}", depth + 1)

    def read_device(self, value: Value, key: str | None, path: str, depth: int) -> None:
        if depth > 64:
            raise ValueError("PowerTool configuration nesting limit exceeded")
        record = _record(self.document, value, "Device")
        self.enter(record)
        node = self.node(key)
        address = _integer(self.member(record, "address"))
        if not 0 <= address <= 255:
            raise ValueError("Invalid PowerTool device address")
        self.devices.append(
            SavedDevice(
                path,
                address,
                self.member(record, "disable") is not None,
                key,
                len(self.array(self.member(record, "serializedChannels"))),
            )
        )
        self.parameters.extend(
            self.read_parameters(
                self.member(record, "serializedParameters"),
                node.get("parameters", []),
                path + "/parameters",
                depth + 1,
            )
        )
        channels = self.array(self.member(record, "serializedChannels"))
        channel_keys = node.get("channels", [])
        if node.get("special"):
            limit = node["max_channels"]
            if len(channel_keys) != 1 or not 1 <= len(channels) <= limit:
                raise ValueError("Invalid PowerTool channel count")
            channel_keys = channel_keys * len(channels)
        for index, (child, child_key) in enumerate(self.paired(channels, channel_keys)):
            self.read_channel(child, child_key, f"{path}/channels/{index}", depth + 1)
        attached = self.member(record, "attachedDevice")
        if attached is not None:
            child_key = self.selected(
                node.get("children", []),
                self.member(record, "serializedProductOptionIndex"),
            )
            self.read_device(attached, child_key, path + "/attached", depth + 1)
        for index, (child, child_key) in enumerate(
            self.paired(
                self.array(record.members.get("devices")), node.get("choices", [])
            )
        ):
            self.read_device(child, child_key, f"{path}/devices/{index}", depth + 1)
        subsystems = self.array(self.member(record, "serializedSubSystems"))
        definitions = node.get("subsystems", [])
        if self.metadata is not None and len(subsystems) > len(definitions):
            raise ValueError("PowerTool subsystem exceeds product metadata")
        for index, value in enumerate(subsystems):
            subsystem = _record(self.document, value, "SubSystem")
            self.enter(subsystem)
            child_key = self.selected(
                definitions[index] if definitions else [],
                self.member(subsystem, "serializedProductOptionIndex"),
            )
            self.read_device(
                self.member(subsystem, "attachedDevice"),
                child_key,
                f"{path}/subsystems/{index}",
                depth + 1,
            )

    def selected(self, keys: list[str], value: Value) -> str | None:
        index = _integer(value)
        if index < 0 or (self.metadata is not None and index >= len(keys)):
            raise ValueError("Invalid PowerTool product option")
        return keys[index] if index < len(keys) else None


@lru_cache(maxsize=4)
def read_project_blob(data: bytes) -> SavedProject:
    """Read saved values; label them only when database, version and product match the catalogue.

    Values are stored representations, including enum indices and timer codes. This
    inspection does not infer object bindings, editable capabilities or image support.
    """
    document = read_document(data)
    root = _record(document, document.root, "Project")
    reader = _ProjectReader(document, None)
    version = _integer(reader.member(root, "Version"))
    if version not in range(5):
        raise ValueError("Unsupported PowerTool project version")
    database = _string(reader.member(root, "productDBPath"))
    db_version = _record(document, reader.member(root, "productDBVersion"))
    if db_version.type_name != "System.Version":
        raise ValueError("Invalid PowerTool database version")
    version_text = ".".join(
        str(_integer(reader.member(db_version, k)))
        for k in ("_Major", "_Minor", "_Build", "_Revision")
    )
    code = _integer(reader.member(root, "code"))
    product = _string(reader.member(root, "productType")) if version else "priOn"
    metadata = next(
        (
            m
            for m in _catalog()
            if m["database"] == database
            and m["version"] == version_text
            and m["code"] == code
            and product in m["products"]
        ),
        None,
    )
    reader.metadata = metadata
    roots = reader.array(reader.member(root, "ProjectedDevices"))
    for index, (value, key) in enumerate(
        reader.paired(roots, metadata["products"][product] if metadata else [])
    ):
        reader.read_device(value, key, f"devices/{index}", 0)
    return SavedProject(
        database,
        version_text,
        code,
        product,
        tuple(reader.channels),
        tuple(reader.parameters),
        metadata is not None,
        document.length,
        tuple(reader.devices),
    )


def config_from_project_blob(data: bytes) -> PowerToolConfig:
    """Explicitly convert the verified push-button parameter layout, without inferring links."""
    from .model import (
        DATABASE_VERSION,
        ChannelConfig,
        Variant,
        functions,
    )

    saved = read_project_blob(data)
    if (
        not saved.metadata_verified
        or saved.code != 3
        or saved.database != "ToolBObjektTasterDatabase.dll"
        or saved.database_version != DATABASE_VERSION
        or saved.product_type not in ("TasterBA1f", "TasterBA2f")
    ):
        raise ValueError("Native conversion is not verified for this PowerTool profile")
    variant: Variant = "01" if saved.product_type == "TasterBA1f" else "02"
    channels: list[ChannelConfig] = []
    if [(d.path, d.address, d.disabled) for d in saved.devices] != [
        ("devices/0", 0, False),
        ("devices/0/attached", 0, False),
        ("devices/0/attached/devices/0", 62, False),
    ]:
        raise ValueError("Unsupported native PowerTool device layout or disable flags")
    if saved.parameters:
        raise ValueError("Unsupported PowerTool device parameters")
    for channel in saved.channels:
        if channel.path != f"devices/0/attached/devices/0/channels/{len(channels)}":
            raise ValueError("Unsupported native PowerTool channel layout")
        if channel.internal_objects:
            raise ValueError("Unsupported native internal communication objects")
        function = channel.native_function
        if function is None:
            if channel.parameters or channel.application:
                raise ValueError("Unsupported PowerTool application")
            function = "inactive"
        fields = functions()[function].fields
        objects = functions()[function].objects
        if channel.object_flags and channel.object_flags != tuple(
            o.flags for o in objects
        ):
            raise ValueError(
                "Native conversion of modified PowerTool object flags is not verified"
            )
        values: dict[str, int | float] = {}
        paths: dict[int, str] = {}
        indices: dict[int | None, int] = {}
        for index, field in enumerate(fields):
            parent = field.parent
            position = indices.get(parent, 0)
            indices[parent] = position + 1
            paths[index] = (
                f"{paths[parent] if parent is not None else channel.path + '/application'}/{position}"
            )
        by_path = {paths[i]: field for i, field in enumerate(fields)}
        for parameter in channel.parameters:
            field = by_path.get(parameter.path)
            if field is None:
                raise ValueError(
                    "PowerTool parameter layout does not match native schema"
                )
            value = parameter.value
            if value is None:
                continue
            if type(value) not in (int, float):
                raise ValueError("Unsupported native parameter value")
            assert isinstance(value, (int, float))
            if field.kind == "freetimer":
                code = int(value)
                factor, exponent = code & 255, code >> 8
                if code != value or not 0 <= exponent <= 23 or not 0 <= factor <= 127:
                    raise ValueError("Invalid PowerTool timer code")
                value = factor * (5 << exponent)
            elif field.kind in ("signed", "unsigned"):
                stored = int(value)
                if stored != value or stored < 0 or stored & ~field.mask:
                    raise ValueError("Invalid masked PowerTool numeric value")
                value = stored >> field.shift
                if field.kind == "signed":
                    width = (field.mask >> field.shift).bit_length()
                    if value & (1 << (width - 1)):
                        value -= 1 << width
            if field.visible and field.kind != "page":
                field.validate(value)
                values[field.key] = value
            elif value != field.default:
                raise ValueError("Unsupported hidden PowerTool parameter override")
        channels.append(ChannelConfig(function, values))
    return PowerToolConfig(variant, tuple(channels))

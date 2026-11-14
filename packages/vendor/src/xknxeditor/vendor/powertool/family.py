"""Data-driven parameter evaluation for captured PowerTool product profiles."""

from __future__ import annotations

import gzip
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from .model import float32, object_flag_overrides, timer_code
from .project_blob import read_project_blob, schema_version

type Scalar = int | float | str | None


@lru_cache(maxsize=1)
def schemas() -> tuple[dict[str, Any], ...]:
    return tuple(
        json.loads(
            gzip.decompress(
                files(__package__).joinpath("family-schema.json.gz").read_bytes()
            )
        )
    )


def schema_for(profile: str, database_code: int | None = None) -> dict[str, Any]:
    if database_code is None:
        database_code = {
            "ObjektRTR": 3,
            "ObjektRTRunified": 3,
            "PraesenzStandard": 4,
            "PraesenzKomfort": 4,
            "SoloRTR3": 2,
            "SoloRTR3oBE": 2,
        }.get(profile)
    matches = [
        schema
        for schema in schemas()
        if profile in schema["products"]
        and (database_code is None or schema["code"] == database_code)
    ]
    if len(matches) != 1:
        raise ValueError("Unknown or ambiguous PowerTool profile")
    return matches[0]


@dataclass(frozen=True)
class FamilyConfig:
    profile: str
    options: Mapping[str, int] = field(default_factory=lambda: {})
    values: Mapping[str, Scalar] = field(default_factory=lambda: {})
    bindings: Mapping[str, str] = field(default_factory=lambda: {})
    schema_version: int = 1
    database_code: int | None = None
    products: Mapping[str, int] = field(default_factory=lambda: {})
    channel_counts: Mapping[str, int] = field(default_factory=lambda: {})
    program_ids: tuple[str, str] | None = None
    mask_version: int = 0x07B0
    channel_ids: Mapping[str, str] = field(default_factory=lambda: {})
    object_flags: Mapping[str, int] = field(default_factory=lambda: {})

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "object_flags", object_flag_overrides(self.object_flags)
        )
        if self.database_code is not None and type(self.database_code) is not int:
            raise ValueError("Invalid PowerTool database code")
        if self.mask_version not in (0x07B0, 0x17B0):
            raise ValueError("Invalid PowerTool media")
        if self.program_ids is not None and (
            type(self.program_ids) is not tuple
            or len(self.program_ids) != 2
            or any(
                type(value) is not str
                or len(value) != 10
                or not value.startswith("0007")
                or any(c not in "0123456789abcdef" for c in value)
                for value in self.program_ids
            )
        ):
            raise ValueError("Invalid PowerTool application identifiers")
        schema_for(self.profile, self.database_code)
        if self.schema_version != 1:
            raise ValueError("Unknown PowerTool family configuration version")
        for name in (
            "options",
            "values",
            "bindings",
            "products",
            "channel_counts",
            "channel_ids",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        if any(
            type(k) is not str or type(v) is not int or v < 0
            for values in (self.options, self.products, self.channel_counts)
            for k, v in values.items()
        ):
            raise ValueError("Invalid PowerTool channel options")
        if any(
            type(k) is not str
            or type(v) not in (int, float, str, type(None))
            or (isinstance(v, float) and not math.isfinite(v))
            for k, v in self.values.items()
        ):
            raise ValueError("Invalid PowerTool parameter values")
        if any(
            type(k) is not str or type(v) is not str for k, v in self.bindings.items()
        ) or len(set(self.bindings.values())) != len(self.bindings):
            raise ValueError("Invalid PowerTool object bindings")
        if any(
            type(k) is not str or type(v) is not str or not v
            for k, v in self.channel_ids.items()
        ) or len(set(self.channel_ids.values())) != len(self.channel_ids):
            raise ValueError("Invalid PowerTool channel identities")


@dataclass
class Node:
    key: str
    path: str
    spec: dict[str, Any]
    parent: Node | None = None
    application: Node | None = None
    channel: Node | None = None
    product: Node | None = None
    parameters: list[Node] = field(default_factory=lambda: [])
    channels: list[Node] = field(default_factory=lambda: [])
    objects: list[Node] = field(default_factory=lambda: [])


class FamilyEvaluation:
    def __init__(self, config: FamilyConfig) -> None:
        self.config = config
        self.schema = schema_for(config.profile, config.database_code)
        self.nodes: dict[str, Node] = {}
        self.channels: list[Node] = []
        self.devices: list[Node] = []
        self.product_choices: dict[str, tuple[str, ...]] = {}
        self.count_limits: dict[str, int] = {}
        self._evaluating: set[tuple[str, str]] = set()
        self._properties: dict[tuple[str, str], Scalar | bool] = {}
        for i, key in enumerate(self.schema["products"][config.profile]):
            placements = self.schema.get("root_placements", {}).get(config.profile)
            self._device(
                key, f"devices/{i}", placement=placements[i] if placements else None
            )
        if set(config.options) - {c.path for c in self.channels}:
            raise ValueError("Unknown PowerTool channel")
        if set(config.products) - self.product_choices.keys():
            raise ValueError("Unknown PowerTool product selection")
        if set(config.channel_counts) - self.count_limits.keys():
            raise ValueError("Unknown PowerTool channel collection")
        if set(config.channel_ids) - {c.path for c in self.channels}:
            raise ValueError("Unknown PowerTool channel identity")
        identities = [config.channel_ids.get(c.path, c.path) for c in self.channels]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate PowerTool channel identity")
        if set(config.object_flags) - {
            f"{config.channel_ids.get(c.path, c.path)}/{c.application.key}/{o.key}"
            for c in self.channels
            if c.application is not None
            for o in c.application.objects
        }:
            raise ValueError("PowerTool flags refer to an unknown object")
        if set(config.values) - {
            n.path for n in self.nodes.values() if n.spec["kind"] == "parameter"
        }:
            raise ValueError("Unknown PowerTool parameter")

        for node in self.nodes.values():
            if node.spec["kind"] == "parameter":
                self._validate_stored(node)

    def _validate_stored(self, node: Node) -> None:
        value = self.config.values.get(node.path)
        if value is None:
            return
        spec = node.spec
        kind = spec["value_kind"]
        if kind == "V_PAGE":
            raise ValueError("PowerTool pages have no scalar value")
        if spec["extension"] == "DatapointConfig":
            if value != spec["default"]:
                raise ValueError("Invalid PowerTool datapoint configuration")
            return
        if kind in ("V_STRING", "V_STRINGPTR"):
            smart_touch = bool(
                node.application and node.application.spec["smart_touch"]
            )
            if not isinstance(value, str) or len(
                value.encode("utf-8" if smart_touch else "utf-16-be")
            ) > spec["size"] - (0 if smart_touch else 4):
                raise ValueError("Invalid PowerTool text value")
            return
        if kind == "V_FLOAT":
            if type(value) not in (int, float):
                raise ValueError("Invalid PowerTool float value")
            assert isinstance(value, (int, float))
            number = float32(value)
        else:
            if type(value) is not int:
                raise ValueError("Invalid PowerTool integer value")
            if kind == "V_ENUM":
                if not 0 <= value < len(spec["options"]):
                    raise ValueError("Invalid PowerTool enum index")
                return
            if kind == "V_SYMBOL":
                if value not in [option["value"] for option in spec["options"]]:
                    raise ValueError("Invalid PowerTool symbol")
                return
            if kind == "V_FREETIMER":
                if not 0 <= value >> 8 <= 23 or not 0 <= value & 255 <= 127:
                    raise ValueError("Invalid PowerTool timer")
                number = (value & 255) * (5 << (value >> 8))
            else:
                if value < 0 or value & ~spec["mask"]:
                    raise ValueError("Invalid PowerTool masked value")
                number = value >> spec["shift"]
                if kind == "V_SIGNED" and value & (1 << (spec["size"] * 8 - 1)):
                    number = value - (1 << (spec["size"] * 8))
        bounds = spec["constraints"]
        low, high = bounds["min"], bounds["max"]
        if kind == "V_FLOAT":
            low, high = float32(low), float32(high)
        elif kind == "V_FREETIMER":
            code = timer_code(low)
            low = (code & 255) * (5 << (code >> 8))
        if not low <= number <= high:
            raise ValueError("PowerTool parameter is outside its allowed range")

    def _node(self, key: str, path: str, parent: Node | None = None) -> Node:
        node = Node(key, path, self.schema["nodes"][key], parent)
        self.nodes[path] = node
        if parent:
            node.product = parent.product
            node.channel = parent.channel
            node.application = parent.application
        return node

    def _parameters(self, node: Node) -> None:
        prefix = (
            node.path
            if node.spec["kind"] in ("application", "parameter")
            else node.path + "/parameters"
        )
        for i, key in enumerate(node.spec.get("parameters", [])):
            child = self._node(key, f"{prefix}/{i}", node)
            node.parameters.append(child)
            self._parameters(child)

    def _device(
        self,
        key: str,
        path: str,
        parent: Node | None = None,
        address: int | None = None,
        placement: dict[str, Any] | None = None,
    ) -> None:
        if len(self.devices) >= 1024:
            raise ValueError("PowerTool device limit exceeded")
        node = self._node(key, path, parent)
        if placement:
            node.spec = {**node.spec, **placement}
        if address is not None:
            node.spec = {**node.spec, "address": address}
        node.product = node
        self.devices.append(node)
        self._parameters(node)
        self._channels(node)
        candidates = node.spec["children"]
        if candidates:
            selected_path = path + "/attached"
            self.product_choices[selected_path] = tuple(candidates)
            if self.config.database_code is None:
                populated = [
                    k for k in candidates if self.schema["nodes"][k]["choices"]
                ]
                if len(populated) != 1:
                    raise ValueError("Unverified PowerTool attachment layout")
                default = candidates.index(populated[0])
            else:
                default = 0
            choice = self.config.products.get(selected_path, default)
            if not 0 <= choice < len(candidates):
                raise ValueError("Invalid PowerTool product selection")
            placements = node.spec.get("child_placements")
            self._device(
                candidates[choice],
                selected_path,
                node,
                placement=placements[choice] if placements else None,
            )
        for i, child in enumerate(node.spec["choices"]):
            placements = node.spec.get("choice_placements")
            self._device(
                child,
                f"{path}/devices/{i}",
                node,
                placement=placements[i] if placements else None,
            )
        selections = self._subsystems(node)
        for i, choices in enumerate(node.spec["subsystems"]):
            selected_path = f"{path}/subsystems/{i}"
            self.product_choices[selected_path] = tuple(choices)
            choice, disabled = selections[i]
            placements = node.spec.get("subsystem_placements")
            self._device(
                choices[choice],
                selected_path,
                node,
                node.spec["subsystem_addresses"][i],
                {**(placements[i][choice] if placements else {}), "disabled": disabled},
            )

    def _subsystems(self, node: Node) -> list[tuple[int, bool]]:
        groups = node.spec["subsystems"]
        selected = [
            self.config.products.get(f"{node.path}/subsystems/{i}", 0)
            for i in range(len(groups))
        ]
        if any(not 0 <= v < len(groups[i]) for i, v in enumerate(selected)):
            raise ValueError("Invalid PowerTool subsystem selection")
        disabled = [
            self.schema["nodes"][group[selected[i]]].get("disabled", False)
            for i, group in enumerate(groups)
        ]
        addresses = node.spec.get("subsystem_addresses", [])
        partners = node.spec.get("subsystem_partners", [[] for _ in groups])
        for i, rules in enumerate(partners):
            for rule in rules:
                if rule["product"] != selected[i]:
                    continue
                target = addresses.index(rule["address"])
                if groups[target][selected[target]] != rule["target"]:
                    raise ValueError("PowerTool hardware requires a matching partner")
                disabled[target] |= rule["disable"]
        areas = node.spec.get("subsystem_areas", ["None" for _ in groups])
        actors = [i for i, area in enumerate(areas) if area == "Actor"]
        used_bau = [
            i
            for i in actors
            if self.schema["nodes"][groups[i][selected[i]]].get("is_bau")
        ]
        if len(used_bau) > 1:
            raise ValueError("PowerTool hardware permits only one bus coupler")
        if actors and len(groups) >= 3:
            inner = [i for i in actors if actors[0] < i < actors[-1]]
            if inner and all(selected[i] and i not in used_bau for i in inner):
                raise ValueError("PowerTool hardware needs space for its bus coupler")
        return list(zip(selected, disabled, strict=True))

    def _channels(self, parent: Node) -> None:
        keys = parent.spec["channels"]
        if parent.spec.get("special") and parent.spec.get("max_channels", 0) > 0:
            self.count_limits[parent.path] = parent.spec["max_channels"]
            count = self.config.channel_counts.get(parent.path, len(keys))
            if len(keys) != 1 or not 1 <= count <= self.count_limits[parent.path]:
                raise ValueError("Invalid PowerTool channel count")
            keys = keys * count
        for i, key in enumerate(keys):
            if len(self.channels) >= 4096:
                raise ValueError("PowerTool channel limit exceeded")
            node = self._node(key, f"{parent.path}/channels/{i}", parent)
            parent.channels.append(node)
            self.channels.append(node)
            node.channel = node
            self._parameters(node)
            options = node.spec["applications"]
            index = self.config.options.get(node.path, 0)
            if not 0 <= index < len(options):
                raise ValueError("Invalid PowerTool function option")
            app = self._node(options[index], node.path + "/application", node)
            app.application = app
            node.application = app
            self._parameters(app)
            for position, obj in enumerate(app.spec["objects"]):
                app.objects.append(
                    self._node(obj, f"{app.path}/objects/{position}", app)
                )
            self._channels(node)

    def evaluate(self, node: Node, rule: list[Any]) -> Scalar | bool:
        op = rule[0]
        if op == "const":
            return rule[1]
        if op == "get":
            return self._get(node, rule[1])
        if op == "active":
            return any(
                device.key == rule[1] and device.parent is node.parent
                for device in self.devices
                if "/subsystems/" in device.path
                and device.path.rsplit("/", 2)[-2] == "subsystems"
            )
        if op == "if":
            return self.evaluate(
                node, rule[2] if self.evaluate(node, rule[1]) else rule[3]
            )
        if op == "all":
            return all(self.evaluate(node, c) for c in rule[1:])
        if op == "any":
            return any(self.evaluate(node, c) for c in rule[1:])
        if op == "not":
            return not self.evaluate(node, rule[1])
        left = self.evaluate(node, rule[1])
        if op == "neg":
            if not isinstance(left, (int, float)):
                raise ValueError("Invalid PowerTool numeric expression")
            return -left
        right = self.evaluate(node, rule[2])
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            raise ValueError("Invalid PowerTool numeric expression")
        if op == "shl":
            return int(left) << int(right)
        return {
            "gt": left > right,
            "ge": left >= right,
            "lt": left < right,
            "le": left <= right,
        }[op]

    def _get(self, node: Node, path: list[str | int]) -> Scalar | bool:
        if path == ["base", "Type"]:
            return node.spec["object_type"]
        roots = {
            "Application": node.application,
            "Product": node.product,
            "ParentParameter": node.parent,
            "ParentChannel": node.parent,
        }
        current = roots.get(str(path[0]))
        i = 1
        while current is not None and i < len(path):
            part = path[i]
            if part in ("Parameters", "Channels"):
                collection = (
                    current.parameters if part == "Parameters" else current.channels
                )
                current = collection[int(path[i + 1])]
                i += 2
            elif part == "Application":
                current = current.application
                i += 1
            elif part == "type":
                return current.key
            elif part == "Shift":
                return current.spec["shift"]
            elif part == "ParameterValue":
                return self.stored(current)
            elif part in ("Exists", "Visible"):
                return self.property(current, str(part))
            else:
                break
        raise ValueError(f"Unverified PowerTool expression path: {path}")

    def property(self, node: Node, name: str) -> Scalar | bool:
        cache_key = (node.path, name)
        if cache_key in self._properties:
            return self._properties[cache_key]
        if cache_key in self._evaluating:
            raise ValueError("Cyclic PowerTool condition")
        self._evaluating.add(cache_key)
        try:
            if name in node.spec:
                result = self.evaluate(node, node.spec[name])
            elif name == "Exists":
                result = (
                    self.property(node.parent, name)
                    if node.spec["kind"] == "parameter" and node.parent
                    else True
                )
            elif name == "Visible":
                result = node.spec.get("visible", True)
            elif name == "Type":
                result = node.spec["object_type"]
            else:
                result = node.spec[name[0].lower() + name[1:]]
            self._properties[cache_key] = result
            return result
        finally:
            self._evaluating.remove(cache_key)

    def stored(self, node: Node) -> Scalar:
        value = self.config.values.get(node.path)
        if value is not None and (
            (node.path, "Visible") in self._evaluating
            or self.property(node, "Visible")
            or node.spec["extension"] in ("ControlId", "ParentId", "Position")
        ):
            return value
        spec = node.spec
        value = spec["default"]
        if spec["value_kind"] in (
            "V_UNSIGNED",
            "V_SIGNED",
            "P_SecondsShort",
            "P_SecondsLong",
            "P_MillisecondsShort",
            "P_MillisecondsLong",
        ):
            return (int(value) << spec["shift"]) & spec["mask"]
        if spec["value_kind"] == "V_FREETIMER":
            return timer_code(int(value))
        return value

    def image_value(self, node: Node) -> Scalar:
        value = self.stored(node)
        spec = node.spec
        if spec["value_kind"] == "V_ENUM":
            if type(value) is not int or not 0 <= value < len(spec["options"]):
                raise ValueError("Invalid PowerTool enum index")
            return (int(spec["options"][value]["value"]) << spec["shift"]) & spec[
                "mask"
            ]
        if spec["value_kind"] == "V_UNSIGNED":
            if not isinstance(value, (int, float)):
                raise ValueError("Invalid PowerTool numeric value")
            factor = float32(spec["constraints"].get("factor", 1))
            product = value * factor
            return int(float32(product) if factor != int(factor) else product)
        return value

    def channel_exists(self, channel: Node) -> bool:
        if not self.property(channel, "Exists"):
            return False
        if channel.parent is not None and channel.parent.spec["kind"] == "channel":
            return self.channel_exists(channel.parent)
        return channel.product is None or bool(self.property(channel.product, "Exists"))

    def channel_number(self, channel: Node) -> int:
        assert channel.product is not None
        product = channel.product
        if product.spec.get("special"):
            return product.spec["address"] << 16 | product.channels.index(channel)
        return (
            channel.spec.get("address") or product.spec["address"]
        ) << 16 | channel.spec["number"]

    def active(self, channel: Node) -> bool:
        return (
            self.channel_exists(channel)
            and channel.application is not None
            and not channel.application.spec["inactive"]
        )

    def display(self, node: Node) -> Scalar:
        value = self.stored(node)
        spec = node.spec
        if type(value) is int:
            kind = spec["value_kind"]
            if kind == "V_SYMBOL":
                return next(
                    i
                    for i, option in enumerate(spec["options"])
                    if option["value"] == value
                )
            if kind == "V_FREETIMER":
                return (value & 255) * (5 << (value >> 8))
            if kind == "V_SIGNED":
                width = spec["size"] * 8
                return (value & ((1 << width) - 1)) - (
                    (1 << width) if value & (1 << (width - 1)) else 0
                )
            if kind == "V_UNSIGNED":
                return ((value & spec["mask"]) >> spec["shift"]) + spec[
                    "constraints"
                ].get("displayoffset", 0)
        return value


def from_saved(data: bytes, *, qualified: bool = False) -> FamilyConfig:
    saved = read_project_blob(data)
    schema = schema_for(saved.product_type, saved.code)
    if not saved.metadata_verified or (
        saved.database,
        schema_version(saved.database, saved.code, saved.database_version),
        saved.code,
    ) != (schema["database"], schema["version"], schema["code"]):
        raise ValueError("Unverified PowerTool database version")
    qualified = qualified or any(
        d.channel_count > 1
        and schema["nodes"][
            (d.product_key or "").removeprefix("BJE.toolB.ProductDatabase.")
        ].get("special")
        for d in saved.devices
    )
    products: dict[str, int] = {}
    counts: dict[str, int] = {}
    if qualified:
        definitions = {
            d.path: (d.product_key or "").removeprefix("BJE.toolB.ProductDatabase.")
            for d in saved.devices
        }
        for device in saved.devices:
            spec = schema["nodes"][definitions[device.path]]
            if spec.get("special") and device.channel_count:
                counts[device.path] = device.channel_count
            if device.path.endswith("/attached"):
                parent = device.path.removesuffix("/attached")
                choices = schema["nodes"][definitions[parent]]["children"]
            elif (
                "/subsystems/" in device.path
                and device.path.rsplit("/", 2)[-2] == "subsystems"
            ):
                parent, _, index = device.path.rsplit("/", 2)
                choices = schema["nodes"][definitions[parent]]["subsystems"][int(index)]
            else:
                continue
            products[device.path] = choices.index(definitions[device.path])
    config = FamilyConfig(
        saved.product_type,
        {c.path: c.option for c in saved.channels},
        {
            p.path: p.value
            for p in [
                *saved.parameters,
                *(p for c in saved.channels for p in c.parameters),
            ]
        },
        database_code=saved.code if qualified else None,
        products=products,
        channel_counts=counts,
    )
    evaluated = FamilyEvaluation(config)
    if [(d.path, d.address, d.disabled) for d in saved.devices] != [
        (d.path, d.spec["address"], d.spec.get("disabled", False))
        for d in evaluated.devices
    ]:
        raise ValueError("Unverified PowerTool device layout")
    if [c.path for c in saved.channels] != [c.path for c in evaluated.channels]:
        raise ValueError("Unverified PowerTool channel layout")
    if any(c.internal_objects for c in saved.channels):
        raise ValueError("Unverified internal PowerTool objects")
    flags: dict[str, int] = {}
    for channel, node in zip(saved.channels, evaluated.channels, strict=True):
        assert node.application is not None
        expected = tuple(
            o.spec["priority"]
            | sum(
                bit
                for name, bit in (
                    ("responseUpdateEnable", 128),
                    ("transmitEnable", 64),
                    ("readOnInitEnable", 32),
                    ("writeEnable", 16),
                    ("readEnable", 8),
                    ("communicationEnable", 4),
                )
                if evaluated.property(o, name[0].upper() + name[1:])
            )
            for o in node.application.objects
        )
        if channel.object_flags:
            if len(channel.object_flags) != len(expected):
                raise ValueError("PowerTool object flags do not match the application")
            flags.update(
                (f"{node.path}/{node.application.key}/{obj.key}", value)
                for obj, value, default in zip(
                    node.application.objects,
                    channel.object_flags,
                    expected,
                    strict=True,
                )
                if value != default
            )
    return replace(config, object_flags=flags)


def edit(config: FamilyConfig, path: str, text: str) -> FamilyConfig:
    evaluated = FamilyEvaluation(config)
    if path in evaluated.product_choices:
        return _edit_product(config, evaluated, path, int(text))
    if path.endswith("/channel_count"):
        import uuid

        device_path = path.removesuffix("/channel_count")
        limit = evaluated.count_limits.get(device_path)
        count = int(text)
        if limit is None or not 1 <= count <= limit:
            raise ValueError("Invalid PowerTool channel count")
        prefix = device_path + "/channels/"
        removed = tuple(f"{prefix}{i}/" for i in range(count, limit))
        updated = replace(
            config,
            channel_counts={**config.channel_counts, device_path: count},
            values={
                k: v for k, v in config.values.items() if not k.startswith(removed)
            },
            options={
                k: v
                for k, v in config.options.items()
                if not (k + "/").startswith(removed)
            },
            channel_ids={
                **{
                    k: v
                    for k, v in config.channel_ids.items()
                    if not (k + "/").startswith(removed)
                },
                **{
                    f"{prefix}{i}": str(uuid.uuid4())
                    for i in range(len(evaluated.nodes[device_path].channels), count)
                },
            },
        )
        FamilyEvaluation(updated)
        return updated
    node = evaluated.nodes.get(path)
    if (
        node is None
        or not evaluated.property(node, "Exists")
        or (node.channel is not None and not evaluated.channel_exists(node.channel))
    ):
        raise ValueError("PowerTool field is unavailable")
    if node.spec["kind"] == "channel":
        option = int(text)
        if not 0 <= option < len(node.spec["applications"]):
            raise ValueError("Invalid PowerTool function")
        if option == config.options.get(path, 0):
            return config
        from .panel import reset_dependents

        config = reset_dependents(config, evaluated, node)
        if (
            node.product is not None
            and node.product.path in evaluated.count_limits
            and config.database_code is None
        ):
            config = replace(
                config,
                database_code=evaluated.schema["code"],
                products={
                    p: choices.index(evaluated.nodes[p].key)
                    for p, choices in evaluated.product_choices.items()
                },
            )
        values = {
            k: v
            for k, v in config.values.items()
            if not k.startswith(path + "/application/")
        }
        updated = replace(
            config, options={**config.options, path: option}, values=values
        )
        if (
            node.product is not None
            and node.product.path in evaluated.count_limits
            and config.database_code is not None
        ):
            updated = _edit_special_channel(updated, evaluated, node, option)
        state = FamilyEvaluation(updated)
        if state.nodes.get(path) is not None:
            from .panel import initialize_application

            updated = initialize_application(updated, state, path)
        return updated
    if node.spec["kind"] != "parameter" or not evaluated.property(node, "Visible"):
        raise ValueError("PowerTool field is not editable")
    spec = node.spec
    kind = spec["value_kind"]
    if kind == "V_PAGE":
        raise ValueError("PowerTool page is not editable")
    value: Scalar = (
        text
        if kind in ("V_STRING", "V_STRINGPTR")
        else float32(float(text))
        if kind == "V_FLOAT"
        else int(text)
    )
    if value == evaluated.display(node):
        return config
    from .panel import choices as panel_choices

    available = panel_choices(evaluated, node)
    if available is not None and value not in dict(available):
        raise ValueError("Panel position or parent is unavailable")
    if isinstance(value, str):
        panel = bool(node.application and node.application.spec["smart_touch"])
        if len(value.encode("utf-8" if panel else "utf-16-be")) > spec["size"] - (
            0 if panel else 4
        ):
            raise ValueError("PowerTool text exceeds available space")
    else:
        assert isinstance(value, (int, float))
        bounds = spec["constraints"]
        if kind in ("V_ENUM", "V_SYMBOL"):
            if not 0 <= value < len(spec["options"]):
                raise ValueError("Invalid PowerTool enum index")
        elif not math.isfinite(value) or not (
            float32(bounds["min"]) if kind == "V_FLOAT" else bounds["min"]
        ) <= value - bounds.get("displayoffset", 0) <= (
            float32(bounds["max"]) if kind == "V_FLOAT" else bounds["max"]
        ):
            raise ValueError("PowerTool value is outside its allowed range")
        if kind in (
            "V_UNSIGNED",
            "V_SIGNED",
            "P_SecondsShort",
            "P_SecondsLong",
            "P_MillisecondsShort",
            "P_MillisecondsLong",
        ):
            value = (
                int(value - bounds.get("displayoffset", 0)) << spec["shift"]
            ) & spec["mask"]
        elif kind == "V_FREETIMER":
            value = timer_code(int(value))
        elif kind == "V_SYMBOL":
            value = spec["options"][int(value)]["value"]
    updated = replace(config, values={**config.values, path: value})
    state = FamilyEvaluation(updated)
    result = replace(
        updated,
        values={
            key: val
            for key, val in updated.values.items()
            if state.property(state.nodes[key], "Visible")
            or state.nodes[key].spec["extension"]
            in ("ControlId", "ParentId", "Position")
        },
    )
    from .panel import validate

    validate(FamilyEvaluation(result))
    return result


def _edit_product(
    config: FamilyConfig, state: FamilyEvaluation, path: str, option: int
) -> FamilyConfig:
    if config.database_code is None:
        raise ValueError("PowerTool hardware selection requires a database identity")
    choices = state.product_choices[path]
    if not 0 <= option < len(choices):
        raise ValueError("Invalid PowerTool hardware option")
    if state.nodes[path].key == choices[option]:
        return config
    if state.nodes[path].spec.get("disabled"):
        raise ValueError("PowerTool hardware is selected by a partner")
    products = dict(config.products)
    changed: set[str] = {path}
    products[path] = option
    if "/subsystems/" in path and path.rsplit("/", 2)[-2] == "subsystems":
        parent_path, _, position = path.rsplit("/", 2)
        parent = state.nodes[parent_path]
        addresses = parent.spec["subsystem_addresses"]
        groups = parent.spec["subsystems"]
        rules = parent.spec["subsystem_partners"]
        old_option = choices.index(state.nodes[path].key)
        for rule in rules[int(position)]:
            if rule["product"] == old_option:
                target = addresses.index(rule["address"])
                target_path = f"{parent_path}/subsystems/{target}"
                products[target_path] = 0
                changed.add(target_path)
        pending = [int(position)]
        seen: set[int] = set()
        while pending:
            i = pending.pop()
            if i in seen:
                continue
            seen.add(i)
            for rule in rules[i]:
                if rule["product"] != products.get(f"{parent_path}/subsystems/{i}", 0):
                    continue
                target = addresses.index(rule["address"])
                target_path = f"{parent_path}/subsystems/{target}"
                required = groups[target].index(rule["target"])
                current = products.get(target_path, 0)
                if current not in (0, required):
                    raise ValueError("PowerTool hardware partner slot is occupied")
                if current != required:
                    products[target_path] = required
                    changed.add(target_path)
                pending.append(target)
    prefixes = tuple(p + "/" for p in changed)
    updated = replace(
        config,
        products={k: v for k, v in products.items() if not k.startswith(prefixes)},
        options={k: v for k, v in config.options.items() if not k.startswith(prefixes)},
        values={k: v for k, v in config.values.items() if not k.startswith(prefixes)},
        channel_counts={
            k: v
            for k, v in config.channel_counts.items()
            if k not in changed and not k.startswith(prefixes)
        },
        channel_ids={
            k: v for k, v in config.channel_ids.items() if not k.startswith(prefixes)
        },
    )
    FamilyEvaluation(updated)
    return updated


def _edit_special_channel(
    config: FamilyConfig, state: FamilyEvaluation, channel: Node, option: int
) -> FamilyConfig:
    import uuid

    assert channel.product is not None
    device = channel.product
    count = len(device.channels)
    limit = state.count_limits[device.path]
    inactive = sum(config.options.get(c.path, 0) == 0 for c in device.channels)
    if option == 0 and count > 1 and not (count == limit and inactive < 2):
        removed = device.channels.index(channel)
        prefix = device.path + "/channels/"

        def shifted(path: str) -> str | None:
            if not path.startswith(prefix):
                return path
            index, separator, tail = path[len(prefix) :].partition("/")
            number = int(index)
            if number == removed:
                return None
            return prefix + str(number - (number > removed)) + separator + tail

        return replace(
            config,
            channel_counts={**config.channel_counts, device.path: count - 1},
            options={
                new: v
                for k, v in config.options.items()
                if (new := shifted(k)) is not None
            },
            values={
                new: v
                for k, v in config.values.items()
                if (new := shifted(k)) is not None
            },
            channel_ids={
                **{
                    k: v
                    for k, v in config.channel_ids.items()
                    if not k.startswith(prefix)
                },
                **{
                    new: config.channel_ids.get(c.path, c.path)
                    for c in device.channels
                    if (new := shifted(c.path)) is not None
                },
            },
        )
    if count < limit and inactive == 0:
        path = f"{device.path}/channels/{count}"
        return replace(
            config,
            channel_counts={**config.channel_counts, device.path: count + 1},
            channel_ids={**config.channel_ids, path: str(uuid.uuid4())},
        )
    return config

"""Typed configuration for the two BAU B push-button profiles."""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any, Literal

Variant = Literal["01", "02"]
Number = int | float
BLOB_NAME = "PowerTool6108-Config"
DATABASE_VERSION = "1.1.2.85"


def object_flag_overrides(values: Mapping[str, int]) -> Mapping[str, int]:
    checked: Any = values
    if not isinstance(checked, Mapping) or any(
        type(key) is not str or type(value) is not int or not 0 <= value <= 255
        for key, value in values.items()
    ):
        raise ValueError("Invalid PowerTool object flags")
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class Condition:
    operator: str
    index: int = 0
    value: Number = 0
    children: tuple[Condition, ...] = ()

    @classmethod
    def parse(cls, data: list[Any]) -> Condition:
        op = str(data[0])
        if op in ("all", "any"):
            return cls(op, children=tuple(cls.parse(c) for c in data[1:]))
        return cls(
            op, int(data[1]) if len(data) > 1 else 0, data[2] if len(data) > 2 else 0
        )


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    storage: str
    address: int
    size: int
    mask: int
    shift: int
    default: Number | None
    visible: bool
    condition: Condition
    parent: int | None
    options: tuple[tuple[str, int], ...]
    constraints: Mapping[str, Any]

    def validate(self, value: Number) -> None:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{self.label}: expected a finite number")
        if self.kind == "page":
            raise ValueError(f"{self.label}: a group has no editable value")
        if self.kind != "float" and int(value) != value:
            raise ValueError(f"{self.label}: expected an integer")
        if self.kind == "enum":
            if not 0 <= value < len(self.options):
                raise ValueError(f"{self.label}: invalid choice")
        else:
            low, high = self.constraints["min"], self.constraints["max"]
            if self.kind == "float":
                value, low, high = float32(value), float32(low), float32(high)
            if not low <= value <= high:
                raise ValueError(f"{self.label}: expected {low}..{high}")


@dataclass(frozen=True)
class ObjectSpec:
    key: str
    label: str
    position: int
    visible: bool
    flags: int
    dpt: str
    size_code: int
    condition: Condition
    types: tuple[tuple[Condition, str, int], ...]


@dataclass(frozen=True)
class Function:
    key: str
    label: str
    code: int
    config_offset: int
    fields: tuple[Field, ...]
    objects: tuple[ObjectSpec, ...]


@lru_cache(maxsize=1)
def functions() -> Mapping[str, Function]:
    raw = json.loads(files(__package__).joinpath("schema.json").read_text())
    result: dict[str, Function] = {}
    for key, data in raw.items():
        fields_ = tuple(
            Field(
                p["key"],
                p["label"],
                p["kind"],
                p["storage"],
                p["address"],
                p["size"],
                p["mask"],
                p["shift"],
                p["default"],
                p["visible"],
                Condition.parse(p["condition"]),
                p["parent"],
                tuple((o["label"], int(o["value"])) for o in p["options"]),
                MappingProxyType(p["constraints"]),
            )
            for p in data["fields"]
        )
        objects = tuple(
            ObjectSpec(
                o["key"],
                o["label"],
                o["position"],
                o["visible"],
                o["flags"],
                o["dpt"],
                o["size_code"],
                Condition.parse(o["condition"]),
                tuple((Condition.parse(c), d, s) for c, d, s in o["types"]),
            )
            for o in data["objects"]
        )
        result[key] = Function(
            key, data["label"], data["code"], data["config_offset"], fields_, objects
        )
    return MappingProxyType(result)


def float32(value: Number) -> float:
    try:
        return struct.unpack(">f", struct.pack(">f", value))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError("number exceeds float32 range") from exc


def timer_code(milliseconds: int) -> int:
    if milliseconds == 0:
        return 0
    candidates = [(milliseconds // (5 << i), i) for i in range(24)]
    valid = [(factor, i) for factor, i in candidates if 1 <= factor <= 127]
    if not valid:
        raise ValueError("time cannot be represented")
    factor, index = max(valid)
    return index << 8 | factor


@dataclass(frozen=True)
class ChannelConfig:
    function: str
    values: Mapping[str, Number] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        if self.function not in functions():
            raise ValueError(f"unsupported function: {self.function}")
        fields_ = {f.key: f for f in functions()[self.function].fields}
        for key, value in self.values.items():
            if key not in fields_:
                raise ValueError(f"unknown field: {key}")
            if not fields_[key].visible:
                raise ValueError(f"field is not editable: {key}")
            fields_[key].validate(value)


@dataclass(frozen=True)
class PowerToolConfig:
    variant: Variant
    channels: tuple[ChannelConfig, ...]
    bindings: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    schema_version: int = 1
    database_version: str = DATABASE_VERSION
    object_flags: Mapping[str, int] = field(default_factory=lambda: {})

    def __post_init__(self) -> None:
        object.__setattr__(self, "bindings", MappingProxyType(dict(self.bindings)))
        object.__setattr__(
            self, "object_flags", object_flag_overrides(self.object_flags)
        )
        if self.schema_version != 1 or self.database_version != DATABASE_VERSION:
            raise ValueError("unsupported configuration version")
        if self.variant not in ("01", "02") or len(self.channels) != (
            3 if self.variant == "01" else 4
        ):
            raise ValueError("invalid variant or channel count")
        for index, channel in enumerate(self.channels):
            if channel.function not in channel_functions(self.variant, index):
                raise ValueError(
                    f"function {channel.function} is unavailable on channel {index}"
                )
        if set(self.object_flags) - {
            f"channel_{index}/{channel.function}/{obj.key}"
            for index, channel in enumerate(self.channels)
            for obj in functions()[channel.function].objects
        }:
            raise ValueError("PowerTool flags refer to an unknown object")
        if len(set(self.bindings.values())) != len(self.bindings):
            raise ValueError("duplicate object binding")


def channel_functions(variant: Variant, index: int) -> tuple[str, ...]:
    if variant == "01" and index == 2:
        return ("led", "inactive")
    return tuple(
        k
        for k in functions()
        if k != "led" and (index % 2 == 0 or not k.endswith("_rocker"))
    )


def default_config(variant: Variant) -> PowerToolConfig:
    selected = (
        ("switch_rocker", "inactive", "led")
        if variant == "01"
        else ("switch_rocker", "inactive", "switch_rocker", "inactive")
    )
    return PowerToolConfig(variant, tuple(ChannelConfig(f) for f in selected))


def channel_exists(config: PowerToolConfig, index: int) -> bool:
    return index % 2 == 0 or config.channels[index - 1].function.endswith("_button")


def channel_number(variant: Variant, index: int) -> int:
    return 0x3E0000 + ((2, 3, 1) if variant == "01" else (2, 3, 258, 259))[index]


class EvaluatedChannel:
    def __init__(self, config: ChannelConfig) -> None:
        self.config = config
        self.function = functions()[config.function]
        self._exists: dict[int, bool] = {}

    def condition(self, condition: Condition) -> bool:
        op = condition.operator
        if op == "always":
            return True
        if op == "exists":
            return self.exists(condition.index)
        if op == "all":
            return all(self.condition(c) for c in condition.children)
        if op == "any":
            return any(self.condition(c) for c in condition.children)
        value = self.stored_value(condition.index)
        expected = condition.value
        return {
            "eq": value == expected,
            "ne": value != expected,
            "gt": value > expected,
            "ge": value >= expected,
            "lt": value < expected,
            "le": value <= expected,
        }[op]

    def exists(self, index: int) -> bool:
        if index not in self._exists:
            self._exists[index] = self.condition(self.function.fields[index].condition)
        return self._exists[index]

    def value(self, index: int) -> Number:
        f = self.function.fields[index]
        if f.default is None:
            return 0
        return self.config.values.get(f.key, f.default) if f.visible else f.default

    def stored_value(self, index: int) -> Number:
        f = self.function.fields[index]
        value = self.value(index)
        if f.kind in ("unsigned", "signed"):
            return (int(value) << f.shift) & f.mask
        if f.kind == "freetimer":
            return timer_code(int(value))
        return value

    def image_value(self, index: int) -> Number:
        f = self.function.fields[index]
        value = self.stored_value(index)
        if f.kind == "enum":
            return (f.options[int(value)][1] << f.shift) & f.mask
        if f.kind == "unsigned":
            factor = float32(f.constraints.get("factor", 1))
            product = value * factor
            return int(float32(product) if factor != int(factor) else product)
        return value


def edit_config(
    config: PowerToolConfig, channel: int, key: str, value: str
) -> PowerToolConfig:
    if not 0 <= channel < len(config.channels) or not channel_exists(config, channel):
        raise ValueError("channel is unavailable")
    channels = list(config.channels)
    current = channels[channel]
    if key == "function":
        if value == current.function:
            return config
        channels[channel] = ChannelConfig(value)
    else:
        evaluated = EvaluatedChannel(current)
        match = next(
            ((i, f) for i, f in enumerate(evaluated.function.fields) if f.key == key),
            None,
        )
        if match is None or not match[1].visible or not evaluated.exists(match[0]):
            raise ValueError("field is not editable in this configuration")
        number = float(value) if match[1].kind == "float" else int(value)
        match[1].validate(number)
        channels[channel] = replace(current, values={**current.values, key: number})
    return replace(
        config,
        channels=tuple(channels),
        object_flags={
            role: flags
            for role, flags in config.object_flags.items()
            if key != "function" or not role.startswith(f"channel_{channel}/")
        },
    )

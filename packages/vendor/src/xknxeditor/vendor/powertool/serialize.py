"""Versioned native configuration persistence."""

from __future__ import annotations

import json
from typing import Any

from .model import BLOB_NAME, ChannelConfig, PowerToolConfig


def to_json_bytes(config: PowerToolConfig) -> bytes:
    return (
        json.dumps(
            {
                "format": BLOB_NAME,
                "schema_version": config.schema_version,
                "database_version": config.database_version,
                "variant": config.variant,
                "channels": [
                    {"function": c.function, "values": dict(c.values)}
                    for c in config.channels
                ],
                "bindings": dict(config.bindings),
                **(
                    {"object_flags": dict(config.object_flags)}
                    if config.object_flags
                    else {}
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate configuration key: {key}")
        result[key] = value
    return result


def from_json_bytes(data: bytes) -> PowerToolConfig:
    try:
        raw = json.loads(data, object_pairs_hook=unique_members)
        if (
            set(raw) - {"object_flags"}
            != {
                "format",
                "schema_version",
                "database_version",
                "variant",
                "channels",
                "bindings",
            }
            or raw["format"] != BLOB_NAME
        ):
            raise ValueError("unknown native configuration format")
        if any(set(c) != {"function", "values"} for c in raw["channels"]):
            raise ValueError("invalid channel configuration")
        if not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in raw["bindings"].items()
        ):
            raise ValueError("invalid object bindings")
        return PowerToolConfig(
            raw["variant"],
            tuple(ChannelConfig(**c) for c in raw["channels"]),
            raw["bindings"],
            raw["schema_version"],
            raw["database_version"],
            raw.get("object_flags", {}),
        )
    except (
        TypeError,
        KeyError,
        AttributeError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid native configuration") from exc

"""Shared technical metadata for image encoding and saved project inspection."""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

_PREFIX = "BJE.toolB.ProductDatabase."
_NODE_FIELDS = {
    "device": ("parameters", "channels", "children", "choices", "subsystems"),
    "channel": ("parameters", "channels", "applications", "number"),
    "application": ("parameters", "function", "smart_touch", "compiler"),
    "parameter": (
        "value_kind",
        "display_order",
        "default",
        "size",
        "mask",
        "shift",
        "display_offset",
        "parameters",
    ),
}
_REFERENCES = {
    "parameters",
    "channels",
    "children",
    "choices",
    "subsystems",
    "applications",
}


@lru_cache(maxsize=1)
def _device_model() -> dict[str, Any]:
    model = json.loads(
        gzip.decompress(
            files(__package__).joinpath("device-model.json.gz").read_bytes()
        )
    )
    if model["format"] != 1:
        raise ValueError("Unsupported PowerTool device model")
    return model


def decision_tables() -> dict[str, Any]:
    """The shared predicates and branches used by all device families."""
    return _device_model()["decisions"]


@lru_cache(maxsize=1)
def family_schemas() -> tuple[dict[str, Any], ...]:
    """Resolve family identities against shared layout and module records."""
    model = _device_model()
    return tuple(
        {
            **family,
            "modules": {
                key: model["modules"][index] for key, index in family["modules"].items()
            },
            "nodes": {
                key: {
                    **model["layouts"][binding["layout"]],
                    "rules": binding.get("rules", {}),
                }
                for key, binding in family["nodes"].items()
            },
        }
        for family in model["families"]
    )


@lru_cache(maxsize=1)
def native_nodes() -> dict[str, dict[str, Any]]:
    """Map native function, field and object keys to product class identifiers."""
    return json.loads(
        files(__package__).joinpath("native-map.json").read_text(encoding="utf-8")
    )


def _qualified(value: Any) -> Any:
    if isinstance(value, list):
        return [_qualified(item) for item in cast(list[Any], value)]
    return _PREFIX + value


@lru_cache(maxsize=1)
def reader_catalog() -> tuple[dict[str, Any], ...]:
    """Derive the saved-project layout, retaining serialized order and choice counts."""
    native = {
        spec["node"].removeprefix(_PREFIX): function
        for function, spec in native_nodes().items()
    }
    result: list[dict[str, Any]] = []
    for schema in sorted(family_schemas(), key=lambda item: item["database"]):
        nodes: dict[str, dict[str, Any]] = {}
        for key, spec in schema["nodes"].items():
            kind = spec["kind"]
            if kind not in _NODE_FIELDS:
                continue
            node = {"kind": kind}
            for name in _NODE_FIELDS[kind]:
                value = spec[name]
                node[name] = _qualified(value) if name in _REFERENCES else value
            if kind == "parameter":
                node["mask"] = str(spec["mask"])
                node["visibility"] = (
                    "conditional"
                    if "Visible" in spec["rules"]
                    else "visible"
                    if spec["visible"]
                    else "hidden"
                )
                if "options" in spec:
                    node["options"] = [""] * len(spec["options"])
            if kind == "application":
                compiler = spec["compiler"]
                if key in ("GUI_VirtuellStatuszeile", "GUI_VirtuellStatuszeile_2"):
                    compiler = key
                namespace = (
                    _PREFIX
                    if compiler.startswith(("App_", "GUI_"))
                    else "BJE.toolB.ProductData."
                )
                node["compiler"] = namespace + compiler
                if schema["code"] == 3 and key in native:
                    node["native_function"] = native[key]
            if spec.get("special"):
                node["special"] = spec["special"]
                node["max_channels"] = spec["max_channels"]
            nodes[_PREFIX + key] = node
        result.append(
            {
                "database": schema["database"],
                "version": schema["version"],
                "code": schema["code"],
                "products": {
                    profile: _qualified(roots)
                    for profile, roots in schema["products"].items()
                },
                "nodes": nodes,
            }
        )
    return tuple(result)

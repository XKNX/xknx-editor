"""Unified native configuration entry points for the PowerTool provider."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from xknxeditor.prod import Application

from .codec import (
    DEFAULT_HOST,
    Association,
    CompiledPowerToolImage,
    HostProfile,
    compile_image,
)
from .family import FamilyConfig, FamilyEvaluation, edit, from_saved, schema_for
from .family_codec import PROFILES, compile_family
from .model import BLOB_NAME, PowerToolConfig, default_config, edit_config
from .prepare import product_parameters, profile_for
from .project_blob import config_from_project_blob, read_project_blob
from .serialize import from_json_bytes, to_json_bytes, unique_members

type Configuration = PowerToolConfig | FamilyConfig


def profile(config: Configuration) -> str:
    if isinstance(config, FamilyConfig):
        if config.profile in PROFILES and config.database_code in (
            None,
            PROFILES[config.profile][3],
        ):
            return config.profile
        if config.database_code is not None:
            return f"{config.database_code}:{config.profile}"
        return config.profile
    return config.variant


def product_profile(application: Application, secondary: Application | None) -> str:
    parameters = product_parameters(application)
    product = parameters.get("ProductType", "")
    code = int(parameters.get("PTDatabaseCode", "0"))
    if code == 3 and product in ("TasterBA1f", "TasterBA2f"):
        return profile_for(application, secondary)
    legacy = PROFILES.get(product)
    if (
        legacy is None
        or code != legacy[3]
        or (
            application.program.application_number,
            application.program.application_version,
        )
        != legacy[:2]
    ):
        schema_for(product, code)
        if secondary is None:
            raise ValueError("Missing PowerTool secondary application")
        masks: set[str] = set()
        for app, kind in (
            (application, "ApplicationProgram"),
            (secondary, "PeiProgram"),
        ):
            program = app.program
            number, version = program.application_number, program.application_version
            mask = str(program.mask_version).upper().removeprefix("MV-")
            if (
                app.manufacturer_id.upper() != "M-0007"
                or not app.id.upper().startswith(
                    f"M-0007_A-{number:04X}-{version:02X}-"
                )
                or getattr(program.program_type, "value", program.program_type) != kind
                or mask not in ("07B0", "17B0")
            ):
                raise ValueError("Mismatched PowerTool program identity")
            masks.add(mask)
        if (
            len(masks) != 1
            or parameters.get("BAUBVersion") not in ("0.1.0", "0.1.5")
            or application.program.application_number
            != secondary.program.application_number
            or application.program.application_version
            == secondary.program.application_version
        ):
            raise ValueError("Unverified PowerTool host profile")
        return (
            product if legacy is not None and code == legacy[3] else f"{code}:{product}"
        )
    number, main, pei, code = PROFILES[product]
    if secondary is None:
        raise ValueError("Missing PowerTool secondary application")
    for app, version in ((application, main), (secondary, pei)):
        program = app.program
        if (
            app.manufacturer_id.upper() != "M-0007"
            or not app.id.upper().startswith(f"M-0007_A-{number:04X}-{version:02X}-")
            or program.application_number != number
            or program.application_version != version
            or str(program.mask_version).upper() not in ("MV-07B0", "07B0")
        ):
            raise ValueError("Mismatched PowerTool program identity")
    if (
        parameters.get("PTDatabaseCode") != str(code)
        or parameters.get("BAUBVersion") != "0.1.0"
    ):
        raise ValueError("Unverified PowerTool host profile")
    return product


def default(profile: str) -> Configuration:
    if profile in ("01", "02"):
        return default_config(cast(Any, profile))
    if ":" in profile:
        code, name = profile.split(":", 1)
        return FamilyConfig(name, database_code=int(code))
    return FamilyConfig(profile)


def for_product(
    config: Configuration, application: Application, secondary: Application | None
) -> Configuration:
    if profile(config) != product_profile(application, secondary):
        raise ValueError("Configuration does not match the product")
    if not isinstance(config, FamilyConfig):
        return config
    assert secondary is not None
    ids = tuple(
        (
            b"\0\x07"
            + app.program.application_number.to_bytes(2, "big")
            + bytes([app.program.application_version])
        ).hex()
        for app in (application, secondary)
    )
    mask = int(str(application.program.mask_version).upper().removeprefix("MV-"), 16)
    if config.program_ids is not None and (
        config.program_ids != ids or config.mask_version != mask
    ):
        raise ValueError("PowerTool application identifiers do not match the product")
    products = config.products
    if config.database_code is None:
        state = FamilyEvaluation(config)
        products = {
            path: choices.index(state.nodes[path].key)
            for path, choices in state.product_choices.items()
        }
    return replace(
        config,
        database_code=int(product_parameters(application)["PTDatabaseCode"]),
        program_ids=(ids[0], ids[1]),
        mask_version=mask,
        products=products,
    )


def load(data: bytes) -> Configuration:
    if len(data) > 8_000_000:
        raise ValueError("PowerTool configuration is too large")
    try:
        decoded: object = json.loads(data, object_pairs_hook=unique_members)
        if not isinstance(decoded, dict):
            raise ValueError("Invalid PowerTool configuration")
        raw = cast(dict[str, Any], decoded)
        if raw.get("family_profile") is not None:
            qualified = raw.get("schema_version") == 3
            extra: set[str] = (
                {
                    "database_code",
                    "products",
                    "channel_counts",
                    "program_ids",
                    "mask_version",
                }
                if qualified
                else set()
            )
            if (
                set(raw) - {"object_flags"} - ({"channel_ids"} if qualified else set())
                != {
                    "format",
                    "schema_version",
                    "family_profile",
                    "options",
                    "values",
                    "bindings",
                }
                | extra
                or raw["format"] != BLOB_NAME
                or raw["schema_version"] not in (2, 3)
                or type(raw["family_profile"]) is not str
                or any(
                    type(raw[k]) is not dict for k in ("options", "values", "bindings")
                )
                or (
                    qualified
                    and (
                        type(raw["database_code"]) is not int
                        or type(raw["products"]) is not dict
                        or type(raw["channel_counts"]) is not dict
                        or type(raw.get("channel_ids", {})) is not dict
                        or type(raw["mask_version"]) is not int
                        or (
                            raw["program_ids"] is not None
                            and type(raw["program_ids"]) is not list
                        )
                    )
                )
            ):
                raise ValueError("Unknown PowerTool family configuration format")
            ids: Any = raw.get("program_ids")
            if ids is not None and (
                len(ids) != 2 or any(type(v) is not str for v in ids)
            ):
                raise ValueError("Invalid PowerTool application identifiers")
            config = FamilyConfig(
                raw["family_profile"],
                raw["options"],
                raw["values"],
                raw["bindings"],
                database_code=raw.get("database_code"),
                products=raw.get("products", {}),
                channel_counts=raw.get("channel_counts", {}),
                channel_ids=raw.get("channel_ids", {}),
                program_ids=(ids[0], ids[1]) if ids is not None else None,
                mask_version=raw.get("mask_version", 0x07B0),
                object_flags=raw.get("object_flags", {}),
            )
            FamilyEvaluation(config)
            return config
        return from_json_bytes(data)
    except (TypeError, AttributeError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid PowerTool configuration") from exc


def dump(config: Configuration) -> bytes:
    if isinstance(config, PowerToolConfig):
        return to_json_bytes(config)
    if config.database_code is None and (
        config.products or config.channel_counts or config.channel_ids
    ):
        raise ValueError("PowerTool hardware selections require a database identity")
    return (
        json.dumps(
            {
                "format": BLOB_NAME,
                "schema_version": 3 if config.database_code is not None else 2,
                "family_profile": config.profile,
                "options": dict(config.options),
                "values": dict(config.values),
                "bindings": dict(config.bindings),
                **(
                    {"object_flags": dict(config.object_flags)}
                    if config.object_flags
                    else {}
                ),
                **(
                    {
                        "database_code": config.database_code,
                        "products": dict(config.products),
                        "channel_counts": dict(config.channel_counts),
                        "channel_ids": dict(config.channel_ids),
                        "program_ids": config.program_ids,
                        "mask_version": config.mask_version,
                    }
                    if config.database_code is not None
                    else {}
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def from_original(data: bytes) -> Configuration:
    saved = read_project_blob(data)
    if saved.code == 3 and saved.product_type in ("TasterBA1f", "TasterBA2f"):
        return config_from_project_blob(data)
    return from_saved(data, qualified=True)


def rebase(config: Configuration) -> Configuration:
    """Make a newly written blob the source for the next edit's channel identities."""
    if isinstance(config, PowerToolConfig):
        return config
    state = FamilyEvaluation(config)
    roles = {
        f"{config.channel_ids.get(channel.path, channel.path)}/{channel.application.key}/{obj.key}": f"{channel.path}/{channel.application.key}/{obj.key}"
        for channel in state.channels
        if channel.application is not None
        for obj in channel.application.objects
    }
    if config.bindings.keys() - roles.keys():
        raise ValueError("PowerTool binding refers to an unknown object")
    if not config.channel_ids:
        return config
    return replace(
        config,
        channel_ids={},
        bindings={roles[key]: value for key, value in config.bindings.items()},
        object_flags={roles[key]: value for key, value in config.object_flags.items()},
    )


def compile_native(
    config: Configuration,
    host: HostProfile = DEFAULT_HOST,
    associations: Sequence[Association] = (),
    flags: Mapping[str, int] | None = None,
) -> CompiledPowerToolImage:
    if isinstance(config, FamilyConfig):
        host = replace(host, mask_version=config.mask_version)
        return compile_family(config, host, associations, flags)
    return compile_image(config, host, associations, flags)


def edit_native(config: Configuration, reference: str, value: str) -> Configuration:
    editable = replace(config, object_flags={}) if config.object_flags else config
    if isinstance(config, FamilyConfig):
        prefix = "powertool:family:"
        if not reference.startswith(prefix):
            raise ValueError("Invalid PowerTool family parameter reference")
        assert isinstance(editable, FamilyConfig)
        updated = edit(editable, reference.removeprefix(prefix), value)
    else:
        parts = reference.split(":")
        if len(parts) != 3 or parts[0] != "powertool":
            raise ValueError("Invalid PowerTool parameter reference")
        assert isinstance(editable, PowerToolConfig)
        updated = edit_config(editable, int(parts[1]), parts[2], value)
    if updated == editable:
        return config
    if not config.object_flags:
        return updated
    old = {o.key: (o.size_code, o.dpt) for o in compile_native(config).objects}
    return replace(
        updated,
        object_flags={
            o.key: config.object_flags[o.key]
            for o in compile_native(updated).objects
            if o.key in config.object_flags and old.get(o.key) == (o.size_code, o.dpt)
        },
    )


def channel_labels(config: Configuration) -> list[str]:
    if isinstance(config, FamilyConfig):
        return [c.spec["label"] for c in FamilyEvaluation(config).channels]
    return [f"Channel {i + 1}" for i in range(len(config.channels))]

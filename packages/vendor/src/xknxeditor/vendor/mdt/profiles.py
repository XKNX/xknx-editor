"""Application identities registered by the configuration apps."""

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Literal


@dataclass(frozen=True)
class DcaProfile:
    app_id: str
    kind: Literal["symbols", "layout", "visucontrol"]
    slots: int = 0
    first_custom_slot: int = 0
    symbol_roles: tuple[str | None, ...] = ()


_PROFILES = (
    DcaProfile(
        "M0083-A0041",
        "symbols",
        36,
        2,
        (
            "special",
            "special",
            "automatic",
            "comfort",
            "standby",
            "night",
            "frost_protection",
            "heat_protection",
            "temperature",
        ),
    ),
    DcaProfile(
        "M0083-A00D1",
        "symbols",
        20,
        0,
        (
            None,
            None,
            None,
            None,
            None,
            "temperature",
            "comfort",
            "standby",
            "night",
            "frost_protection",
            "heat_protection",
            "cooling",
            "heating",
        ),
    ),
    DcaProfile("M0083-A0228", "layout"),
    DcaProfile("M0083-A026F", "visucontrol"),
)


@cache
def _applications() -> dict[str, tuple[str, ...]]:
    data = json.loads(files(__package__).joinpath("applications.json").read_text())
    return {key: tuple(value) for key, value in data.items()}


def profile_for_application(application_id: str) -> DcaProfile | None:
    """Match manufacturer, program and version; the trailing value is a checksum."""
    match = re.fullmatch(
        r"(M-0083_A-[0-9A-F]{4}-[0-9A-F]{2})-[0-9A-F]{4}(?:-O[0-9A-F]{4})?",
        application_id,
    )
    if match is None:
        return None
    identity = match[1]
    return next((p for p in _PROFILES if identity in _applications()[p.app_id]), None)

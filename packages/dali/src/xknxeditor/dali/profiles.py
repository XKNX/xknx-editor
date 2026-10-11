"""Vendor/firmware profiles for the Type-A (property-table) DALI commissioning protocol.

MDT (eelectron firmware) and Theben P64 (INTESIS/Dalie64) speak the same DALI-object property protocol
(IDs 203-228, Hawk transport); they differ only in the KNX interface-object index base and the ETS
application-id prefixes that gate the commissioning tab. A profile captures those per-vendor deltas so
one commissioner/connection serves both. Other DALI DCAs (GIRA parameter-centric, Siemens memory-write
/ protobuf) use different protocols and are NOT Type-A — they are intentionally absent here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DaliProfile:
    """A Type-A DALI gateway family: how to recognise it and where its DALI object sits."""

    key: str  # short vendor key, e.g. "mdt" / "theben"
    app_id_prefixes: tuple[str, ...]  # matched as a prefix of the full Application.id
    object_index_base: int  # KNX object index of DALI channel 0 (firmware object is 0)


PROFILES: tuple[DaliProfile, ...] = (
    # MDT DALI Control (1x64 / 2x64 + presence variants); DALI object at 6 + channel.
    DaliProfile(
        "mdt", tuple(f"M-0083_A-{n}" for n in ("0153", "0154", "0155", "0158")), 6
    ),
    # Theben DALI-Gateway P64 KNX V2 (INTESIS/Dalie64); DALI object at 7 + channel.
    DaliProfile("theben", ("M-0048_A-DA11",), 7),
)


def profile_for(app_id: str | None) -> DaliProfile | None:
    """The Type-A profile whose prefix matches ``app_id``, or None if it is not a Type-A DALI app."""
    if not app_id:
        return None
    upper = app_id.upper()
    for profile in PROFILES:
        if any(upper.startswith(prefix.upper()) for prefix in profile.app_id_prefixes):
            return profile
    return None

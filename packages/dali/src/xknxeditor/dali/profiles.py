"""Vendor/firmware profiles for the Type-A (property-table) DALI commissioning protocol.

MDT, Theben P64 and IPAS DaliControl e64 Pro use related DALI-object property protocols.
Profiles specify their object indices, property IDs and record sizes.

Devices are matched by their known application prefixes. Load procedure object indices
alone do not distinguish the different DALI property layouts.

Other DALI DCAs (GIRA parameter-centric, Siemens memory-write / protobuf) use different
protocols and are NOT Type-A — they are intentionally absent here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DaliProfile:
    """A Type-A DALI gateway family: how to recognise it and where its DALI object sits."""

    key: str  # short vendor key, e.g. "mdt" / "theben"
    app_id_prefixes: tuple[str, ...]  # matched as a prefix of the full Application.id
    object_index_base: int  # KNX object index of DALI channel 0 (firmware object is 0)
    # Per-PID element-size overrides when the vendor deviates from MDT defaults; the
    # Theben P64 V2 chassis (shared by Theben + IPAS) encodes GROUP_STATUS as 10 bytes
    # per element instead of MDT's 8. Keys are the raw ``PropertyType`` integer, so profiles
    # stay independent of the :mod:`xknxeditor.dali.properties` import graph.
    element_size_overrides: tuple[tuple[int, int], ...] = ()
    # BinaryData entry base name the vendor's app stores its channel state in. MDT uses the
    # gc16 blob; the Theben/IPAS e64pro chassis uses the Dalie64 blob.
    backup_blob_base: str = "DaliGC16-Backup-Store"
    property_id_overrides: tuple[tuple[int, int], ...] = ()
    supports_extended_templates: bool = False


def backup_blob_name(base: str, channel: int) -> str:
    """BinaryData ``Name`` for ``channel`` (0-based): ``<base>`` or ``<base>.N`` for channel N."""
    return base if channel == 0 else f"{base}.{channel}"


PROFILES: tuple[DaliProfile, ...] = (
    # MDT DALI Control (1x64 / 2x64 + presence variants); DALI object at 6 + channel.
    DaliProfile(
        "mdt", tuple(f"M-0083_A-{n}" for n in ("0153", "0154", "0155", "0158")), 6
    ),
    # Theben DALI-Gateway P64 KNX V2 (INTESIS/Dalie64); DALI object at 7 + channel.
    # Byte-count delta vs. MDT: Group_Status is 10 bytes per element (not 8).
    DaliProfile(
        "theben",
        ("M-0048_A-DA11",),
        7,
        element_size_overrides=((208, 10),),  # PropertyType.GROUP_STATUS -> 10 bytes
        backup_blob_base="Dalie64-Backup-Store",
        property_id_overrides=((212, 213),),
        supports_extended_templates=True,
    ),
    # IPAS DaliControl e64 Pro — same OEM chassis as Theben P64 V2 (identical module
    # layout and ObjIdx set). A real IPAS project confirms the e64pro blob is byte-for-byte
    # the Theben Dalie64 structure, so project persistence is verified; the bus commissioning
    # protocol itself has no IPAS capture yet.
    DaliProfile(
        "ipas",
        ("M-00EF_A-DA11",),
        7,
        element_size_overrides=((208, 10),),
        backup_blob_base="Dalie64-Backup-Store",
        property_id_overrides=((212, 213),),
        supports_extended_templates=True,
    ),
)


def profile_for(
    app_id: str | None, *, application_xml: str | bytes | None = None
) -> DaliProfile | None:
    """The Type-A profile for ``app_id``, or ``None`` if it is not a Type-A DALI app.

    ``application_xml`` is accepted for compatibility but does not select a profile:
    different families share the same load procedure object indices.
    """
    if not app_id:
        return None
    upper = app_id.upper()
    for profile in PROFILES:
        if any(upper.startswith(prefix.upper()) for prefix in profile.app_id_prefixes):
            return profile
    return None

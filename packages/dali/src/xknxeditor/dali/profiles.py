"""Vendor/firmware profiles for the Type-A (property-table) DALI commissioning protocol.

MDT (eelectron firmware), Theben P64 (INTESIS/Dalie64) and IPAS DaliControl e64 Pro are
the same OEM chassis under three brands and speak the same DALI-object property protocol
(IDs 203-228, Hawk transport); they differ only in the KNX interface-object index base
and the ETS application-id prefixes that gate the commissioning tab.

Devices are matched in two layers:

1. **Fast path** — hardcoded ``PROFILES`` tuple with the known prefixes. Covers the three
   OEM brands we have shipped against.
2. **Signature path** — if ``profile_for`` is given an ``xml`` payload, we fingerprint
   the application's LoadProcedures and match the P64-chassis signature (one
   ``LdCtrlWriteProp`` on ``ObjIdx=7`` plus the standard 1-4 segments). Any future
   OEM-rebrand that keeps the same signature will register automatically without needing
   its prefix hardcoded here.

Other DALI DCAs (GIRA parameter-centric, Siemens memory-write / protobuf) use different
protocols and are NOT Type-A — they are intentionally absent here.
"""

from __future__ import annotations

import re
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
    # Hardware-protocol variants we have not been able to validate against a captured
    # commissioning session — mark the profile ``unverified=True`` so the GUI can surface
    # a warning before any write hits the bus.
    unverified: bool = False
    # BinaryData entry base name the vendor's app stores its channel state in. MDT uses the
    # gc16 blob; the Theben/IPAS e64pro chassis uses the Dalie64 blob.
    backup_blob_base: str = "DaliGC16-Backup-Store"


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
    ),
    # IPAS DaliControl e64 Pro — same OEM chassis as Theben P64 V2 (identical module
    # layout and ObjIdx set). A real IPAS project confirms the e64pro blob is byte-for-byte
    # the Theben Dalie64 structure, so project persistence is verified; the bus commissioning
    # protocol itself has no IPAS capture yet, so it stays unverified and the GUI warns.
    DaliProfile(
        "ipas",
        ("M-00EF_A-DA11",),
        7,
        element_size_overrides=((208, 10),),
        unverified=True,
        backup_blob_base="Dalie64-Backup-Store",
    ),
)

# Signature of the shared P64 chassis in the ``LoadProcedures``: standard LsmLoadImage
# segments on ObjIdx 1-4 plus one vendor-specific ``LdCtrlWriteProp`` on ObjIdx 7. Any
# knxprod whose top-level application XML carries exactly this fingerprint speaks the
# Theben-family DALI property protocol with object_index_base=7.
_P64_SIGNATURE: frozenset[tuple[str, int]] = frozenset(
    {
        ("LdCtrlLoadImageProp", 1),
        ("LdCtrlLoadImageProp", 2),
        ("LdCtrlLoadImageProp", 3),
        ("LdCtrlLoadImageProp", 4),
        ("LdCtrlWriteProp", 7),
    }
)
_P64_SIGNATURE_PROFILE = DaliProfile(
    "p64-chassis",
    (),
    7,
    element_size_overrides=((208, 10),),
    unverified=True,
    backup_blob_base="Dalie64-Backup-Store",
)
_LD_CTRL_OBJIDX_RE = re.compile(r'<(Ld\w+)[^>]+ObjIdx="(\d+)"')


def profile_for(
    app_id: str | None, *, application_xml: str | bytes | None = None
) -> DaliProfile | None:
    """The Type-A profile for ``app_id``, or ``None`` if it is not a Type-A DALI app.

    When ``application_xml`` is given, its ``LdCtrl*/@ObjIdx`` set is matched against
    ``_P64_SIGNATURE`` first; a hit returns an anonymous profile (``key="p64-chassis"``)
    with ``object_index_base=7``. The hardcoded ``PROFILES`` tuple is tried last so that
    an unknown OEM rebrand of the same hardware is picked up without a new PR.
    """
    if application_xml is not None:
        xml_text = (
            application_xml.decode("utf-8", "replace")
            if isinstance(application_xml, bytes)
            else application_xml
        )
        hits = {(elem, int(idx)) for elem, idx in _LD_CTRL_OBJIDX_RE.findall(xml_text)}
        if _P64_SIGNATURE.issubset(hits):
            return _P64_SIGNATURE_PROFILE
    if not app_id:
        return None
    upper = app_id.upper()
    for profile in PROFILES:
        if any(upper.startswith(prefix.upper()) for prefix in profile.app_id_prefixes):
            return profile
    return None

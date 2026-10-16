"""Learned classic-memory-service caps, keyed by application id.

Some device firmwares advertise a larger APDU than they accept for classic memory
writes. A download discovers the real limit through its no-ACK fallback; remembering
it per application lets later downloads of the same product seed the working cap and
skip the discovery retry. Stored in a small JSON cache next to the other settings.
"""

from __future__ import annotations

from .settings import load_settings, save_settings

_STORE = "memory_caps"


def learned_memory_cap(application_id: str | None) -> int | None:
    """The memory-service cap previously found to work for this application, if any."""
    if not application_id:
        return None
    value = load_settings(_STORE).get(application_id)
    return value if isinstance(value, int) else None


def remember_memory_cap(application_id: str | None, cap: int | None) -> None:
    """Store the memory-service cap a successful download settled on.

    ``cap is None`` means the APDU-derived default worked, so nothing is remembered.
    """
    if not application_id or cap is None:
        return
    data = load_settings(_STORE)
    if data.get(application_id) == cap:
        return
    data[application_id] = cap
    save_settings(_STORE, data)

"""Learned maximum APDU lengths of interfaces that do not report theirs.

A tunnelling v1 server (e.g. knxd) does not answer the max APDU length feature query.
When a download through it fails with long frames and its retry with standard frames
works, the interface evidently cannot carry long frames (knxd with a TPUART even takes
its link down on one). Remembering that per interface lets later connections to it start
with standard frames, so no further download has to fail first. Stored in a small JSON
cache next to the other settings.
"""

from __future__ import annotations

from .settings import load_settings, save_settings

_STORE = "interface_apdu"


def learned_interface_apdu(interface: str | None) -> int | None:
    """The maximum APDU length learned for this interface (its connection target), if any."""
    if not interface:
        return None
    value = load_settings(_STORE).get(interface)
    return value if isinstance(value, int) else None


def remember_interface_apdu(interface: str | None, length: int | None) -> None:
    """Store the maximum APDU length ``interface`` carries (found by a download, or set by
    the user)."""
    if not interface or length is None:
        return
    data = load_settings(_STORE)
    if data.get(interface) == length:
        return
    data[interface] = length
    save_settings(_STORE, data)


def forget_interface_apdu(interface: str | None) -> None:
    """Drop the maximum APDU length stored for ``interface``: downloads through it negotiate
    the length from the device again."""
    if not interface:
        return
    data = load_settings(_STORE)
    if interface not in data:
        return
    del data[interface]
    save_settings(_STORE, data)

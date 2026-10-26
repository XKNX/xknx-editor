"""The IP settings of an IP device: its ``IPConfig`` in the project.

``Assign`` says how the device gets its IP address: ``Auto`` (DHCP/AutoIP, the default) or
``Fixed``. ``IPAddress`` is the fixed address, and with ``Auto`` the address the device was last
assigned; ``SubnetMask`` and ``DefaultGateway`` belong to a fixed address. ``MACAddress`` is read
from the device by a download. Changing how the address is assigned keeps the addresses.
"""

from __future__ import annotations

ASSIGN_AUTO = "Auto"
ASSIGN_FIXED = "Fixed"
ASSIGNMENTS = (ASSIGN_AUTO, ASSIGN_FIXED)
# The address an empty "Assigned IP Address" stores.
NO_ADDRESS = "0.0.0.0"
# Addresses that stand for "not known": shown as unknown, never as an address.
UNKNOWN_ADDRESSES = frozenset({NO_ADDRESS, "255.255.255.255"})
# The value a missing address of a fixed assignment shows.
NONE_ADDRESS = "255.255.255.255"

_DIGITS = "0123456789abcdef"


def parse_ipv4(text: str) -> str:
    """``text`` as a dotted IPv4 address ("192.168.1.10").

    Read like the usual address parsers: one to four parts separated by dots, each decimal,
    hexadecimal (``0x1f``) or octal (a leading ``0``); the last part fills the octets left, so
    "192.168.1" is 192.168.0.1 and "1" is 0.0.0.1. Raises ``ValueError`` for anything else.
    """
    parts = text.strip().split(".")
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"invalid IP address: {text!r}")
    values = [_part(part, text) for part in parts]
    remaining = 5 - len(values)
    if any(value > 0xFF for value in values[:-1]) or values[-1] >= 1 << (8 * remaining):
        raise ValueError(f"invalid IP address: {text!r}")
    octets = [*values[:-1], *values[-1].to_bytes(remaining, "big")]
    return ".".join(str(octet) for octet in octets)


def _part(part: str, text: str) -> int:
    lowered = part.lower()
    if lowered.startswith("0x"):
        digits, base = lowered[2:], 16
    elif len(lowered) > 1 and lowered.startswith("0"):
        digits, base = lowered[1:], 8
    else:
        digits, base = lowered, 10
    if not digits or any(digit not in _DIGITS[:base] for digit in digits):
        raise ValueError(f"invalid IP address: {text!r}")
    return int(digits, base)


def known_address(address: str | None) -> str | None:
    """``address`` unless it is missing or stands for "not known" (0.0.0.0, 255.255.255.255)."""
    return None if address is None or address in UNKNOWN_ADDRESSES else address

"""The text of an IP address parameter value.

An IPv4 address takes one to four parts separated by dots, each decimal, octal (a leading
``0``) or hexadecimal (a leading ``0x``); the last part fills the octets the others leave:
``0`` is ``0.0.0.0``, ``10.1`` is ``10.0.0.1`` and ``0x7f.1`` is ``127.0.0.1``. A text with a
colon is an IPv6 address.
"""

from __future__ import annotations

import ipaddress

_MAX_IPV4 = 0xFFFFFFFF


_DIGITS = {8: "01234567", 10: "0123456789", 16: "0123456789abcdefABCDEF"}


def _ipv4_part(part: str) -> int | None:
    if part[:2] in ("0x", "0X"):
        digits, base = part[2:], 16
    elif part.startswith("0"):
        digits, base = part[1:] or "0", 8
    else:
        digits, base = part, 10
    if not digits or any(c not in _DIGITS[base] for c in digits):
        return None
    return int(digits, base)


def _ipv4(text: str) -> ipaddress.IPv4Address | None:
    parts = text.split(".")
    if len(parts) > 4:
        return None
    values: list[int] = []
    for part in parts:
        value = _ipv4_part(part)
        if value is None or value > _MAX_IPV4:
            return None
        values.append(value)
    *leading, last = values
    if any(value > 0xFF for value in leading):
        return None
    if last >= 1 << (8 * (4 - len(leading))):
        return None
    number = last
    for position, value in enumerate(leading):
        number |= value << (8 * (3 - position))
    return ipaddress.IPv4Address(number)


def parse_ip_address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address a parameter value's text gives, or ``None`` when it gives none."""
    text = text.strip()
    if ":" in text:
        try:
            return ipaddress.IPv6Address(text)
        except ValueError:
            return None
    return _ipv4(text)

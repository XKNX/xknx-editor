"""The Factory Default Setup Key (FDSK) of a KNX Data Secure device, as printed on its label.

The label code is the RFC 4648 base32 text (36 characters, written in groups of six separated by
"-") of the device's KNX serial number (6 octets), the FDSK (16 octets) and a CRC-4 (polynomial
x^4 + x + 1) over both in the upper half of a last octet.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

_CRC4 = (0x0, 0x3, 0x6, 0x5, 0xC, 0xF, 0xA, 0x9, 0xB, 0x8, 0xD, 0xE, 0x7, 0x4, 0x1, 0x2)
_LENGTH = 36
_MISTYPED = str.maketrans({"0": "O", "1": "L", "8": "B"})


class InvalidFdskError(ValueError):
    """The text is not a valid FDSK label code."""


@dataclass(frozen=True, slots=True)
class Fdsk:
    """A device's serial number and its Factory Default Setup Key."""

    serial_number: bytes
    key: bytes

    @property
    def label(self) -> str:
        """The code as printed on the device label, in groups of six characters."""
        crc = _crc4(self.serial_number + self.key)
        data = self.serial_number + self.key + bytes([crc << 4])
        text = base64.b32encode(data).decode("ascii")[:_LENGTH]
        return "-".join(text[i : i + 6] for i in range(0, _LENGTH, 6))


def _crc4(data: bytes) -> int:
    crc = 0
    for octet in data:
        crc = _CRC4[crc ^ (octet >> 4)]
        crc = _CRC4[crc ^ (octet & 0x0F)]
    return crc


def parse_fdsk(text: str) -> Fdsk:
    """The serial number and FDSK of a label code; raises :class:`InvalidFdskError` for a code
    that is malformed or fails its checksum. Separators and white space are ignored."""
    # Base32 has no 0, 1 or 8: read them as the letters they are mistaken for.
    code = "".join(c for c in text.upper() if c.isalnum()).translate(_MISTYPED)
    if len(code) != _LENGTH:
        raise InvalidFdskError(
            f"an FDSK code has {_LENGTH} characters, got {len(code)}"
        )
    try:
        # 36 characters carry 180 bits; pad to the 40 of 25 octets' worth (23 used).
        data = base64.b32decode(code + "AAAA")
    except (binascii.Error, ValueError) as exc:
        raise InvalidFdskError(f"not a base32 FDSK code: {exc}") from exc
    serial_number, key, crc = data[:6], data[6:22], data[22] >> 4
    if _crc4(serial_number + key) != crc:
        raise InvalidFdskError("the FDSK code's checksum does not match")
    return Fdsk(serial_number, key)

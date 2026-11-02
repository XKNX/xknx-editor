"""File versions of baggage files: version resource of Windows programs, ProductVersion of
installer packages."""

from __future__ import annotations

import struct

_FIXED_SIGNATURE = b"\xbd\x04\xef\xfe"
_VS_VERSION_INFO = "VS_VERSION_INFO".encode("utf-16-le")


def file_version(name: str, data: bytes) -> str | None:
    """``major.minor.build.private`` of a program file, the product version of an installer
    package, or None."""
    version = pe_version(data)
    if version is not None:
        return version
    if name.lower().endswith(".msi"):
        from .msi import product_version

        text = product_version(data)
        if text:
            parts = text.split(".")
            while len(parts) < 4:
                parts.append("0")
            return ".".join(parts[:4])
    return None


def pe_version(data: bytes) -> str | None:
    if data[:2] != b"MZ":
        return None
    start = data.find(_VS_VERSION_INFO)
    if start < 0:
        return None
    pos = data.find(_FIXED_SIGNATURE, start)
    if pos < 0 or pos - start > 64:
        return None
    ms, ls = struct.unpack_from("<II", data, pos + 8)
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"

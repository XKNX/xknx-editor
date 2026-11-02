"""Ids of the XML product data as the converter forms them."""

from __future__ import annotations

import unicodedata

_LETTER_OR_DIGIT = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Nd"})


def fragment(text: str) -> str:
    """``text`` as id fragment: letters and digits stay, every other character becomes ``.XX``
    for each byte of its UTF-8 encoding."""
    out: list[str] = []
    for ch in text:
        if ch.isascii():
            if ch.isalnum():
                out.append(ch)
                continue
        elif unicodedata.category(ch) in _LETTER_OR_DIGIT:
            out.append(ch)
            continue
        out.extend(f".{b:02X}" for b in ch.encode("utf-8", errors="replace"))
    return "".join(out)


def manufacturer_id(number: int) -> str:
    return f"M-{number:04X}"


def application_id(
    manufacturer: str,
    number: int,
    version: int,
    original_manufacturer: str | None = None,
) -> str:
    """The id of an application program, with the unsigned fingerprint ``0000``."""
    ident = f"{manufacturer}_A-{number:04X}-{version:02X}-0000"
    if original_manufacturer:
        ident += f"-O{original_manufacturer[2:]}"
    return ident


def hardware_id(
    manufacturer: str,
    serial_number: str,
    version: int,
    original_manufacturer: str | None = None,
) -> str:
    ident = f"{manufacturer}_H-{fragment(serial_number)}-{version}"
    if original_manufacturer:
        ident += f"-O{original_manufacturer[2:]}"
    return ident


def hardware2program_id(hardware: str, application_refs: list[str]) -> str:
    """``<hardware>_HP`` followed by each referenced application id without ``<manu>_A``."""
    parts = [hardware, "_HP"]
    for ref in application_refs:
        parts.append(ref[ref.rfind("_") + 2 :])
    return "".join(parts)

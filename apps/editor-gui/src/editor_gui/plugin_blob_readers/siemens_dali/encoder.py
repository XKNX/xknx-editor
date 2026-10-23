"""Primitive byte encoders ported from Siemens' ``ParameterGenLibrary.dll``.

Each ``encode_*`` turns a plug-in value into the exact byte sequence the ETS DCA plug-in
writes into its property-table. The six helpers below cover every leaf type used by the
Siemens N141/03 Parameters sections — ``Byte``, ``UShort``, ``Date``, ``Time``, ``Enum`` and
``BinaryInformation``. The property-table itself (``dict[int, bytes]``) is built per
section in :mod:`properties` by calling these encoders in the vendor-defined order.

Encoding table, kept here as byte-for-byte provenance:
* ``Byte``                 → ``[value]``                                   (1 byte)
* ``UShort``               → ``[value >> 8, value & 0xFF]``                (2 bytes, big-endian)
* ``Date``                 → ``[day, month, year % 100]``                  (3 bytes)
* ``Time``                 → ``[hour, minute, second]``                    (3 bytes)
* ``Enum<T, T2>``          → ``[int(value)]``                              (1 byte, token index)
* ``BinaryInformation<T>`` → ``[int(value)]``                              (1 byte, BI_FALSE=0 …)
"""

from __future__ import annotations

from datetime import date, datetime, time

from editor_gui.plugin_blob_readers.siemens_dali.schema import (
    ENUM_RUNTIME_VALUES,
    ENUM_TOKENS,
)


def encode_byte(value: int | str) -> bytes:
    """Pack an unsigned 8-bit integer; accepts a decimal string as it would read from the
    BLOB XML ``Value=`` attribute."""
    v = int(value)
    if not 0 <= v <= 0xFF:
        raise ValueError(f"byte out of range: {v}")
    return bytes([v])


def encode_ushort(value: int | str) -> bytes:
    """Big-endian 16-bit encoding (match ``implicit operator byte[](UShort)``)."""
    v = int(value)
    if not 0 <= v <= 0xFFFF:
        raise ValueError(f"ushort out of range: {v}")
    return bytes([(v >> 8) & 0xFF, v & 0xFF])


def encode_date(value: date | datetime | str) -> bytes:
    """3-byte Date: day, month, year-mod-100. XML format is ``YYYY-MM-DDTHH:MM:SS.sss``."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", ""))
    d = value.date() if isinstance(value, datetime) else value
    return bytes([d.day, d.month, d.year % 100])


def encode_time(value: time | datetime | str) -> bytes:
    """3-byte Time: hour, minute, second. XML format is ``HH:MM:SS`` or an ISO datetime."""
    if isinstance(value, str):
        if "T" in value:
            value = datetime.fromisoformat(value.replace("Z", ""))
        else:
            value = time.fromisoformat(value)
    t = value.time() if isinstance(value, datetime) else value
    return bytes([t.hour, t.minute, t.second])


def encode_enum(enum_name: str, value: str) -> bytes:
    """Enum values byte-encode as their **runtime** enum constant (``public enum X {
    TOKEN = N, ... }``), not necessarily the XML list
    position. ``ENUM_RUNTIME_VALUES`` carries the explicit mappings where they diverge;
    for every other enum, the list position in ``ENUM_TOKENS`` matches the runtime value
    (verified against the C# decompile)."""
    tokens = ENUM_TOKENS.get(enum_name)
    if tokens is None:
        raise KeyError(f"unknown enum: {enum_name}")
    override = ENUM_RUNTIME_VALUES.get(enum_name)
    if override is not None and value in override:
        return bytes([override[value]])
    if value not in tokens:
        raise ValueError(f"{value!r} not in {enum_name}")
    return bytes([tokens.index(value)])


def encode_binary_information(value: str) -> bytes:
    """BinaryInformation encodes six aliases onto the two underlying C# enum values
    (``ParameterGenLibrary.BinaryInformationValue``): ``BI_FALSE``/``BI_DISABLE``/``BI_OFF``
    map to ``0``; ``BI_TRUE``/``BI_ENABLE``/``BI_ON`` map to ``1``. Using the XML list
    position (as a generic ``Enum`` would) would emit ``02/03/04`` for the aliases, which
    is wrong — the vendor's `_value` runtime holds the aliased enum constant, not its
    textual position."""
    v = _BI_VALUES.get(value)
    if v is None:
        raise ValueError(f"{value!r} not a BinaryInformationValue")
    return bytes([v])


# C# ``public enum BinaryInformationValue { BI_FALSE=0, BI_TRUE=1, BI_DISABLE=BI_FALSE,
# BI_ENABLE=BI_TRUE, BI_OFF=BI_FALSE, BI_ON=BI_TRUE }`` — three names each for the two
# values. We encode on this table instead of the generic enum lookup.
_BI_VALUES: dict[str, int] = {
    "BI_FALSE": 0,
    "BI_TRUE": 1,
    "BI_DISABLE": 0,
    "BI_ENABLE": 1,
    "BI_OFF": 0,
    "BI_ON": 1,
}


__all__ = (
    "encode_binary_information",
    "encode_byte",
    "encode_date",
    "encode_enum",
    "encode_time",
    "encode_ushort",
)

"""How a Generic Plug-in setting sits in the parameter memory: encode a value, decode bytes."""

from __future__ import annotations

from .description import ParameterType, TypeKind

_INTEGER_KINDS = (TypeKind.NUMBER, TypeKind.TIME_S, TypeKind.TIME_MS)


def byte_size(ptype: ParameterType) -> int | None:
    """The number of whole bytes ``ptype`` takes, None for sizes that are not whole bytes."""
    if ptype.size_bits <= 0 or ptype.size_bits % 8:
        return None
    return ptype.size_bits // 8


def encode(value: str, ptype: ParameterType) -> bytes | None:
    """The memory bytes of ``value``; None when the type or value can't be encoded."""
    size = byte_size(ptype)
    if size is None:
        return None
    if ptype.kind is TypeKind.ENUM:
        enum = ptype.enum_by_name(value)
        if enum is None:
            return None
        number = enum.value
    elif ptype.kind in _INTEGER_KINDS:
        try:
            number = int(value)
        except ValueError:
            return None
    else:
        return None
    order = "little" if ptype.little_endian else "big"
    try:
        return number.to_bytes(size, order, signed=ptype.signed or number < 0)
    except OverflowError:
        return None


def decode(data: bytes, ptype: ParameterType) -> str | None:
    """The symbolic value the memory bytes stand for, None when they can't be read."""
    if ptype.kind not in (TypeKind.ENUM, *_INTEGER_KINDS):
        return None
    order = "little" if ptype.little_endian else "big"
    number = int.from_bytes(data, order, signed=ptype.signed)
    if ptype.kind is TypeKind.ENUM:
        enum = ptype.enum_by_value(number)
        return enum.name if enum is not None else str(number)
    return str(number)

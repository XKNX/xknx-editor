"""How a Generic Plug-in setting sits in the parameter memory: encode a value, decode bytes."""

from __future__ import annotations

from .description import INTEGER_KINDS, ParameterType, TypeKind, integer

_INTEGER_KINDS = INTEGER_KINDS


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
    if ptype.kind is TypeKind.TEXT:
        raw = value.encode("utf-16-le", errors="surrogatepass")[::2]
        return raw[:size].ljust(size, b"\x00")
    if ptype.kind is TypeKind.ENUM:
        enum = ptype.enum_by_name(value)
        if enum is not None:
            number = enum.value
        else:
            try:
                number = integer(value)
            except ValueError:
                return None
    elif ptype.kind in _INTEGER_KINDS:
        try:
            number = integer(value)
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
    if len(data) != byte_size(ptype):
        return None
    if ptype.kind is TypeKind.TEXT:
        return data.split(b"\x00", 1)[0].decode("latin-1")
    if ptype.kind not in (TypeKind.ENUM, *_INTEGER_KINDS):
        return None
    order = "little" if ptype.little_endian else "big"
    number = int.from_bytes(data, order, signed=ptype.signed)
    if ptype.kind is TypeKind.ENUM:
        enum = ptype.enum_by_value(number)
        return enum.name if enum is not None else str(number)
    return str(number)

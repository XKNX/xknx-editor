"""Read a Generic Plug-in configuration back from what a device holds.

A download writes the parameter memory (see :func:`.image.compile_image`): every parameter
with a memory offset, its default first, then the value of each setting the configuration
makes active, at its fixed address from the memory base on. This module reads that memory off
a device and decodes it into the configuration that compiles to the same bytes: each active
setting from its bytes, again until the settings that are active no longer change (a value
can make other settings active). Settings without memory, values set by a rule of the
description and values the product supplies are not in the memory: they stay as the
configuration evaluates them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from .blob import parse_settings
from .codec import byte_size, decode, encode
from .configuration import evaluate, write_values
from .description import Description, TypeKind
from .image import CompiledImage, compile_image

# How often decoding runs again: a value can make other settings active (or not).
_PASSES = 16


class MemoryReader(Protocol):
    """What a device read needs: its memory."""

    async def read_memory(
        self, address: int, count: int, *, user: bool = False
    ) -> bytes: ...


@dataclass(frozen=True)
class DeviceImage:
    """The parameter memory a device holds: ``data`` from the address ``base`` on."""

    base: int
    data: bytes


def memory_range(description: Description) -> tuple[int, int]:
    """The address and the length of the parameter memory a download writes."""
    base = description.memory_base
    if base is None:
        raise ValueError("Missing parameter memory base")
    end = 0
    for parameter in description.parameters.values():
        if not parameter.offset:
            continue
        size = byte_size(description.types[parameter.type_id])
        if size is None:
            raise ValueError(f"Cannot encode parameter {parameter.id}")
        end = max(end, parameter.offset - base + size)
    return base, end


async def read_device_image(
    device: MemoryReader, description: Description
) -> DeviceImage:
    """Read the parameter memory a download of ``description`` writes off a device."""
    base, length = memory_range(description)
    return DeviceImage(base, await device.read_memory(base, length))


def image_of(compiled: CompiledImage) -> DeviceImage:
    """What a device holds after a download of ``compiled``."""
    return DeviceImage(compiled.base, compiled.data[: compiled.used_length])


def decode_device(
    description: Description,
    data: bytes,
    image: DeviceImage,
    globals_: dict[str, str] | None = None,
) -> bytes:
    """The configuration (the settings XML ``data`` with the values ``image`` holds) that a
    device holding ``image`` was downloaded with. ``data`` gives what the memory does not
    hold, e.g. the product's default configuration. Raises ``ValueError`` when no
    configuration gives these bytes."""
    base, length = memory_range(description)
    if image.base != base or len(image.data) != length:
        raise ValueError("The memory read off the device is not this product's")
    settings = parse_settings(data)
    if settings is None:
        raise ValueError("Invalid Siemens configuration XML")
    values = dict(settings.values)
    for _ in range(_PASSES):
        state = evaluate(description, replace(settings, values=values), globals_)
        changed = False
        for ref_id, _value in state.memory_values:
            ref = description.refs[ref_id]
            if ref_id in state.assigned or ref.global_variable:
                continue
            parameter = description.parameters[ref.parameter_id]
            if not parameter.offset:
                continue
            ptype = description.types[parameter.type_id]
            offset = parameter.offset - base
            held = image.data[offset : offset + (byte_size(ptype) or 0)]
            if encode(state.values.get(ref_id, ""), ptype) == held:
                continue
            value = decode(held, ptype)
            if value is None:
                raise ValueError(f"Cannot decode parameter {parameter.id}")
            if ptype.kind is TypeKind.ENUM:
                enum = ptype.enumeration(value)
                if enum is None:
                    raise ValueError(f"No choice of {ref_id} has the value {value}")
                # Stored the way an edit stores it: by name or by value, as the default is.
                value = (
                    enum.name
                    if ptype.enum_by_name(parameter.default)
                    else str(enum.value)
                )
            values[ref_id] = value
            changed = True
        if not changed:
            break
    recovered = replace(settings, values=values)
    compiled = compile_image(description, recovered, globals_)
    if compiled.used_length != length or any(
        compiled.mask[i] and compiled.data[i] != image.data[i] for i in range(length)
    ):
        raise ValueError("No configuration gives the memory read off the device")
    return write_values(data, evaluate(description, recovered, globals_).values)

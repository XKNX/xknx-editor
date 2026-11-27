"""Read a Generic Plug-in configuration back from what a device holds.

A download writes the parameter memory (see :func:`.image.compile_image`): every parameter
with a memory offset, its default first, then the value of each setting the configuration
makes active, at its fixed address from the memory base on (or, for a relative download, from
the start of the application's segment on). This module reads that memory off a device and
decodes it into the configuration that compiles to the same bytes: each active setting from
its bytes, again until the settings that are active no longer change (a value can make other
settings active). Settings without memory, values set by a rule of the description, values
the product supplies and settings whose download condition is 0 are not in the memory: they
stay as the configuration evaluates them. The configuration keeps the memory read as its last downloaded
image, with the device's record of the segment, as a download keeps it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from .blob import parse_settings
from .codec import byte_size, decode, encode
from .configuration import downloaded_config, evaluate, write_values
from .description import Description, TypeKind, locked_parameters
from .image import CompiledImage, compile_image

# How often decoding runs again: a value can make other settings active (or not).
_PASSES = 16


# Where a device keeps the record of its parameter segment: object 4, property 27 (a
# download reads it at its end and stores it with the image).
_SEGMENT_OBJECT = 4
_SEGMENT_PROPERTY = 27
_SEGMENT_RECORD_SIZE = 8
# Where the application's segment starts (its table reference): a relative download writes
# the parameter memory from there on.
_SEGMENT_POINTER = 7


class MemoryReader(Protocol):
    """What a device read needs: its memory and the record of its parameter segment."""

    async def read_memory(
        self, address: int, count: int, *, user: bool = False
    ) -> bytes: ...

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
        require_nonzero: bool = False,
    ) -> bytes: ...


@dataclass(frozen=True)
class DeviceImage:
    """The parameter memory a device holds: ``data`` from the address ``base`` on, and the
    device's record of the segment (``segment_info``, empty when it has none)."""

    base: int
    data: bytes
    segment_info: bytes = b""


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


@dataclass(frozen=True)
class MemoryLocation:
    """Where a device holds the parameter memory: ``length`` octets from ``address`` on,
    relative to the start of the application's segment (object 4) when ``relative``."""

    address: int
    length: int
    relative: bool = False


def memory_location(description: Description) -> MemoryLocation:
    """Where a device holds the parameter memory a download of ``description`` writes."""
    base, length = memory_range(description)
    offset = description.download_offset
    if offset is None:
        return MemoryLocation(base, length)
    return MemoryLocation(base - offset, length, relative=True)


def stored_image(description: Description, data: bytes) -> bytes:
    """The parameter memory ``data`` read off a device (from the memory base on) as the
    plug-in stores an image: from its origin on (see ``Description.image_origin``)."""
    base, origin = description.memory_base, description.image_origin
    if base is None or origin is None:
        raise ValueError("Missing parameter memory base")
    return bytes(base - origin) + data


async def read_device_image(
    device: MemoryReader, description: Description
) -> DeviceImage:
    """Read the parameter memory a download of ``description`` writes off a device, and
    the device's record of that segment."""
    base, _length = memory_range(description)
    location = memory_location(description)
    address = location.address
    if location.relative:
        pointer = await device.read_property(_SEGMENT_OBJECT, _SEGMENT_POINTER)
        if len(pointer) != 4:
            raise ValueError("The device reports no application segment")
        address += int.from_bytes(pointer, "big")
    data = await device.read_memory(address, location.length)
    segment_info = await device.read_property(_SEGMENT_OBJECT, _SEGMENT_PROPERTY)
    if len(segment_info) != _SEGMENT_RECORD_SIZE:
        segment_info = b""
    return DeviceImage(base, data, segment_info)


def image_of(compiled: CompiledImage) -> DeviceImage:
    """What a device holds after a download of ``compiled``."""
    first = compiled.start - compiled.base
    return DeviceImage(compiled.start, compiled.data[first : compiled.used_length])


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
        # A setting whose download condition is 0 is not in the memory: it keeps its value.
        locked = locked_parameters(
            description, state.values, [r for r, _ in state.memory_values]
        )
        changed = False
        for ref_id, _value in state.memory_values:
            ref = description.refs[ref_id]
            if ref_id in state.assigned or ref.global_variable:
                continue
            parameter = description.parameters[ref.parameter_id]
            if not parameter.offset or parameter.id in locked:
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
    shift = base - compiled.base
    if compiled.used_length > shift + length or any(
        compiled.mask[shift + i] and compiled.data[shift + i] != image.data[i]
        for i in range(length)
    ):
        raise ValueError("No configuration gives the memory read off the device")
    result = write_values(data, evaluate(description, recovered, globals_).values)
    if not image.segment_info:
        return result
    # The device holds what a download of this configuration stores as its last
    # downloaded image: kept as that (when it was downloaded is not known).
    return downloaded_config(
        result, compiled.data, compiled.mask, image.segment_info, downloaded=False
    )

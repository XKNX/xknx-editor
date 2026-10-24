"""The image a device leaves the factory with: ``Hardware2Program/@LoadedImage`` of the product.

Some products carry the memory and property contents their devices are loaded with at the factory.
A property the load procedure writes starts from these contents, and the parameters are laid over
them; a property the factory image does not hold starts from zeros.

The attribute holds a raw DEFLATE stream of a little-endian image:

- ``UInt32`` format version (``0x00010001``) and ``Int32`` number of segments,
- per segment ``UInt32`` location kind, ``UInt16`` object reference, ``UInt16`` property id,
  ``UInt32`` start address, ``UInt32`` size, a flags byte (bit 0: data follows, bit 1: mask
  follows), then ``size`` data bytes and ``size`` mask bytes as flagged.

A property segment's start address is the byte offset of element 1; an object addressed by type
keeps its occurrence in the address's top byte. Application properties count their objects from
the mask's first application object (``FirstAppObjectIdx``).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import Enum

_VERSION = 0x00010001
_SEGMENT_HEADER = struct.Struct("<IHHII")


class FactoryLocation(Enum):
    MEMORY = "memory"
    USER_MEMORY = "user_memory"
    PROPERTY = "property"
    APP_PROPERTY = "app_property"
    LC_SLAVE_MEMORY = "lc_slave_memory"
    LC_FILTER_MEMORY = "lc_filter_memory"
    PROPERTY_BY_TYPE = "property_by_type"
    RELATIVE_MEMORY = "relative_memory"
    RELATIVE_MEMORY_BY_TYPE = "relative_memory_by_type"


_KINDS = {
    1: FactoryLocation.MEMORY,
    2: FactoryLocation.MEMORY,
    3: FactoryLocation.USER_MEMORY,
    4: FactoryLocation.PROPERTY,
    5: FactoryLocation.APP_PROPERTY,
    6: FactoryLocation.LC_SLAVE_MEMORY,
    7: FactoryLocation.LC_FILTER_MEMORY,
    11: FactoryLocation.PROPERTY_BY_TYPE,
    12: FactoryLocation.RELATIVE_MEMORY,
    13: FactoryLocation.RELATIVE_MEMORY_BY_TYPE,
}


class FactoryImageError(ValueError):
    """The factory image is not in the expected format."""


@dataclass(frozen=True, slots=True)
class FactorySegment:
    location: FactoryLocation
    # The object index (or the application object index, or the object type) of a property or
    # relative segment.
    object_ref: int
    property_id: int
    address: int
    size: int
    data: bytes | None
    mask: bytes | None

    @property
    def occurrence(self) -> int:
        """The occurrence of an object addressed by type."""
        return self.address >> 24

    @property
    def offset(self) -> int:
        """The start address without an occurrence."""
        if self.location in (
            FactoryLocation.PROPERTY_BY_TYPE,
            FactoryLocation.RELATIVE_MEMORY_BY_TYPE,
        ):
            return self.address & 0xFFFFFF
        return self.address


@dataclass(frozen=True, slots=True)
class FactoryImage:
    segments: tuple[FactorySegment, ...]

    def property_data(
        self,
        object_index: int | None,
        object_type: int | None,
        occurrence: int,
        property_id: int,
        first_app_object: int | None,
    ) -> bytes | None:
        """The factory contents of a property from element 1 on, or ``None`` when the image does
        not hold it. An object at or above ``first_app_object`` is held as an application
        property."""
        for segment in self.segments:
            if segment.property_id != property_id or segment.data is None:
                continue
            if segment.location is FactoryLocation.PROPERTY_BY_TYPE:
                hit = (
                    object_type == segment.object_ref
                    and occurrence == segment.occurrence
                )
            elif object_index is None:
                hit = False
            elif segment.location is FactoryLocation.APP_PROPERTY:
                hit = (
                    first_app_object is not None
                    and object_index >= first_app_object
                    and object_index - first_app_object == segment.object_ref
                )
            elif segment.location is FactoryLocation.PROPERTY:
                hit = object_index == segment.object_ref and (
                    first_app_object is None or object_index < first_app_object
                )
            else:
                hit = False
            if hit:
                return bytes(segment.offset) + segment.data
        return None


def parse_factory_image(raw: bytes | None) -> FactoryImage | None:
    """The factory image of a ``LoadedImage`` attribute (its base64 decoded), or ``None`` for an
    empty one."""
    if not raw:
        return None
    try:
        data = zlib.decompress(raw, -15)
    except zlib.error as exc:
        raise FactoryImageError(f"factory image does not decompress: {exc}") from exc
    if len(data) < 8:
        raise FactoryImageError("factory image too short")
    version, count = struct.unpack_from("<Ii", data, 0)
    if version != _VERSION:
        raise FactoryImageError(f"unknown factory image version {version:#010x}")
    pos = 8
    segments: list[FactorySegment] = []
    for _ in range(count):
        if pos + _SEGMENT_HEADER.size + 1 > len(data):
            raise FactoryImageError("factory image segment header truncated")
        kind, ref, pid, address, size = _SEGMENT_HEADER.unpack_from(data, pos)
        pos += _SEGMENT_HEADER.size
        flags = data[pos]
        pos += 1
        content = mask = None
        if flags & 1:
            content = data[pos : pos + size]
            pos += size
        if flags & 2:
            mask = data[pos : pos + size]
            pos += size
        if pos > len(data):
            raise FactoryImageError("factory image segment data truncated")
        location = _KINDS.get(kind)
        if location is not None:
            segments.append(
                FactorySegment(location, ref, pid, address, size, content, mask)
            )
    if pos != len(data):
        raise FactoryImageError("factory image has trailing data")
    return FactoryImage(tuple(segments))

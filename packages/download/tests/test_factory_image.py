"""The factory image of a product (``Hardware2Program/@LoadedImage``)."""

from __future__ import annotations

import struct
import zlib

import pytest

from xknxeditor.download.factory_image import (
    FactoryImageError,
    FactoryLocation,
    parse_factory_image,
)


def _segment(
    kind: int,
    ref: int,
    pid: int,
    address: int,
    data: bytes | None,
    mask: bytes | None = None,
    size: int | None = None,
) -> bytes:
    size = len(data if data is not None else mask or b"") if size is None else size
    flags = (1 if data is not None else 0) | (2 if mask is not None else 0)
    return (
        struct.pack("<IHHIIB", kind, ref, pid, address, size, flags)
        + (data or b"")
        + (mask or b"")
    )


def _image(
    *segments: bytes, version: int = 0x00010001, count: int | None = None
) -> bytes:
    raw = struct.pack("<Ii", version, len(segments) if count is None else count)
    raw += b"".join(segments)
    deflate = zlib.compressobj(wbits=-15)
    return deflate.compress(raw) + deflate.flush()


def test_an_empty_image_is_none() -> None:
    assert parse_factory_image(None) is None
    assert parse_factory_image(b"") is None


def test_segments_are_read_with_data_and_mask() -> None:
    image = parse_factory_image(
        _image(
            _segment(1, 0, 0, 0x4000, b"\x01\x02"),
            _segment(4, 7, 53, 0, b"\x02\x00", b"\xff\x0f"),
            _segment(11, 9, 54, (2 << 24) | 3, b"\xaa"),
            _segment(99, 0, 0, 0, b"\x00"),
        )
    )
    assert image is not None
    # An unknown location is skipped.
    assert [s.location for s in image.segments] == [
        FactoryLocation.MEMORY,
        FactoryLocation.PROPERTY,
        FactoryLocation.PROPERTY_BY_TYPE,
    ]
    memory, prop, by_type = image.segments
    assert (memory.address, memory.offset, memory.data) == (0x4000, 0x4000, b"\x01\x02")
    assert (prop.data, prop.mask) == (b"\x02\x00", b"\xff\x0f")
    assert (by_type.occurrence, by_type.offset) == (2, 3)


def test_a_segment_without_data_holds_none() -> None:
    image = parse_factory_image(_image(_segment(4, 1, 5, 0, None, size=3)))
    assert image is not None
    assert image.segments[0].data is None
    assert image.property_data(1, None, 0, 5, None) is None


def test_property_data_is_found_by_index_type_and_application_object() -> None:
    image = parse_factory_image(
        _image(
            _segment(4, 1, 54, 0, b"\x10"),
            _segment(5, 3, 54, 2, b"\x20\x21"),
            _segment(11, 9, 53, (1 << 24) | 1, b"\x30"),
        )
    )
    assert image is not None
    # A system object by its index.
    assert image.property_data(1, None, 0, 54, 5) == b"\x10"
    # The start address is the byte offset of element 1.
    assert image.property_data(8, None, 0, 54, 5) == b"\x00\x00\x20\x21"
    assert image.property_data(8, None, 0, 54, None) is None
    # An index at or above the first application object is not a system object.
    assert image.property_data(1, None, 0, 54, 1) is None
    # An object addressed by type, with its occurrence in the address's top byte.
    assert image.property_data(None, 9, 1, 53, None) == b"\x00\x30"
    assert image.property_data(None, 9, 0, 53, None) is None
    assert image.property_data(None, None, 0, 54, 5) is None
    assert image.property_data(1, None, 0, 99, 5) is None


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not deflate at all \xff\xff", "does not decompress"),
        (_image(version=2), "unknown factory image version"),
        (_image(count=1), "segment header truncated"),
        (_image(_segment(4, 1, 5, 0, b"\x01", size=4)), "segment data truncated"),
    ],
)
def test_malformed_images_are_rejected(raw: bytes, message: str) -> None:
    with pytest.raises(FactoryImageError, match=message):
        parse_factory_image(raw)


def test_trailing_data_is_rejected() -> None:
    raw = struct.pack("<Ii", 0x00010001, 0) + b"\x00"
    deflate = zlib.compressobj(wbits=-15)
    with pytest.raises(FactoryImageError, match="trailing data"):
        parse_factory_image(deflate.compress(raw) + deflate.flush())


def test_a_too_short_image_is_rejected() -> None:
    deflate = zlib.compressobj(wbits=-15)
    with pytest.raises(FactoryImageError, match="too short"):
        parse_factory_image(deflate.compress(b"\x01\x00") + deflate.flush())

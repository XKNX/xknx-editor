from __future__ import annotations

import struct
import zlib

from editor_gui.widgets.icons import dark_png, white_png


def _png(
    pixels: list[tuple[int, int, int, int]], width: int, filter_type: int = 0
) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    height = len(pixels) // width
    rows = b""
    for y in range(height):
        line = b"".join(bytes(p) for p in pixels[y * width : (y + 1) * width])
        if filter_type == 1:
            line = bytes(
                (line[i] - (line[i - 4] if i >= 4 else 0)) & 0xFF
                for i in range(len(line))
            )
        rows += bytes([filter_type]) + line
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _pixels(data: bytes) -> list[tuple[int, ...]]:
    width = struct.unpack(">I", data[16:20])[0]
    pos, idat = 8, b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        if data[pos + 4 : pos + 8] == b"IDAT":
            idat += data[pos + 8 : pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4 + 1
    out: list[tuple[int, ...]] = []
    for y in range(len(raw) // stride):
        line = raw[y * stride + 1 : (y + 1) * stride]
        out += [tuple(line[i : i + 4]) for i in range(0, len(line), 4)]
    return out


def test_single_colour_icon_turns_white_keeping_alpha() -> None:
    src = _png([(0, 0, 0, 255), (0, 0, 0, 0), (0, 0, 0, 128), (0, 0, 0, 0)], 2, 1)
    result = white_png(src)
    assert result is not None
    assert _pixels(result) == [
        (255, 255, 255, 255),
        (255, 255, 255, 0),
        (255, 255, 255, 128),
        (255, 255, 255, 0),
    ]


def test_coloured_icon_is_kept() -> None:
    assert white_png(_png([(255, 0, 0, 255), (0, 0, 255, 255)], 2)) is None


def test_not_a_png() -> None:
    assert white_png(b"<svg/>") is None


def test_picture_greys_are_inverted_and_colours_kept() -> None:
    src = _png(
        [(0, 0, 0, 255), (64, 160, 64, 255), (250, 250, 245, 128), (0, 0, 0, 0)], 2, 1
    )
    result = dark_png(src)
    assert result is not None
    assert _pixels(result) == [
        (255, 255, 255, 255),
        (64, 160, 64, 255),
        (5, 5, 10, 128),
        (255, 255, 255, 0),
    ]


def test_opaque_picture_is_kept() -> None:
    assert dark_png(_png([(0, 0, 0, 255), (255, 255, 255, 255)], 2)) is None

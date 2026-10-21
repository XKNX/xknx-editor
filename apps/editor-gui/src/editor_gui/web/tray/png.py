"""Read the pixels of an 8-bit RGB or RGBA PNG (not interlaced), as the Linux tray needs them."""

from __future__ import annotations

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def rgba_pixels(png: bytes) -> tuple[int, int, bytes]:
    """``(width, height, pixels)`` with 4 bytes per pixel in R, G, B, A order, rows top down."""
    if not png.startswith(_SIGNATURE):
        raise ValueError("not a PNG")
    pos = len(_SIGNATURE)
    width = height = channels = 0
    data = bytearray()
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind = png[pos + 4 : pos + 8]
        chunk = png[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _c, _f, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            if depth != 8 or color not in (2, 6) or interlace:
                raise ValueError("only 8-bit RGB or RGBA PNGs, not interlaced")
            channels = 4 if color == 6 else 3
        elif kind == b"IDAT":
            data += chunk
        elif kind == b"IEND":
            break
    raw = zlib.decompress(bytes(data))
    stride = width * channels
    previous = bytearray(stride)
    out = bytearray()
    for y in range(height):
        start = y * (stride + 1)
        method = raw[start]
        row = bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0
            if method == 1:
                row[i] = (row[i] + left) & 0xFF
            elif method == 2:
                row[i] = (row[i] + up) & 0xFF
            elif method == 3:
                row[i] = (row[i] + (left + up) // 2) & 0xFF
            elif method == 4:
                row[i] = (row[i] + _paeth(left, up, up_left)) & 0xFF
        previous = row
        if channels == 4:
            out += row
        else:
            for i in range(0, stride, 3):
                out += row[i : i + 3] + b"\xff"
    return width, height, bytes(out)


def argb_pixels(png: bytes) -> tuple[int, int, bytes]:
    """``(width, height, pixels)`` with 4 bytes per pixel in A, R, G, B order (big-endian ARGB32,
    as a StatusNotifierItem's icon pixmap)."""
    width, height, rgba = rgba_pixels(png)
    argb = bytearray(len(rgba))
    argb[0::4] = rgba[3::4]
    argb[1::4] = rgba[0::4]
    argb[2::4] = rgba[1::4]
    argb[3::4] = rgba[2::4]
    return width, height, bytes(argb)

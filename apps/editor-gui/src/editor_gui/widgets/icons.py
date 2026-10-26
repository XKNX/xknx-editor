"""Icons and pictures an application ships as images.

Single-coloured icons are drawn in the text colour (not a fully opaque one: that is a colour,
e.g. of an enumeration entry). The grey parts of other images on a
transparent background are inverted when they would hardly stand out from the theme's
background: dark greys on a dark theme, light greys on a light one.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from imgui_bundle import hello_imgui, imgui

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True, slots=True)
class Icon:
    texture: imgui.ImTextureRef
    # Single-coloured icons are stored white and drawn tinted with the text colour.
    tinted: bool


@dataclass(frozen=True, slots=True)
class Picture:
    texture: imgui.ImTextureRef
    width: float
    height: float


# Keyed by the image key and whether the background is dark.
_cache: dict[tuple[str, bool], Icon | None] = {}
_pictures: dict[tuple[str, bool], Picture | None] = {}
# Largest channel difference of a pixel that still counts as grey.
_GREY_SPREAD = 16


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    chunks: list[tuple[bytes, bytes]] = []
    pos = len(_PNG_SIGNATURE)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        chunks.append((kind, data[pos + 8 : pos + 8 + length]))
        pos += 12 + length
    return chunks


def _unfilter(raw: bytes, width: int, height: int) -> bytearray:
    """Reverse the PNG scanline filters of 8 bit RGBA image data."""
    stride = width * 4
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        for x in range(stride):
            left = line[x - 4] if x >= 4 else 0
            up = prev[x]
            if kind == 1:
                line[x] = (line[x] + left) & 0xFF
            elif kind == 2:
                line[x] = (line[x] + up) & 0xFF
            elif kind == 3:
                line[x] = (line[x] + (left + up) // 2) & 0xFF
            elif kind == 4:
                up_left = prev[x - 4] if x >= 4 else 0
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                pred = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
                line[x] = (line[x] + pred) & 0xFF
        out += line
        prev = line
    return out


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _decode_rgba(data: bytes) -> tuple[bytes, int, int, bytearray] | None:
    """Header, size and pixels of an 8 bit RGBA PNG without interlacing."""
    if not data.startswith(_PNG_SIGNATURE):
        return None
    try:
        chunks = _chunks(data)
        header = next(payload for kind, payload in chunks if kind == b"IHDR")
        width, height, depth, colour, _, _, interlace = struct.unpack(
            ">IIBBBBB", header
        )
        if depth != 8 or colour != 6 or interlace != 0:
            return None
        raw = zlib.decompress(b"".join(p for kind, p in chunks if kind == b"IDAT"))
        return header, width, height, _unfilter(raw, width, height)
    except (StopIteration, struct.error, zlib.error, IndexError):
        return None


def _encode_rgba(header: bytes, width: int, height: int, pixels: bytearray) -> bytes:
    stride = width * 4
    rows = b"".join(
        b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height)
    )
    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def white_png(data: bytes) -> bytes | None:
    """``data`` re-encoded in white with its alpha, if it is a single-coloured RGBA PNG with
    transparent parts (a glyph); a fully opaque one is a colour and keeps it."""
    decoded = _decode_rgba(data)
    if decoded is None:
        return None
    header, width, height, pixels = decoded
    if all(pixels[i] == 255 for i in range(3, len(pixels), 4)):
        return None
    colours = {
        bytes(pixels[i : i + 3]) for i in range(0, len(pixels), 4) if pixels[i + 3]
    }
    if len(colours) > 1:
        return None
    for i in range(0, len(pixels), 4):
        pixels[i : i + 3] = b"\xff\xff\xff"
    return _encode_rgba(header, width, height, pixels)


def _is_grey(r: int, g: int, b: int) -> bool:
    return max(r, g, b) - min(r, g, b) <= _GREY_SPREAD


def readable_png(data: bytes, dark_background: bool) -> bytes | None:
    """``data`` with its grey pixels inverted when, on average, they are as dark (or as light)
    as the background, if it is an RGBA PNG with transparent parts; ``None`` when it is kept
    (an image without transparent parts brings its own background)."""
    decoded = _decode_rgba(data)
    if decoded is None:
        return None
    header, width, height, pixels = decoded
    if not any(pixels[i] == 0 for i in range(3, len(pixels), 4)):
        return None
    # The brightness of the visible greys, weighted by their opacity.
    weight = brightness = 0
    for i in range(0, len(pixels), 4):
        r, g, b, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
        if a and _is_grey(r, g, b):
            weight += a
            brightness += (r + g + b) * a
    if not weight or (brightness / (3 * weight) < 128) != dark_background:
        return None
    for i in range(0, len(pixels), 4):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        if _is_grey(r, g, b):
            pixels[i : i + 3] = bytes((255 - r, 255 - g, 255 - b))
    return _encode_rgba(header, width, height, pixels)


def dark_background() -> bool:
    """Whether the theme's window background is dark."""
    c = imgui.get_style().color_(imgui.Col_.window_bg)
    return 0.299 * c.x + 0.587 * c.y + 0.114 * c.z < 0.5


def has_icon(key: str) -> bool:
    """Whether the icon for ``key`` was loaded (or found missing) before for this theme."""
    return (key, dark_background()) in _cache


def load_icon(key: str, data: bytes | None) -> Icon | None:
    """The texture for an icon image, created once per ``key`` and theme brightness."""
    dark = dark_background()
    if (key, dark) in _cache:
        return _cache[(key, dark)]
    icon: Icon | None = None
    if data:
        white = white_png(data)
        shown = white or readable_png(data, dark) or data
        texture = _texture(shown, f"icon:{key}:{_variant(dark)}")
        if texture is not None:
            icon = Icon(texture[0], white is not None)
    _cache[(key, dark)] = icon
    return icon


def has_picture(key: str) -> bool:
    """Whether the picture for ``key`` was loaded (or found missing) before for this theme."""
    return (key, dark_background()) in _pictures


def load_picture(key: str, data: bytes | None) -> Picture | None:
    """The texture for a picture, created once per ``key`` and theme brightness."""
    dark = dark_background()
    if (key, dark) in _pictures:
        return _pictures[(key, dark)]
    picture: Picture | None = None
    if data:
        texture = _texture(
            readable_png(data, dark) or data, f"picture:{key}:{_variant(dark)}"
        )
        if texture is not None:
            picture = Picture(*texture)
    _pictures[(key, dark)] = picture
    return picture


def _texture(data: bytes, key: str) -> tuple[imgui.ImTextureRef, float, float] | None:
    """A texture for an encoded image and its size, None when it cannot be decoded. In the web
    mode the browser decodes and holds it."""
    from editor_gui import web

    if web.active_hub is not None:
        return web.encoded_texture(data)
    try:
        image = hello_imgui.image_and_size_from_encoded_data(data, key)
    except RuntimeError:
        return None
    if image.size.x <= 0:
        return None
    return imgui.ImTextureRef(image.texture_id), image.size.x, image.size.y


def _variant(dark: bool) -> str:
    return "dark" if dark else "light"


def draw_icon(icon: Icon, pos: imgui.ImVec2, size: float) -> None:
    """Draw ``icon`` as a ``size`` square at screen position ``pos``."""
    colour = imgui.get_color_u32(imgui.Col_.text) if icon.tinted else 0xFFFFFFFF
    imgui.get_window_draw_list().add_image(
        icon.texture,
        pos,
        imgui.ImVec2(pos.x + size, pos.y + size),
        imgui.ImVec2(0, 0),
        imgui.ImVec2(1, 1),
        colour,
    )

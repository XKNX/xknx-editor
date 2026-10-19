"""Encode Dear ImGui's draw data and texture updates for the browser client.

Every message is one binary WebSocket message: a kind byte, then a zlib-compressed body
(little-endian throughout).

- **Frame** (``KIND_FRAME``): ``u8`` mouse cursor + 1 (0 = hidden), ``u8`` index size (2 or 4),
  ``u16`` 0, ``f32`` display x, y, width, height, ``u32`` draw-list count; per draw list ``u8``
  ``LIST_FULL`` or ``LIST_KEPT`` and its 8-byte content hash. A kept list is the one with that
  hash from the browser's previous frame (an unchanged window), so only changed lists travel. A
  full list follows with ``u32`` vertex, index and command counts, the vertices (``ImDrawVert``:
  ``f32`` pos[2], ``f32`` uv[2], ``u32`` colour), the indices padded to 4 bytes, and per command
  ``f32`` clip rectangle[4], ``u32`` texture, vertex offset, index offset and element count.
- **Texture** (``KIND_TEXTURE``): ``u32`` id, ``u8`` operation, 3 padding bytes, ``u32`` texture
  width, height, then the rectangle x, y, width, height and its pixels. ``TEX_CREATE`` and
  ``TEX_UPDATE`` carry RGBA pixels of the rectangle, ``TEX_DESTROY`` none, ``TEX_ENCODED`` an
  encoded image (PNG) the browser decodes itself.
- **Clipboard** (``KIND_CLIPBOARD``): the UTF-8 text the app copied.
- **Event** (``KIND_EVENT``): a UTF-8 JSON object for the page itself (file dialogs, downloads,
  links, control of the app).
"""

from __future__ import annotations

import ctypes
import hashlib
import itertools
import struct
import threading
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from imgui_bundle import imgui

if TYPE_CHECKING:
    from collections.abc import Iterable

KIND_FRAME = 1
KIND_TEXTURE = 2
KIND_CLIPBOARD = 3
KIND_EVENT = 4

LIST_FULL = 0
LIST_KEPT = 1

TEX_CREATE = 1
TEX_UPDATE = 2
TEX_DESTROY = 3
TEX_ENCODED = 4

_VERTEX_SIZE = 20  # sizeof(ImDrawVert)
_FRAME_HEADER = struct.Struct("<BBHffffI")
_LIST_HEADER = struct.Struct("<III")
_LIST_REF = struct.Struct("<B8s")
_COMMAND = struct.Struct("<ffffIIII")
_TEXTURE_HEADER = struct.Struct("<IB3xIIIIII")
# Texture ids the browser knows; ImGui's own textures and encoded images share one sequence.
_texture_ids = itertools.count(1)


def message(kind: int, body: bytes) -> bytes:
    """One wire message: the kind byte and the compressed body."""
    return bytes((kind,)) + zlib.compress(body, 1)


def _index_size(draw_list: imgui.ImDrawList) -> int:
    """Bytes per ``ImDrawIdx`` (2 unless the build switched to 32-bit indices)."""
    buffer = draw_list.idx_buffer
    count = buffer.size()
    if count < 2:
        return 2
    raw = ctypes.string_at(buffer.data_address(), 4)
    first, second = struct.unpack("<HH", raw)
    return 2 if (first, second) == (buffer[0], buffer[1]) else 4


@dataclass(frozen=True)
class Frame:
    """One encoded frame: its header and every draw list as ``(hash, bytes)``."""

    header: bytes
    lists: tuple[tuple[bytes, bytes], ...]

    def same_as(self, other: Frame | None) -> bool:
        """Whether ``other`` draws exactly the same."""
        return (
            other is not None
            and other.header == self.header
            and [h for h, _ in other.lists] == [h for h, _ in self.lists]
        )

    def body(self, known: frozenset[bytes] = frozenset()) -> bytes:
        """The uncompressed message body; lists whose hash is in ``known`` are referenced only."""
        parts = [self.header]
        for digest, data in self.lists:
            if digest in known:
                parts.append(_LIST_REF.pack(LIST_KEPT, digest))
            else:
                parts.append(_LIST_REF.pack(LIST_FULL, digest))
                parts.append(data)
        return b"".join(parts)

    @property
    def hashes(self) -> frozenset[bytes]:
        return frozenset(h for h, _ in self.lists)


def encode_frame(draw_data: imgui.ImDrawData, cursor: int) -> Frame:
    """The frame of ``draw_data`` (see the module docstring)."""
    lists = [draw_data.cmd_lists[i] for i in range(draw_data.cmd_lists.size())]
    index_size = next((_index_size(dl) for dl in lists if dl.idx_buffer.size() >= 2), 2)
    header = _FRAME_HEADER.pack(
        cursor + 1,
        index_size,
        0,
        draw_data.display_pos.x,
        draw_data.display_pos.y,
        draw_data.display_size.x,
        draw_data.display_size.y,
        len(lists),
    )
    encoded: list[tuple[bytes, bytes]] = []
    for draw_list in lists:
        data = _encode_list(draw_list, index_size)
        encoded.append((hashlib.blake2b(data, digest_size=8).digest(), data))
    return Frame(header, tuple(encoded))


def _encode_list(draw_list: imgui.ImDrawList, index_size: int) -> bytes:
    vertices = draw_list.vtx_buffer
    indices = draw_list.idx_buffer
    commands = draw_list.cmd_buffer
    parts = [
        _LIST_HEADER.pack(vertices.size(), indices.size(), commands.size()),
        ctypes.string_at(vertices.data_address(), vertices.size() * _VERTEX_SIZE),
    ]
    index_bytes = indices.size() * index_size
    parts.append(ctypes.string_at(indices.data_address(), index_bytes))
    if index_bytes % 4:
        parts.append(b"\x00" * (4 - index_bytes % 4))
    for i in range(commands.size()):
        command = commands[i]
        clip = command.clip_rect
        parts.append(
            _COMMAND.pack(
                clip.x,
                clip.y,
                clip.z,
                clip.w,
                int(command.get_tex_id()) & 0xFFFFFFFF,
                command.vtx_offset,
                command.idx_offset,
                command.elem_count,
            )
        )
    return b"".join(parts)


def texture_body(
    texture_id: int,
    operation: int,
    size: tuple[int, int],
    rect: tuple[int, int, int, int],
    pixels: bytes = b"",
) -> bytes:
    """The uncompressed body of a texture message."""
    return _TEXTURE_HEADER.pack(texture_id, operation, *size, *rect) + pixels


@dataclass
class _Mirror:
    """The browser's copy of one texture: its size and RGBA pixels (or the encoded image)."""

    width: int
    height: int
    pixels: bytearray
    encoded: bytes = b""


class TextureMirror:
    """Turns ImGui's texture requests into texture messages and keeps every texture's pixels, so a
    browser that connects later gets them all.

    :meth:`process` runs on the GUI thread each frame and marks the requests done, as a renderer
    backend does. :meth:`snapshot` runs on the server thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._textures: dict[int, _Mirror] = {}

    def process(self, textures: Iterable[imgui.ImTextureData]) -> list[bytes]:
        """Messages for every texture ImGui wants created, updated or destroyed."""
        out: list[bytes] = []
        for texture in textures:
            status = texture.status
            if status == imgui.ImTextureStatus.want_create:
                texture_id = next(_texture_ids)
                pixels = _rgba(texture, 0, 0, texture.width, texture.height)
                with self._lock:
                    self._textures[texture_id] = _Mirror(
                        texture.width, texture.height, bytearray(pixels)
                    )
                size = (texture.width, texture.height)
                out.append(
                    message(
                        KIND_TEXTURE,
                        texture_body(
                            texture_id, TEX_CREATE, size, (0, 0, *size), pixels
                        ),
                    )
                )
                texture.set_tex_id(texture_id)
                texture.set_status(imgui.ImTextureStatus.ok)
            elif status == imgui.ImTextureStatus.want_updates:
                texture_id = int(texture.get_tex_id())
                rect = texture.update_rect
                x, y, w, h = rect.x, rect.y, rect.w, rect.h
                pixels = _rgba(texture, x, y, w, h)
                with self._lock:
                    mirror = self._textures.get(texture_id)
                    if mirror is not None:
                        _blit(mirror, x, y, w, h, pixels)
                out.append(
                    message(
                        KIND_TEXTURE,
                        texture_body(
                            texture_id,
                            TEX_UPDATE,
                            (texture.width, texture.height),
                            (x, y, w, h),
                            pixels,
                        ),
                    )
                )
                texture.set_status(imgui.ImTextureStatus.ok)
            elif status == imgui.ImTextureStatus.want_destroy:
                texture_id = int(texture.get_tex_id())
                with self._lock:
                    self._textures.pop(texture_id, None)
                out.append(
                    message(
                        KIND_TEXTURE,
                        texture_body(texture_id, TEX_DESTROY, (0, 0), (0, 0, 0, 0)),
                    )
                )
                texture.set_tex_id(0)
                texture.set_status(imgui.ImTextureStatus.destroyed)
        return out

    def add_encoded(self, data: bytes, width: int, height: int) -> tuple[int, bytes]:
        """Register an encoded image (PNG): its texture id and the message for the browser."""
        texture_id = next(_texture_ids)
        with self._lock:
            self._textures[texture_id] = _Mirror(width, height, bytearray(), data)
        size = (width, height)
        return texture_id, message(
            KIND_TEXTURE,
            texture_body(texture_id, TEX_ENCODED, size, (0, 0, *size), data),
        )

    def snapshot(self) -> list[bytes]:
        """A create message for every texture the browser needs."""
        with self._lock:
            items = list(self._textures.items())
        out: list[bytes] = []
        for texture_id, mirror in items:
            size = (mirror.width, mirror.height)
            if mirror.encoded:
                body = texture_body(
                    texture_id, TEX_ENCODED, size, (0, 0, *size), mirror.encoded
                )
            else:
                body = texture_body(
                    texture_id, TEX_CREATE, size, (0, 0, *size), bytes(mirror.pixels)
                )
            out.append(message(KIND_TEXTURE, body))
        return out


def _rgba(texture: imgui.ImTextureData, x: int, y: int, w: int, h: int) -> bytes:
    """The RGBA pixels of a rectangle of ``texture`` (an Alpha8 texture becomes white + alpha)."""
    pixels = texture.get_pixels_array()
    bpp = texture.bytes_per_pixel
    rows = pixels.reshape(texture.height, texture.width * bpp)
    block = rows[y : y + h, x * bpp : (x + w) * bpp]
    if bpp == 4:
        return block.tobytes()
    alpha = block.tobytes()
    out = bytearray(b"\xff" * (len(alpha) * 4))
    out[3::4] = alpha
    return bytes(out)


def _blit(mirror: _Mirror, x: int, y: int, w: int, h: int, pixels: bytes) -> None:
    """Copy an updated rectangle into the mirrored texture."""
    stride = mirror.width * 4
    row = w * 4
    for line in range(h):
        start = (y + line) * stride + x * 4
        mirror.pixels[start : start + row] = pixels[line * row : (line + 1) * row]


def png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height of a PNG from its IHDR chunk, None for anything else."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height

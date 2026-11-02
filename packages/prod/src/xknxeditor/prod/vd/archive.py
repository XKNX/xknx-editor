"""Open a ``.vd1`` … ``.vd5`` product database file.

The file is a ZipCrypto-encrypted ZIP archive holding the table dump (a file whose extension ends
with ``_``, e.g. ``ets.vd_`` or ``ets2.vd_``) and, for older databases, help files and plug-in
libraries next to it. The archive comment is the folder the dump was exported to.
"""

from __future__ import annotations

import importlib
import io
import re
import struct
import zipfile
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from xknxeditor.prod.errors import ArchiveError, PasswordRequired, check_cancelled

from .dump import Dump

if TYPE_CHECKING:
    import threading
    from collections.abc import Sequence

_INIT_KEYS: tuple[int, int, int] = (0x12345678, 0x23456789, 0x34567890)
_CHUNK = 1 << 20
_DUMP_NAME = re.compile(r"\.[^./\\]*_$")
VD_SUFFIX = re.compile(r"\.vd[1-5]$", re.IGNORECASE)


def _dedupe(values: list[bytes]) -> tuple[bytes, ...]:
    seen: set[bytes] = set()
    out: list[bytes] = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return tuple(out)


def _baked() -> list[bytes]:
    try:
        module = importlib.import_module(f"{__package__}._secret")
    except Exception:
        return []
    raw = getattr(module, "PASSWORDS", ())
    values = cast(
        "tuple[object, ...] | list[object]",
        raw if isinstance(raw, tuple | list) else (),
    )
    out: list[bytes] = []
    for v in values:
        if isinstance(v, bytes):
            out.append(v)
        elif isinstance(v, str):
            out.append(v.encode())
    return out


def configured_passwords() -> tuple[bytes, ...]:
    """Passwords available without asking, from an optional local ``_secret`` module."""
    return _dedupe(_baked())


def _resolve(passwords: Sequence[bytes] | None) -> tuple[bytes, ...]:
    return _dedupe([*(passwords or ()), *configured_passwords()])


def is_vd_file(name: str) -> bool:
    """Whether ``name`` has the extension of an older product database (``.vd1`` … ``.vd5``)."""
    return VD_SUFFIX.search(name) is not None


def holds_vd_dump(names: list[str]) -> bool:
    """Whether a ZIP with the entry ``names`` is a product database: it holds a table dump (a file
    whose extension ends with ``_``) and no ``knx_master.xml``."""
    return "knx_master.xml" not in names and any(_DUMP_NAME.search(n) for n in names)


def read_dump(
    source: str | Path | bytes,
    *,
    passwords: Sequence[bytes] | None = None,
    cancel: threading.Event | None = None,
) -> Dump:
    """Read the table dump of a ``.vd*`` file (path or raw bytes).

    ``passwords`` are tried together with :func:`configured_passwords`; when none are available a
    :class:`~xknxeditor.prod.errors.PasswordRequired` is raised. Setting ``cancel`` stops the
    decryption with :class:`ImportCancelled`."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"Not a product database (no ZIP archive): {e}") from e
    with archive:
        entry = _dump_entry(archive)
        return Dump(_read_entry(archive, data, entry, _resolve(passwords), cancel))


def _dump_entry(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    candidates = [i for i in archive.infolist() if _DUMP_NAME.search(i.filename)]
    if not candidates:
        raise ArchiveError("The product database holds no table dump")
    # Prefer the dump in the folder the archive comment names (the export folder).
    folder = archive.comment.decode("cp1252", "replace").replace("\\", "/").lower()
    folder = folder.split(":", 1)[-1].strip("/")
    for info in candidates:
        if info.filename.lower().rsplit("/", 1)[0] == folder:
            return info
    return candidates[0]


def _read_entry(
    archive: zipfile.ZipFile,
    data: bytes,
    entry: zipfile.ZipInfo,
    passwords: Sequence[bytes],
    cancel: threading.Event | None = None,
) -> bytes:
    if not entry.flag_bits & 0x1:
        return archive.read(entry)
    if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ArchiveError(
            f"Unsupported compression in the product database: {entry.compress_type}"
        )
    if not passwords:
        raise PasswordRequired("A password is required to read this product database")
    # zipfile decrypts ZipCrypto byte by byte in a slow loop (minutes for a large database); this
    # decrypter uses lookup tables and is about 20 times faster.
    offset = entry.header_offset
    name_length, extra_length = struct.unpack_from("<HH", data, offset + 26)
    start = offset + 30 + name_length + extra_length
    encrypted = data[start : start + entry.compress_size]
    # The last byte of the 12-byte encryption header checks the password: the high byte of the CRC,
    # or of the modification time when the sizes follow the data (flag bit 3).
    check = (entry._raw_time >> 8) & 0xFF if entry.flag_bits & 0x8 else entry.CRC >> 24  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
    for password in passwords:
        keys = _keys(password)
        header = _decrypt(encrypted[:12], keys)
        if header[11] != check:
            continue
        plain = bytearray()
        keys = _keys(password)
        # In parts, so a cancel takes effect within a fraction of a second.
        for part in range(0, len(encrypted), _CHUNK):
            check_cancelled(cancel)
            plain += _decrypt(encrypted[part : part + _CHUNK], keys)
        del plain[:12]
        content = (
            zlib.decompress(plain, -15)
            if entry.compress_type == zipfile.ZIP_DEFLATED
            else bytes(plain)
        )
        if zlib.crc32(content) != entry.CRC:
            raise ArchiveError("The product database is damaged (CRC error)")
        return content
    raise ArchiveError("The product database is protected with an unknown password")


def _crc_table() -> list[int]:
    table: list[int] = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    return table


_CRC = _crc_table()
# The key stream byte for each value of the low 16 bits of the third key.
_STREAM = bytes((((k | 2) * ((k | 2) ^ 1)) >> 8) & 0xFF for k in range(65536))


def _keys(password: bytes) -> list[int]:
    keys = list(_INIT_KEYS)
    _decrypt_into(password, keys, encrypt=True)
    return keys


def _decrypt(data: bytes, keys: list[int]) -> bytearray:
    return _decrypt_into(data, keys, encrypt=False)


def _decrypt_into(data: bytes, keys: list[int], *, encrypt: bool) -> bytearray:
    """ZipCrypto over ``data``; updates ``keys``. ``encrypt`` feeds ``data`` itself to the keys (as
    for the password), otherwise the decrypted bytes."""
    k0, k1, k2 = keys
    crc = _CRC
    stream = _STREAM
    out = bytearray(len(data))
    for i, b in enumerate(data):
        c = b if encrypt else b ^ stream[k2 & 0xFFFF]
        out[i] = c
        k0 = crc[(k0 ^ c) & 0xFF] ^ (k0 >> 8)
        k1 = ((k1 + (k0 & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        k2 = crc[(k2 ^ (k1 >> 24)) & 0xFF] ^ (k2 >> 8)
    keys[:] = [k0, k1, k2]
    return out

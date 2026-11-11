"""Keep only the product data of an archive: ``knx_master.xml``, the ``M-XXXX/`` folders and their
``M-XXXX.signature``.

A ``.knxproj`` carries its product data in the same layout as a ``.knxprod``, next to the project
itself (``P-XXXX/`` or, password protected, ``P-XXXX.zip``). The catalog stores only the product
part, so neither its store nor a product exported from it holds a project."""

from __future__ import annotations

import copy
import io
import struct
import zipfile
from collections.abc import Collection

from xknxeditor.prod.archive import MANUFACTURER_PATTERN
from xknxeditor.prod.errors import ArchiveError

MASTER_XML = "knx_master.xml"
_LOCAL_HEADER = b"PK\x03\x04"
_DESCRIPTOR = b"PK\x07\x08"
_ZIP64_LIMIT = 0xFFFFFFFF


class NoCatalogError(ArchiveError):
    """The product data of a manufacturer has no ``Catalog.xml`` (as a project may carry it), so it
    is no ``.knxprod`` that can be imported again."""

    def __init__(self, manufacturer_id: str) -> None:
        super().__init__(f"Missing required file: {manufacturer_id}/Catalog.xml")
        self.manufacturer_id = manufacturer_id


def is_product_member(
    name: str, manufacturer_ids: Collection[str] | None = None
) -> bool:
    """Whether the archive entry ``name`` is product data: ``knx_master.xml``, a ``M-XXXX/`` entry
    or a ``M-XXXX.signature``; with ``manufacturer_ids`` only of those manufacturers."""
    if name == MASTER_XML:
        return True
    head, slash, _rest = name.partition("/")
    if slash:
        manufacturer = head
    elif name.endswith(".signature"):
        manufacturer = name.removesuffix(".signature")
    else:
        return False
    if not MANUFACTURER_PATTERN.match(manufacturer):
        return False
    return manufacturer_ids is None or manufacturer in manufacturer_ids


def product_members(
    content: bytes, manufacturer_ids: Collection[str] | None = None
) -> bytes:
    """``content`` (a ZIP archive) with only its product data (see :func:`is_product_member`).

    Returns ``content`` itself when nothing is left out, so a ``.knxprod`` keeps its bytes (and
    the catalog its content hash). Otherwise the kept entries are copied as they are stored, in
    their order and without compressing them again: the same archive gives the same bytes, whatever
    zlib this Python has, and so the same name in the catalog's store. Raises
    :class:`~xknxeditor.prod.errors.ArchiveError` when ``content`` is no ZIP archive."""
    try:
        source = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"Invalid ZIP archive: {e}") from e
    with source:
        entries = source.infolist()
        kept = [e for e in entries if is_product_member(e.filename, manufacturer_ids)]
        if len(kept) == len(entries):
            return content
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as out:
            for entry in kept:
                _copy_raw(content, source, entry, out)
    return buffer.getvalue()


def _copy_raw(
    content: bytes,
    source: zipfile.ZipFile,
    entry: zipfile.ZipInfo,
    out: zipfile.ZipFile,
) -> None:
    """Append ``entry`` of ``source`` (whose bytes are ``content``) to ``out`` as it is stored:
    local header, compressed data and data descriptor, with its ``ZipInfo`` in the directory."""
    start = entry.header_offset
    header = content[start : start + 30]
    if (
        header[:4] != _LOCAL_HEADER
        or max(entry.compress_size, entry.file_size) >= _ZIP64_LIMIT
    ):
        # Not a plain local header, or ZIP64 sizes (no product file is 4 GB): write it again.
        out.writestr(copy.copy(entry), source.read(entry))
        return
    name_length, extra_length = struct.unpack("<HH", header[26:30])
    end = start + 30 + name_length + extra_length + entry.compress_size
    if entry.flag_bits & 0x08:  # a data descriptor follows the data: CRC and both sizes
        extra = content[
            start + 30 + name_length : start + 30 + name_length + extra_length
        ]
        sizes = 16 if _has_zip64_extra(extra) else 8  # ZIP64: 8 bytes per size
        end += 4 + sizes + (4 if content[end : end + 4] == _DESCRIPTOR else 0)
    fp = out.fp
    assert fp is not None
    copied = copy.copy(entry)
    copied.header_offset = fp.tell()
    fp.write(content[start:end])
    out.filelist.append(copied)
    out.NameToInfo[copied.filename] = copied
    out.start_dir = fp.tell()


def _has_zip64_extra(extra: bytes) -> bool:
    """Whether the extra field of a local header holds a ZIP64 record (header id 0x0001)."""
    offset = 0
    while offset + 4 <= len(extra):
        header_id, size = struct.unpack("<HH", extra[offset : offset + 4])
        if header_id == 0x0001:
            return True
        offset += 4 + size
    return False


def has_other_members(path: str) -> bool:
    """Whether the archive at ``path`` holds anything but product data (e.g. a whole project)."""
    with zipfile.ZipFile(path) as archive:
        return not all(is_product_member(name) for name in archive.namelist())

"""Copy members of a ZIP into a new one as they are stored (their compressed data unchanged): no
unpacking and packing again, and never more than one member's compressed data in memory besides
the new ZIP. The members read back the same, octet for octet."""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Iterable

_LOCAL = struct.Struct("<4sHHHHHIIIHH")
_CENTRAL = struct.Struct("<4sHHHHHHIIIHHHHHII")
_END = struct.Struct("<4sHHHHIIH")
_LOCAL_SIGNATURE = b"PK\x03\x04"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_END_SIGNATURE = b"PK\x05\x06"
_ENCRYPTED = 0x1
_DATA_DESCRIPTOR = 0x8
_UTF8_NAME = 0x800
# Beyond these a ZIP needs its ZIP64 extensions, which the copy does not write.
_LIMIT = 0xFFFFFFFF
_MEMBERS = 0xFFFF


def copy_members(source: zipfile.ZipFile, names: Iterable[str]) -> bytes | None:
    """A ZIP of the members ``names`` of ``source`` (read from a file) with their compressed data
    copied as they are stored; None when one cannot be copied so (encrypted, or one the copy
    would need ZIP64 for): the caller packs them again then."""
    fp = source.fp
    if fp is None:
        return None
    out = io.BytesIO()
    central: list[bytes] = []
    for name in names:
        info = source.getinfo(name)
        flags = info.flag_bits & ~_DATA_DESCRIPTOR
        if (
            info.flag_bits & _ENCRYPTED
            or max(info.file_size, info.compress_size, info.header_offset) >= _LIMIT
        ):
            return None
        try:
            encoded = name.encode("utf-8" if flags & _UTF8_NAME else "cp437")
        except UnicodeEncodeError:
            return None
        fp.seek(info.header_offset)
        header = fp.read(_LOCAL.size)
        if len(header) != _LOCAL.size:
            return None
        signature, *_rest, name_length, extra_length = _LOCAL.unpack(header)
        if signature != _LOCAL_SIGNATURE:
            return None
        fp.seek(info.header_offset + _LOCAL.size + name_length + extra_length)
        data = fp.read(info.compress_size)
        if len(data) != info.compress_size:
            return None
        year, month, day, hour, minute, second = info.date_time
        dos_date = (year - 1980) << 9 | month << 5 | day
        dos_time = hour << 11 | minute << 5 | second // 2
        offset = out.tell()
        if offset >= _LIMIT:
            return None
        out.write(
            _LOCAL.pack(
                _LOCAL_SIGNATURE,
                info.extract_version,
                flags,
                info.compress_type,
                dos_time,
                dos_date,
                info.CRC,
                info.compress_size,
                info.file_size,
                len(encoded),
                0,
            )
        )
        out.write(encoded)
        out.write(data)
        central.append(
            _CENTRAL.pack(
                _CENTRAL_SIGNATURE,
                info.create_version | info.create_system << 8,
                info.extract_version,
                flags,
                info.compress_type,
                dos_time,
                dos_date,
                info.CRC,
                info.compress_size,
                info.file_size,
                len(encoded),
                0,
                0,
                0,
                info.internal_attr,
                info.external_attr,
                offset,
            )
            + encoded
        )
    if len(central) > _MEMBERS:
        return None
    start = out.tell()
    for entry in central:
        out.write(entry)
    size = out.tell() - start
    if start >= _LIMIT or size >= _LIMIT:
        return None
    out.write(
        _END.pack(_END_SIGNATURE, 0, 0, len(central), len(central), size, start, 0)
    )
    return out.getvalue()

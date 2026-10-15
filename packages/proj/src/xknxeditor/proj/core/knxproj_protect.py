"""Password protection of the project folder in a ``.knxproj`` archive.

A protected archive carries the project folder ``P-XXXX/`` as an encrypted zip ``P-XXXX.zip`` (stored
uncompressed in the archive) and says so in ``P-XXXX.info`` (``"IsPasswordProtected": true``). The
folder signature ``P-XXXX.signature`` and the project certificate cover the decrypted folder, so
they do not depend on the password.

How the inner zip is encrypted depends on the archive's schema (the namespace of its
``knx_master.xml``):

- project schema 21 and later: WinZip AES-256 (AE-1, deflated entries), with a zip password derived
  from the project password (:func:`ets6_zip_password`);
- older schemas: traditional PKWARE encryption (ZipCrypto) with the project password itself.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import struct
import zipfile
import zlib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pyzipper

# The first project schema whose protected folder is AES encrypted.
AES_SCHEMA = 21

_PROTECTED_FOLDER = re.compile(r"P-[0-9A-Fa-f]+\.zip")


def is_password_protected(source: Path | str) -> bool:
    """Whether the ``.knxproj`` at ``source`` carries its project folder password-protected."""
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    return any(_PROTECTED_FOLDER.fullmatch(name) for name in names)


def ets6_zip_password(password: str) -> bytes:
    """The zip password of a schema 21+ project folder protected with ``password``."""
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-16-le"),
        b"21.project.ets.knx.org",
        65536,
        dklen=32,
    )
    return base64.b64encode(key)


def archive_schema(master_xml: bytes) -> int:
    """The project schema of an archive: the version in its ``knx_master.xml`` namespace."""
    match = re.search(rb'xmlns="[^"]*/(\d+)"', master_xml[:1000])
    if match is None:
        raise ValueError("knx_master.xml carries no KNX namespace")
    return int(match.group(1))


def protect_project_folder(members: dict[str, bytes], pid: str, password: str) -> None:
    """Replace the project folder ``pid/`` in the archive ``members`` with its encrypted
    ``pid.zip``, encrypted for the schema of the archive's ``knx_master.xml``."""
    prefix = f"{pid}/"
    files = {
        path[len(prefix) :]: data
        for path, data in members.items()
        if path.startswith(prefix)
    }
    for path in files:
        del members[prefix + path]
    schema = archive_schema(members["knx_master.xml"])
    members[f"{pid}.zip"] = protected_folder_zip(files, password, schema)


def protected_folder_zip(
    files: Mapping[str, bytes], password: str, schema: int
) -> bytes:
    """The encrypted ``P-XXXX.zip`` of a project folder; ``files`` maps each path relative to the
    folder (``/`` separated) to its content."""
    if schema >= AES_SCHEMA:
        return _aes_zip(files, ets6_zip_password(password))
    return _zipcrypto_zip(files, password.encode("utf-8"))


def _aes_zip(files: Mapping[str, bytes], zip_password: bytes) -> bytes:
    buffer = io.BytesIO()
    # pyzipper has no type hints.
    writer: Any = pyzipper.AESZipFile(
        buffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    )
    with writer as archive:
        archive.setpassword(zip_password)
        archive.setencryption(pyzipper.WZ_AES, nbits=256, force_wz_aes_version=1)
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# --- traditional PKWARE encryption ("ZipCrypto"), APPNOTE.TXT section 6.1 ---------------------------


def _crc_table() -> list[int]:
    table: list[int] = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    return table


_CRC = _crc_table()


class _ZipCrypto:
    def __init__(self, password: bytes) -> None:
        self._k0, self._k1, self._k2 = 0x12345678, 0x23456789, 0x34567890
        for byte in password:
            self._update(byte)

    def _update(self, byte: int) -> None:
        self._k0 = (self._k0 >> 8) ^ _CRC[(self._k0 ^ byte) & 0xFF]
        self._k1 = ((self._k1 + (self._k0 & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        self._k2 = (self._k2 >> 8) ^ _CRC[(self._k2 ^ (self._k1 >> 24)) & 0xFF]

    def encrypt(self, data: bytes) -> bytes:
        out = bytearray(len(data))
        for i, byte in enumerate(data):
            temp = (self._k2 | 2) & 0xFFFF
            out[i] = byte ^ (((temp * (temp ^ 1)) >> 8) & 0xFF)
            self._update(byte)
        return bytes(out)


def _dos_time(moment: datetime) -> tuple[int, int]:
    time = (moment.hour << 11) | (moment.minute << 5) | (moment.second // 2)
    date = ((moment.year - 1980) << 9) | (moment.month << 5) | moment.day
    return time, date


def _zipcrypto_zip(files: Mapping[str, bytes], password: bytes) -> bytes:
    """A zip of deflated, ZipCrypto-encrypted entries."""
    time, date = _dos_time(datetime.now())
    out = io.BytesIO()
    central: list[bytes] = []
    for name, data in files.items():
        encoded = name.encode("utf-8")
        flags = 0x0001 | (0x0800 if not name.isascii() else 0)
        crc = zlib.crc32(data)
        compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
        compressed = compressor.compress(data) + compressor.flush()
        # The 12-byte encryption header ends in the CRC's high byte, the password check.
        header = os.urandom(11) + bytes([crc >> 24])
        payload = _ZipCrypto(password).encrypt(header + compressed)
        offset = out.tell()
        fields = (20, flags, 8, time, date, crc, len(payload), len(data), len(encoded))
        out.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, *fields, 0))
        out.write(encoded)
        out.write(payload)
        central.append(
            struct.pack("<IH", 0x02014B50, 20)
            + struct.pack("<HHHHHIIIHHHHHII", *fields, 0, 0, 0, 0, 0, offset)
            + encoded
        )
    start = out.tell()
    for entry in central:
        out.write(entry)
    size = out.tell() - start
    out.write(
        struct.pack(
            "<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central), size, start, 0
        )
    )
    return out.getvalue()

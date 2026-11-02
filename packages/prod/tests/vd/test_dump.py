"""Reading the table dump of a ``.vd*`` product database and unpacking the encrypted archive."""

from __future__ import annotations

import io
import struct
import zipfile
import zlib

import pytest

from xknxeditor.prod.errors import ArchiveError
from xknxeditor.prod.vd import Dump, is_vd_file, read_dump
from xknxeditor.prod.vd import archive as vd_archive

BS = "\\"

_DUMP = "\r\n".join(
    [
        "EX-IM",
        "N C:" + BS + "Ets" + BS + "ets.vd_",
        "K ",
        "D 2015-02-01 10:00:00",
        "V 6.3",
        "H virtual_device",
        "-------------------------------------",
        "T 3 manufacturer",
        "C1 T3 1 4 N MANUFACTURER_ID",
        "C2 T3 3 50 Y MANUFACTURER_NAME",
        "C3 T3 1 4 Y ADDRESS_ID",
        "R 1 T 3 manufacturer",
        "1",
        "Siemens",
        "",
        "R 2 T 3 manufacturer",
        "2",
        "Gr\xfcn " + BS + "'AG" + BS + "' " + BS + BS + " x" + BS + "n2",
        "7",
        "-------------------------------------",
        "T 40 s19_block",
        "C1 T40 1 4 N BLOCK_ID",
        "C2 T40 3 255 Y BLOCK_NAME",
        "C3 T40 8 32767 Y BLOCK_DATA",
        "R 1 T 40 s19_block",
        "5",
        # A value of 40 characters is continued by the next line starting with two backslashes.
        "a" * 40,
        BS + BS + "bc",
        "00ff10",
        "R 2 T 40 s19_block",
        "6",
        # 40 characters not followed by a continuation line: the value ends there.
        "d" * 40,
        "",
        "XXX",
        "",
    ]
).encode("cp1252")


def test_header_tables_and_values() -> None:
    dump = Dump(_DUMP)
    assert dump.version == 0x60003
    assert dump.root_table == "virtual_device"
    assert list(dump.tables) == ["manufacturer", "s19_block"]
    manufacturers = dump.tables["manufacturer"]
    assert [c.name for c in manufacturers.columns] == [
        "MANUFACTURER_ID",
        "MANUFACTURER_NAME",
        "ADDRESS_ID",
    ]
    assert manufacturers.rows == [
        (1, "Siemens", None),
        (2, "Gr\xfcn 'AG' " + BS + " x\n2", 7),
    ]
    blocks = dump.table("S19_BLOCK")
    assert blocks is not None
    assert blocks.rows == [(5, "a" * 40 + "bc", b"\x00\xff\x10"), (6, "d" * 40, None)]
    record = next(blocks.records())
    assert record.int("BLOCK_ID") == 5
    assert record.str("BLOCK_NAME") == "a" * 40 + "bc"
    assert record.bytes("BLOCK_DATA") == b"\x00\xff\x10"
    assert record["MISSING"] is None
    with pytest.raises(TypeError):
        record.str("BLOCK_ID")


def test_old_header_and_errors() -> None:
    old = _DUMP.replace(b"V 6.3", b"V EXIM A")
    assert Dump(old).version == 0x10000
    with pytest.raises(ArchiveError, match="EX-IM"):
        Dump(b"something else")
    with pytest.raises(ArchiveError, match="XXX"):
        Dump(_DUMP.replace(b"XXX", b"YYY"))


def _zipcrypto(name: str, content: bytes, password: bytes, comment: bytes) -> bytes:
    """A ZIP with one deflated, ZipCrypto-encrypted entry (zipfile cannot write encrypted ZIPs)."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    deflated = compressor.compress(content) + compressor.flush()
    crc = zlib.crc32(content)
    keys = vd_archive._keys(password)  # pyright: ignore[reportPrivateUsage]
    plain = bytes(11) + bytes([crc >> 24]) + deflated
    encrypted = bytearray()
    k0, k1, k2 = keys
    for c in plain:
        encrypted.append(c ^ vd_archive._STREAM[k2 & 0xFFFF])  # pyright: ignore[reportPrivateUsage]
        crc_table = vd_archive._CRC  # pyright: ignore[reportPrivateUsage]
        k0 = crc_table[(k0 ^ c) & 0xFF] ^ (k0 >> 8)
        k1 = ((k1 + (k0 & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        k2 = crc_table[(k2 ^ (k1 >> 24)) & 0xFF] ^ (k2 >> 8)
    raw_name = name.encode()
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        1,
        8,
        0,
        0,
        crc,
        len(encrypted),
        len(content),
        len(raw_name),
        0,
    )
    central = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        1,
        8,
        0,
        0,
        crc,
        len(encrypted),
        len(content),
        len(raw_name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    body = local + raw_name + bytes(encrypted)
    directory = central + raw_name
    end = struct.pack(
        "<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(directory), len(body), len(comment)
    )
    return body + directory + end + comment


@pytest.mark.parametrize("password", [b"Orleander", b"Felidae"])
def test_encrypted_archive(password: bytes) -> None:
    data = _zipcrypto("Ets/ets.vd_", _DUMP, password, b"C:" + BS.encode() + b"Ets")
    assert zipfile.ZipFile(io.BytesIO(data)).read("Ets/ets.vd_", pwd=password) == _DUMP
    dump = read_dump(data)
    assert dump.tables["manufacturer"].rows[0] == (1, "Siemens", None)


def test_archive_errors() -> None:
    with pytest.raises(ArchiveError, match="unknown password"):
        read_dump(_zipcrypto("ets.vd_", _DUMP, b"other", b""))
    with pytest.raises(ArchiveError, match="no ZIP"):
        read_dump(b"not a zip")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(ArchiveError, match="no table dump"):
        read_dump(buffer.getvalue())


def test_unencrypted_dump_and_preferred_folder() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("other/old.vd_", _DUMP.replace(b"V 6.3", b"V 4.9"))
        zf.writestr("Ets/ets.vd_", _DUMP)
        zf.comment = b"C:" + BS.encode() + b"Ets"
    assert read_dump(buffer.getvalue()).version == 0x60003


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.vd1", True),
        ("B.VD5", True),
        ("c.vd4", True),
        ("d.vd6", False),
        ("e.knxprod", False),
    ],
)
def test_is_vd_file(name: str, expected: bool) -> None:
    assert is_vd_file(name) is expected

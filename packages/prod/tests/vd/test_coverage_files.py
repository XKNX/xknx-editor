"""File versions of baggage files (installer packages, programs) and the runtime's sort."""

from __future__ import annotations

import random
import struct
from collections.abc import Callable

import pytest

from xknxeditor.prod.vd import netsort
from xknxeditor.prod.vd.msi import product_version
from xknxeditor.prod.vd.netsort import list_sort
from xknxeditor.prod.vd.pefile import file_version, pe_version

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._"
_FREE = 0xFFFFFFFF
_END = 0xFFFFFFFE
_FAT_SECTOR = 0xFFFFFFFD
_SECTOR = 512
_MINI = 64


def _table_name(name: str) -> str:
    """The stream name of an installer table: a marker and two characters per code point."""
    out = [chr(0x4840)]
    for k in range(0, len(name), 2):
        pair = name[k : k + 2]
        if len(pair) == 2:
            out.append(
                chr(0x3800 + _CHARS.index(pair[0]) + (_CHARS.index(pair[1]) << 6))
            )
        else:
            out.append(chr(0x4800 + _CHARS.index(pair[0])))
    return "".join(out)


def _string_pool(
    strings: list[str], codepage: int, long_refs: bool, long_strings: bool
) -> tuple[bytes, bytes]:
    pool = struct.pack(
        "<HH", codepage & 0xFFFF, (codepage >> 16) | (0x8000 if long_refs else 0)
    )
    data = b""
    for s in strings:
        raw = s.encode(f"cp{codepage}")
        if long_strings or len(raw) > 0xFFFF:
            pool += struct.pack("<HHHH", 0, 1, len(raw) & 0xFFFF, len(raw) >> 16)
        else:
            pool += struct.pack("<HH", len(raw), 1)
        data += raw
    return pool, data


def msi(
    properties: dict[str, str],
    *,
    codepage: int = 1252,
    long_refs: bool = False,
    padding: int = 0,
    difat: bool = False,
    long_strings: bool = False,
) -> bytes:
    """A Windows installer package (compound file) holding a string pool and a Property table;
    ``padding`` adds a string of that size (a string pool beyond the mini stream cutoff)."""
    strings = [s for kv in properties.items() for s in kv]
    if padding:
        strings.append("x" * padding)
    pool, data = _string_pool(strings, codepage, long_refs, long_strings)
    width = 3 if long_refs else 2
    refs = list(range(1, len(properties) * 2 + 1))
    names, values = refs[0::2], refs[1::2]
    table = b"".join(r.to_bytes(width, "little") for r in names + values)
    streams = [("!_StringPool", pool), ("!_StringData", data), ("!Property", table)]
    # small streams go to the mini stream, big ones to their own sectors
    mini = b""
    mini_fat: list[int] = []
    entries: list[tuple[str, int, int, int]] = []
    big: list[tuple[int, bytes]] = []
    for name, content in streams:
        if len(content) < 4096:
            start = len(mini) // _MINI
            count = max(1, -(-len(content) // _MINI))
            mini += content.ljust(count * _MINI, b"\0")
            mini_fat += [start + i + 1 for i in range(count - 1)] + [_END]
            entries.append((_table_name(name[1:]), 2, start, len(content)))
        else:
            big.append((len(entries), content))
            entries.append((_table_name(name[1:]), 2, 0, len(content)))
    # sector layout: 0 FAT, 1 directory, 2 mini FAT, 3 DIFAT (optional), then data
    fat: list[int] = [_FAT_SECTOR, _END, _END]
    next_sector = 3
    if difat:
        fat.append(0xFFFFFFFC)
        next_sector = 4
    mini_start = next_sector
    mini_count = max(1, -(-len(mini) // _SECTOR))
    fat += [mini_start + i + 1 for i in range(mini_count - 1)] + [_END]
    sectors_data = mini.ljust(mini_count * _SECTOR, b"\0")
    next_sector += mini_count
    for index, content in big:
        count = -(-len(content) // _SECTOR)
        name, kind, _start, size = entries[index]
        entries[index] = (name, kind, next_sector, size)
        fat += [next_sector + i + 1 for i in range(count - 1)] + [_END]
        sectors_data += content.ljust(count * _SECTOR, b"\0")
        next_sector += count
    fat += [_FREE] * (_SECTOR // 4 - len(fat))

    def entry(name: str, kind: int, start: int, size: int) -> bytes:
        raw = name.encode("utf-16-le") + b"\0\0"
        return (
            raw.ljust(64, b"\0")
            + struct.pack("<HBB", len(raw), kind, 1)
            + struct.pack("<III", _FREE, _FREE, 1 if kind == 5 else _FREE)
            + bytes(16 + 4 + 16)
            + struct.pack("<IQ", start, size)
        )

    directory = entry("Root Entry", 5, mini_start, len(mini))
    for k, (name, kind, start, size) in enumerate(entries):
        right = k + 2 if k + 1 < len(entries) else _FREE
        e = bytearray(entry(name, kind, start, size))
        struct.pack_into("<I", e, 72, right)
        directory += bytes(e)
    directory = directory.ljust(_SECTOR, b"\0")
    mini_fat_sector = struct.pack(f"<{len(mini_fat)}I", *mini_fat).ljust(
        _SECTOR, b"\xff"
    )
    header = bytearray(_SECTOR)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<II", header, 0x2C, 1, 1)
    struct.pack_into(
        "<IIIII", header, 0x38, 4096, 2, 1, 3 if difat else _END, 1 if difat else 0
    )
    difat_entries = [0] + [_FREE] * 108
    struct.pack_into("<109I", header, 0x4C, *difat_entries)
    out = bytes(header) + struct.pack("<128I", *fat) + directory + mini_fat_sector
    if difat:
        out += struct.pack("<128I", *([_FREE] * 127 + [_END]))
    return out + sectors_data


def test_installer_product_version() -> None:
    assert (
        product_version(msi({"ProductName": "Plugin", "ProductVersion": "2.1.7"}))
        == "2.1.7"
    )
    assert product_version(msi({"ProductVersion": "1.0"}, long_refs=True)) == "1.0"
    assert product_version(msi({"ProductVersion": "4.5.6.7"}, difat=True)) == "4.5.6.7"
    # string data beyond the mini stream cutoff; the entry form of strings over 64 KB
    assert product_version(msi({"ProductVersion": "3.3"}, padding=5000)) == "3.3"
    assert product_version(msi({"ProductVersion": "9.9"}, long_strings=True)) == "9.9"
    # texts in the package's code page
    assert (
        product_version(msi({"ProductVersion": "Версия 1"}, codepage=1251))
        == "Версия 1"
    )
    assert product_version(msi({"Manufacturer": "x"})) is None


def test_installer_errors() -> None:
    assert product_version(b"not an installer package") is None
    damaged = bytearray(msi({"ProductVersion": "1.0"}))
    damaged[0x30:0x34] = struct.pack("<I", 40)  # directory sector beyond the file
    assert product_version(bytes(damaged)) is None
    assert product_version(msi({"ProductVersion": "1.0"})[:600]) is None


def test_file_version_of_an_installer_package() -> None:
    assert file_version("Setup.MSI", msi({"ProductVersion": "2.1"})) == "2.1.0.0"
    assert file_version("setup.msi", msi({"ProductVersion": "1.2.3.4.5"})) == "1.2.3.4"
    assert file_version("setup.msi", msi({"ProductName": "x"})) is None
    # only installer packages are read as such
    assert file_version("setup.bin", msi({"ProductVersion": "2.1"})) is None


def test_program_without_version_resource() -> None:
    assert pe_version(b"MZ" + bytes(200)) is None
    info = "VS_VERSION_INFO".encode("utf-16-le")
    # the fixed file info must follow the resource name closely
    far = b"MZ" + info + bytes(100) + b"\xbd\x04\xef\xfe" + bytes(16)
    assert pe_version(far) is None
    assert pe_version(b"MZ" + info + bytes(20)) is None


def _killer(n: int) -> list[int]:
    """An input that drives the depth limited quicksort into its worst case (an adversary
    that fixes values only when compared)."""
    gas = n
    values = [gas] * n
    solid = 0
    candidate = 0

    def compare(x: int, y: int) -> int:
        nonlocal solid, candidate
        if values[x] == gas and values[y] == gas:
            if x == candidate:
                values[x] = solid
            else:
                values[y] = solid
            solid += 1
        if values[x] == gas:
            candidate = x
        elif values[y] == gas:
            candidate = y
        return (values[x] > values[y]) - (values[y] > values[x])

    list_sort(list(range(n)), compare)
    return values


def test_list_sort_falls_back_to_heapsort(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    original = netsort._heapsort  # pyright: ignore[reportPrivateUsage]

    def counting(
        a: list[int], lo: int, hi: int, cmp: Callable[[int, int], int]
    ) -> None:
        calls.append(hi - lo + 1)
        original(a, lo, hi, cmp)

    monkeypatch.setattr(netsort, "_heapsort", counting)
    values = _killer(400)
    list_sort(values, lambda a, b: (a > b) - (a < b))
    assert values == sorted(values)
    assert calls  # the recursion depth ran out
    for _ in range(3):
        items = [random.Random(7).randrange(1000) for _ in range(500)]
        expected = sorted(items)
        list_sort(items, lambda a, b: (a > b) - (a < b))
        assert items == expected


def test_list_sort_small_lists() -> None:
    empty: list[int] = []
    list_sort(empty, lambda a, b: a - b)
    assert empty == []
    one = [5]
    list_sort(one, lambda a, b: a - b)
    assert one == [5]

"""Read the ``ProductVersion`` property of a Windows installer package (``.msi``).

An installer package is a compound file; its tables are streams with encoded names, strings live in
a string pool. Only what reading the ``Property`` table needs is implemented.
"""

from __future__ import annotations

import struct

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._"
_FREE = 0xFFFFFFFF
_END = 0xFFFFFFFE


def _decode_name(name: str) -> str:
    out: list[str] = []
    for ch in name:
        c = ord(ch)
        if 0x3800 <= c < 0x4800:
            c -= 0x3800
            out.append(_CHARS[c & 0x3F])
            out.append(_CHARS[(c >> 6) & 0x3F])
        elif 0x4800 <= c < 0x4840:
            out.append(_CHARS[c - 0x4800])
        elif c == 0x4840:
            out.append("!")
        else:
            out.append(ch)
    return "".join(out)


class _Compound:
    def __init__(self, data: bytes) -> None:
        if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("not a compound file")
        self.data = data
        self.sector = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini = 1 << struct.unpack_from("<H", data, 0x20)[0]
        n_fat, first_dir = struct.unpack_from("<II", data, 0x2C)
        self.cutoff, first_minifat, n_minifat, first_difat, n_difat = (
            struct.unpack_from("<IIIII", data, 0x38)
        )
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sec = first_difat
        for _ in range(n_difat):
            if sec in (_FREE, _END):
                break
            block = self._sector(sec)
            entries = struct.unpack(f"<{self.sector // 4}I", block)
            difat.extend(entries[:-1])
            sec = entries[-1]
        self.fat: list[int] = []
        for s in difat[:n_fat]:
            if s in (_FREE, _END):
                continue
            self.fat.extend(struct.unpack(f"<{self.sector // 4}I", self._sector(s)))
        dirs = self._chain(first_dir)
        self.entries: list[tuple[str, int, int, int]] = []
        for off in range(0, len(dirs), 128):
            raw = dirs[off : off + 128]
            if len(raw) < 128:
                break
            name_len = struct.unpack_from("<H", raw, 64)[0]
            name = raw[: max(0, name_len - 2)].decode("utf-16-le", errors="replace")
            kind = raw[66]
            start = struct.unpack_from("<I", raw, 116)[0]
            size = struct.unpack_from("<Q", raw, 120)[0] & 0xFFFFFFFF
            self.entries.append((name, kind, start, size))
        root = self.entries[0]
        self.ministream = self._chain(root[2])[: root[3]]
        self.minifat: list[int] = []
        if n_minifat:
            raw = self._chain(first_minifat)
            self.minifat = list(struct.unpack(f"<{len(raw) // 4}I", raw))

    def _sector(self, index: int) -> bytes:
        start = (index + 1) * self.sector
        return self.data[start : start + self.sector]

    def _chain(self, start: int) -> bytes:
        out = bytearray()
        sec = start
        seen = 0
        while (
            sec not in (_FREE, _END) and sec < len(self.fat) and seen <= len(self.fat)
        ):
            out += self._sector(sec)
            sec = self.fat[sec]
            seen += 1
        return bytes(out)

    def stream(self, name: str) -> bytes | None:
        for entry_name, kind, start, size in self.entries:
            if kind != 2 or _decode_name(entry_name) != name:
                continue
            if size < self.cutoff:
                out = bytearray()
                sec = start
                while (
                    sec not in (_FREE, _END)
                    and sec < len(self.minifat)
                    and len(out) < size
                ):
                    out += self.ministream[sec * self.mini : (sec + 1) * self.mini]
                    sec = self.minifat[sec]
                return bytes(out[:size])
            return self._chain(start)[:size]
        return None


def product_version(data: bytes) -> str | None:
    try:
        cf = _Compound(data)
        pool = cf.stream("!_StringPool")
        strings = cf.stream("!_StringData")
        table = cf.stream("!Property")
    except (ValueError, struct.error, IndexError):
        return None
    if pool is None or strings is None or table is None or len(pool) < 4:
        return None
    words = struct.unpack(f"<{len(pool) // 2}H", pool[: len(pool) // 2 * 2])
    codepage = words[0] | ((words[1] & 0x7FFF) << 16)
    long_refs = bool(words[1] & 0x8000)
    encoding = f"cp{codepage}" if codepage else "cp1252"
    values: list[str] = [""]
    pos = 0
    i = 2
    while i + 1 < len(words):
        length, refs = words[i], words[i + 1]
        if length == 0 and refs != 0 and i + 3 < len(words):
            length = (words[i + 3] << 16) | words[i + 2]
            i += 2
        raw = strings[pos : pos + length]
        pos += length
        try:
            values.append(raw.decode(encoding))
        except (LookupError, UnicodeDecodeError):
            values.append(raw.decode("cp1252", errors="replace"))
        i += 2
    width = 3 if long_refs else 2
    rows = len(table) // (2 * width)

    def cell(column: int, row: int) -> str:
        at = (column * rows + row) * width
        ref = int.from_bytes(table[at : at + width], "little")
        return values[ref] if ref < len(values) else ""

    for row in range(rows):
        if cell(0, row) == "ProductVersion":
            return cell(1, row)
    return None

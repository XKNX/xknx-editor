"""Read the table dump inside a ``.vd*`` product database.

The older KNX tools exported their relational product database as a text file ("EX-IM" format), one
value per line, in the Windows-1252 code page:

```
EX-IM
N <original path>
...
V 6.3                      database schema version (major.minor)
H virtual_device           root table
-------------------------------------
T 3 manufacturer           table number and name
C1 T3 1 4 N MANUFACTURER_ID  column: type, size, nullable ("Y"/"N"), name
R 1 T 3 manufacturer       a row, followed by one value per column
1
Siemens
                           (an empty line is a NULL value)
...
XXX                        end of the dump
```

A value longer than one line is wrapped: a line whose length is a multiple of 40 (or 40·k+2 for a
continuation line) is followed by continuation lines starting with two backslashes. Backslash
escapes (``\\n``, ``\\r``, ``\\t``, ``\\'``, ``\\\\``) encode line breaks and backslashes; binary
columns hold hex pairs.

Tables are parsed when first used: the dump of a whole manufacturer database has tens of millions of
lines, and the conversion reads only some tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknxeditor.prod.errors import ArchiveError

if TYPE_CHECKING:
    from collections.abc import Iterator

Value = int | str | bytes | None

_ENCODING = "cp1252"
_CONTINUATION = b"\\\\"
_SPACED_CONTINUATION = b"\\\\ \\\\"
# A table starts after a separator line of dashes: "T <number> <name>", then its column lines.
_TABLE_START = re.compile(rb"(?:^|\n)-{5,}[^\n]*\nT (\d+) (\S+)[^\n]*\n")
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "'": "'", "\\": "\\"}

# Column types of the dump: 1 = 32-bit integer, 2 = 16-bit integer, 6/8 = binary (hex), all others
# (3, 4, 5, 7: text, memo, floating point and date) stay text.
_INTEGER_TYPES = frozenset({1, 2})
_BINARY_TYPES = frozenset({6, 8})


@dataclass(frozen=True, slots=True)
class Column:
    """A column of a dump table."""

    name: str
    kind: int
    size: int
    nullable: bool


class Table:
    """One table of the dump; its rows are read on first access."""

    __slots__ = ("_dump", "_rows", "columns", "index", "name", "number", "region")

    name: str
    number: int
    columns: tuple[Column, ...]
    index: dict[str, int]  # column name (upper case) -> position in a row
    region: tuple[int, int]  # byte range of the rows in the dump
    _rows: list[tuple[Value, ...]] | None

    def __init__(
        self,
        dump: Dump,
        name: str,
        number: int,
        columns: tuple[Column, ...],
        region: tuple[int, int],
    ) -> None:
        self._dump = dump
        self.name = name
        self.number = number
        self.columns = columns
        self.index = {c.name.upper(): i for i, c in enumerate(columns)}
        self.region = region
        self._rows = None

    @property
    def rows(self) -> list[tuple[Value, ...]]:
        """The rows, in the order of the dump."""
        if self._rows is None:
            self._rows = self._dump.read_rows(self)
        return self._rows

    def __len__(self) -> int:
        return len(self.rows)

    def has(self, column: str) -> bool:
        return column.upper() in self.index

    def column(self, column: str) -> int:
        """Position of ``column`` in a row; raises ``KeyError`` for an unknown column."""
        return self.index[column.upper()]

    def records(self) -> Iterator[Record]:
        for row in self.rows:
            yield Record(self.index, row)


class Record:
    """A row with access by column name; a column the table does not have reads as NULL."""

    __slots__ = ("_index", "row")

    def __init__(self, index: dict[str, int], row: tuple[Value, ...]) -> None:
        self._index = index
        self.row = row

    def __getitem__(self, column: str) -> Value:
        i = self._index.get(column)
        return None if i is None else self.row[i]

    def int(self, column: str) -> int | None:
        value = self[column]
        if value is None or isinstance(value, int):
            return value
        raise TypeError(f"column {column} is not an integer column")

    def str(self, column: str) -> str | None:
        value = self[column]
        if value is None or isinstance(value, str):
            return value
        raise TypeError(f"column {column} is not a text column")

    def bytes(self, column: str) -> bytes | None:
        value = self[column]
        if value is None or isinstance(value, bytes):
            return value
        raise TypeError(f"column {column} is not a binary column")


class Dump:
    """The tables of an EX-IM dump."""

    __slots__ = ("_data", "root_table", "tables", "version")

    version: int  # (major << 16) | minor, e.g. 0x60003 for "V 6.3"
    root_table: str
    tables: dict[str, Table]  # by lower-case name

    def __init__(self, data: bytes) -> None:
        self._data = data.replace(b"\r\n", b"\n") if b"\r\n" in data else data
        self.version = 0
        self.root_table = ""
        self.tables = {}
        self._scan()

    def table(self, name: str) -> Table | None:
        return self.tables.get(name.lower())

    def _scan(self) -> None:
        """Read the header and the table definitions; the rows of each table are read later."""
        data = self._data
        if not data.startswith(b"EX-IM"):
            raise ArchiveError("Not a product database (no EX-IM header)")
        first = _TABLE_START.search(data)
        for line in data[: first.start() if first else len(data)].split(b"\n"):
            if line.startswith(b"V EXIM A"):
                self.version = 0x10000
            elif line.startswith(b"V "):
                major, _, minor = line[2:].decode("ascii").partition(".")
                self.version = (int(major) << 16) + int(minor)
            elif line.startswith(b"H "):
                self.root_table = line[2:].decode(_ENCODING).strip()
        if self.version == 0:
            self.version = 0x10000
        end = data.rfind(b"\nXXX")
        if end < 0:
            raise ArchiveError("the product database ends early (no XXX)")
        starts = list(_TABLE_START.finditer(data, 0, end + 1))
        for k, match in enumerate(starts):
            region_end = starts[k + 1].start() if k + 1 < len(starts) else end
            self._add_table(match, region_end)

    def _add_table(self, match: re.Match[bytes], region_end: int) -> None:
        data = self._data
        pos = match.end()
        columns: list[Column] = []
        while data.startswith(b"C", pos):
            eol = data.index(b"\n", pos)
            parts = data[pos:eol].decode(_ENCODING).split(" ")
            columns.append(
                Column(parts[5], int(parts[2]), int(parts[3]), parts[4] == "Y")
            )
            pos = eol + 1
        name = match.group(2).decode(_ENCODING)
        table = Table(
            self, name, int(match.group(1)), tuple(columns), (pos, region_end)
        )
        self.tables[name.lower()] = table

    def read_rows(self, table: Table) -> list[tuple[Value, ...]]:
        start, end = table.region
        lines = self._data[start:end].split(b"\n")
        columns = table.columns
        rows: list[tuple[Value, ...]] = []
        strings: dict[str, str] = {}
        i = 0
        n = len(lines)
        while i < n and lines[i].startswith(b"R "):
            i += 1
            values: list[Value] = []
            for column in columns:
                raw, i = _next_value(lines, i)
                values.append(_convert(raw, column, strings))
            rows.append(tuple(values))
        return rows


def _next_value(lines: list[bytes], i: int) -> tuple[bytes, int]:
    """One value starting at line ``i`` with its continuation lines; returns the next line index."""
    if i >= len(lines):
        raise ArchiveError("the product database ends inside a row")
    line = lines[i]
    i += 1
    if not _may_continue(line):
        return line, i
    parts = [line]
    while i < len(lines) and lines[i].startswith(_CONTINUATION):
        line = lines[i]
        parts.append(line[2:])
        i += 1
        if not _may_continue(line):
            break
    return b"".join(parts), i


def _may_continue(line: bytes) -> bool:
    length = len(line) % 40
    return (
        length == 0
        or (length == 2 and line.startswith(_CONTINUATION))
        or (length == 3 and line.startswith(_SPACED_CONTINUATION))
    )


def _convert(raw: bytes, column: Column, strings: dict[str, str]) -> Value:
    if not raw and column.nullable:
        return None
    if column.kind in _BINARY_TYPES:
        return bytes.fromhex(raw.decode("ascii"))
    if column.kind in _INTEGER_TYPES:
        return int(raw)
    text = raw.decode(_ENCODING)
    if "\\" in text:
        text = _unescape(text)
    return strings.setdefault(text, text)


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    last = len(text) - 1
    while i < len(text):
        c = text[i]
        if c == "\\" and i < last:
            i += 1
            c = text[i]
            out.append(_ESCAPES.get(c, c))
        else:
            out.append(c)
        i += 1
    return "".join(out)

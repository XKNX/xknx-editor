"""The product database of a ``.vd*`` file as mutable tables.

The schema upgrades (:mod:`.upgrade`) add, rename and fill columns and tables; the conversion to XML
reads the result. A table keeps its rows as lists in dump order, a column is addressed by its
upper-case name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .dump import Dump, Table, Value


class DbTable:
    """A table: column names and rows (lists of values in column order)."""

    __slots__ = ("_index", "_rows", "_source", "columns", "name", "nullable")

    name: str
    columns: list[str]
    nullable: frozenset[str]  # the columns the dump declares nullable ("Y")
    _rows: list[list[Value]] | None
    _source: Table | None
    _index: dict[str, int]

    def __init__(
        self,
        name: str,
        columns: Iterable[str],
        rows: Iterable[Iterable[Value]] = (),
        *,
        source: Table | None = None,
    ) -> None:
        """``source`` is a dump table whose rows are read on first access (instead of ``rows``);
        its column declarations give :attr:`nullable`."""
        self.name = name
        self.columns = [c.upper() for c in columns]
        self._index = {c: i for i, c in enumerate(self.columns)}
        self.nullable = (
            frozenset(c.name.upper() for c in source.columns if c.nullable)
            if source is not None
            else frozenset()
        )
        self._source = source
        self._rows = None if source is not None else [list(r) for r in rows]

    @property
    def rows(self) -> list[list[Value]]:
        """The rows (lists of values in column order), in dump order."""
        if self._rows is None:
            assert self._source is not None
            self._rows = [list(r) for r in self._source.rows]
            self._source = None
        return self._rows

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[Row]:
        index = self._index
        for values in self.rows:
            yield Row(index, values)

    def has(self, column: str) -> bool:
        return column.upper() in self._index

    def col(self, column: str) -> int:
        """Position of ``column``; raises ``KeyError`` for an unknown column."""
        return self._index[column.upper()]

    def add_column(self, column: str, default: Value = None) -> int:
        """Append ``column`` (filled with ``default``) unless the table has it; returns its position."""
        name = column.upper()
        if name in self._index:
            return self._index[name]
        self.columns.append(name)
        self._index[name] = len(self.columns) - 1
        for values in self.rows:
            values.append(default)
        return self._index[name]

    def rename_column(self, old: str, new: str) -> None:
        i = self._index.pop(old.upper())
        self.columns[i] = new.upper()
        self._index[new.upper()] = i

    def drop_column(self, column: str) -> None:
        i = self._index[column.upper()]
        del self.columns[i]
        for values in self.rows:
            del values[i]
        self._index = {c: k for k, c in enumerate(self.columns)}

    def append(self, values: dict[str, Value]) -> Row:
        """Add a row from column values (missing columns are NULL)."""
        row: list[Value] = [None] * len(self.columns)
        for column, value in values.items():
            row[self._index[column.upper()]] = value
        self.rows.append(row)
        return Row(self._index, row)

    def index_by(self, column: str) -> dict[Value, Row]:
        """The rows by the value of a unique column (the last row wins for duplicates)."""
        i = self.col(column)
        index = self._index
        return {values[i]: Row(index, values) for values in self.rows}

    def group_by(self, column: str) -> dict[Value, list[Row]]:
        """The rows by the value of a column, in table order."""
        i = self.col(column)
        index = self._index
        groups: dict[Value, list[Row]] = {}
        for values in self.rows:
            groups.setdefault(values[i], []).append(Row(index, values))
        return groups


class Row:
    """A row with access by column name; a column the table does not have reads as NULL."""

    __slots__ = ("_index", "values")

    def __init__(self, index: dict[str, int], values: list[Value]) -> None:
        self._index = index
        self.values = values

    def __getitem__(self, column: str) -> Value:
        i = self._index.get(column.upper())
        return None if i is None else self.values[i]

    def __setitem__(self, column: str, value: Value) -> None:
        self.values[self._index[column.upper()]] = value

    def has(self, column: str) -> bool:
        return column.upper() in self._index

    def int(self, column: str) -> int | None:
        value = self[column]
        if value is None or isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value)
        raise TypeError(f"column {column} is not an integer column")

    def str(self, column: str) -> str | None:
        value = self[column]
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
        raise TypeError(f"column {column} is not a text column")

    def bytes(self, column: str) -> bytes | None:
        value = self[column]
        if value is None or isinstance(value, bytes):
            return value
        raise TypeError(f"column {column} is not a binary column")


class Database:
    """All tables of a product database, by lower-case name."""

    __slots__ = ("root_table", "tables", "version")

    version: int  # (major << 16) | minor of the schema the tables are in
    root_table: str
    tables: dict[str, DbTable]

    def __init__(self, version: int, root_table: str = "virtual_device") -> None:
        self.version = version
        self.root_table = root_table
        self.tables = {}

    @classmethod
    def from_dump(cls, dump: Dump) -> Database:
        db = cls(dump.version, dump.root_table)
        for name, table in dump.tables.items():
            db.tables[name] = DbTable(
                table.name, (c.name for c in table.columns), source=table
            )
        return db

    def table(self, name: str) -> DbTable | None:
        return self.tables.get(name.lower())

    def require(self, name: str) -> DbTable:
        """The table ``name``; an empty table when the database has none."""
        table = self.tables.get(name.lower())
        return table if table is not None else DbTable(name, ())

    def add_table(self, name: str, columns: Iterable[str]) -> DbTable:
        table = DbTable(name, columns)
        self.tables[name.lower()] = table
        return table

    def drop_table(self, name: str) -> None:
        self.tables.pop(name.lower(), None)

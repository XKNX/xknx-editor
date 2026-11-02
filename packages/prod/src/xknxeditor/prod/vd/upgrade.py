"""Bring a product database to schema 6.3, the schema the conversion to XML reads.

The older tools wrote databases in schemas 1.0, 2.7, 3.19, 4.9, 5.10, 6.2 and 6.3. The converter
reads every one of them and upgrades it step by step (1.0/2.7 → schema 1 → … → 6.3); most steps
leave the tables as they are, the ones that change something are:

- schema 1.0/2.7 → 1: the table ``product_to_program_to_mt`` (media of an application assignment)
  is created, every assignment gets the medium 0 (TP).
- 5.10 → 6.2: ``s19_block`` gets the columns ``RECORD`` (the 16-byte load control record),
  ``MERGE_ID`` and ``PROC_MASK`` (0).

Afterwards every load procedure step (``s19_block`` row) without a record gets one built from its
columns and its ``s19_block_paragraph`` rows, as schema 6.3 stores it.

Reading the dump already renames two tables of a product database (no project): the device
templates ``device_parameter`` / ``device_object`` become ``device_template_parameter`` /
``device_template_object``. :func:`upgrade` applies that renaming as well.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from xknxeditor.prod.errors import ArchiveError

if TYPE_CHECKING:
    from collections.abc import Callable

    from .database import Database, DbTable, Row
    from .dump import Value

__all__ = ["SCHEMA_VERSION", "upgrade"]

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 0x60003
"""The schema of the database after :func:`upgrade` (6.3, as ``(major << 16) | minor``)."""

# Paragraph type (PT_COLUMN_ID) of the binary paragraph: the data bytes of a load control record.
_BINARY_PARAGRAPH = 18
_RECORD_LENGTH = 16
_INTEGER = re.compile("[+-]?[0-9]+")


def upgrade(db: Database) -> None:
    """Upgrade ``db`` in place to schema 6.3 and set ``db.version``.

    Raises :class:`ArchiveError` for an unknown schema version and for data the converter cannot
    read either (values out of range, a database with devices and device templates).
    """
    steps = _STEPS.get(db.version)
    if steps is None:
        raise ArchiveError(
            f"Unknown product database schema {db.version >> 16}.{db.version & 0xFFFF}"
        )
    rename_template_tables(db)
    for step in steps:
        step(db)
    build_load_records(db)
    db.version = SCHEMA_VERSION


def rename_template_tables(db: Database) -> None:
    """The renaming done while reading: in a product database (a ``virtual_device`` table and no
    ``device`` table) the device templates become ``device_template_parameter`` and
    ``device_template_object``. A database with both device tables cannot be converted."""
    has_device = "device" in db.tables
    has_virtual_device = "virtual_device" in db.tables
    if ("device_parameter" in db.tables or "device_object" in db.tables) and has_device:
        if has_virtual_device:
            raise ArchiveError(
                "The product database holds devices and device templates"
            )
        return
    if not has_virtual_device:
        return
    _rename_table(db, "device_parameter", "device_template_parameter")
    _rename_table(db, "device_object", "device_template_object")


def _rename_table(db: Database, old: str, new: str) -> None:
    table = db.tables.pop(old, None)
    if table is None:
        return
    table.name = new
    db.tables[new] = table


def _add_media_table(db: Database) -> None:
    """Schema 1.0/2.7 → 1: one medium (0) per application assignment."""
    assignments = db.table("product_to_program")
    if assignments is None:
        return
    if db.table("product_to_program_to_mt") is not None:
        raise ArchiveError("The product database holds product_to_program_to_mt twice")
    i = assignments.col("PROD2PROG_ID")
    media = db.add_table(
        "PRODUCT_TO_PROGRAM_TO_MT", ("PROD2PROG_ID", "MEDIUM_TYPE_NUMBER")
    )
    media.rows.extend([values[i], 0] for values in assignments.rows)


def _unchanged(db: Database) -> None:
    """A schema step that changes no table."""


def _extend_load_blocks(db: Database) -> None:
    """Schema 5.10 → 6.2: the load procedure steps get a record, a merge id and a mask."""
    blocks = db.table("s19_block")
    if blocks is None or blocks.has("RECORD"):
        return
    blocks.add_column("RECORD")
    blocks.add_column("MERGE_ID")
    blocks.add_column("PROC_MASK", 0)


_STEPS: dict[int, tuple[Callable[[Database], None], ...]] = {
    0x10000: (
        _add_media_table,
        _unchanged,
        _unchanged,
        _extend_load_blocks,
        _unchanged,
    ),
    0x20007: (
        _add_media_table,
        _unchanged,
        _unchanged,
        _extend_load_blocks,
        _unchanged,
    ),
    0x30013: (_unchanged, _unchanged, _extend_load_blocks, _unchanged),
    0x40009: (_unchanged, _extend_load_blocks, _unchanged),
    0x5000A: (_extend_load_blocks, _unchanged),
    0x60002: (_unchanged,),
    0x60003: (),
}


def build_load_records(db: Database) -> None:
    """Fill ``RECORD`` of every ``s19_block`` row that has none (schema 6.3 keeps the load control
    record in one column, older schemas in single columns plus ``s19_block_paragraph`` rows)."""
    blocks = db.table("s19_block")
    if blocks is None:
        return
    if not blocks.has("RECORD"):
        raise ArchiveError(
            "The load procedure table of the product database has no records"
        )
    record = blocks.col("RECORD")
    builder: _RecordBuilder | None = None
    for row in blocks:
        if row.values[record] is None:
            if builder is None:
                builder = _RecordBuilder(db, blocks)
            row.values[record] = builder.build(row)


class _RecordBuilder:
    """Builds the 16-byte load control record of a load procedure step."""

    def __init__(self, db: Database, blocks: DbTable) -> None:
        self._db = db
        self._blocks = blocks
        paragraphs = db.table("s19_block_paragraph")
        self._has_paragraphs = paragraphs is not None
        # (BLOCK_ID, PT_COLUMN_ID) -> paragraph rows, and BLOCK_ID -> all its paragraph rows
        self._paragraphs: dict[tuple[Value, Value], list[Row]] = {}
        self._by_block: dict[Value, list[Row]] = {}
        if paragraphs is not None:
            for row in paragraphs:
                block = row["BLOCK_ID"]
                self._paragraphs.setdefault((block, row["PT_COLUMN_ID"]), []).append(
                    row
                )
                self._by_block.setdefault(block, []).append(row)
        self._programs: dict[Value, Row] | None = None
        self._block_programs: dict[Value, Value] = {}

    def build(self, row: Row) -> bytes | None:
        record = bytearray(_RECORD_LENGTH)
        control = _byte(row["CONTROL_CODE"], "CONTROL_CODE")
        address = _optional(row["SEGMENT_ADDRESS"], 0xFFFF, "SEGMENT_ADDRESS")
        length = (
            0
            if row["SEGMENT_LENGTH"] is None
            else _int(row["SEGMENT_LENGTH"], "SEGMENT_LENGTH")
        )
        segment_type = _optional(row["SEGMENT_TYPE"], 0xFF, "SEGMENT_TYPE")
        segment_id = _optional(row["SEGMENT_ID"], 0xFF, "SEGMENT_ID")
        access = _optional(row["ACCESS_ATTRIBUTES"], 0xFF, "ACCESS_ATTRIBUTES")
        memory_type = _optional(row["MEMORY_TYPE"], 0xFF, "MEMORY_TYPE")
        memory_attributes = _optional(
            row["MEMORY_ATTRIBUTES"], 0xFF, "MEMORY_ATTRIBUTES"
        )
        block = _int(row["BLOCK_ID"], "BLOCK_ID")
        end = _wrap32(address + length - 1)
        record[0] = control
        kind = control & 0x0F
        if kind in (8, 9):  # write memory: start and end address
            _put_range(record, 1, address, end)
        elif kind in (10, 11):  # compare/load memory with data bytes
            _put_range(record, 1, address, end)
            data = self._binary(block)
            if data is not None and len(data) >= 11:
                record[5:16] = data[:11]
        elif control in (5, 6, 7):
            record[1] = self._number(block, 2) & 0xFF
            record[2] = self._number(block, 3) & 0xFF
            value = self._number(block, 4) & 0xFFFF
            record[3] = value >> 8
            record[4] = value & 0xFF
            record[5] = self._number(block, 5) & 0xFF
            data = self._binary(block)
            if data is not None and len(data) >= 10:
                record[6:16] = data[:10]
        elif control == 13:
            value = self._number(block, 1)
            record[1] = _div(value, 256) & 0xFF
            record[2] = _mod(value, 256) & 0xFF
        elif kind == 3:  # segment
            record[1] = segment_type
            record[2] = segment_id
            if segment_type in (0, 1):
                _put_range(record, 3, address, end)
                record[7] = access
                record[8] = memory_type
                record[9] = memory_attributes
            elif segment_type == 2:
                record[3] = address >> 8
                record[4] = address & 0xFF
            elif segment_type == 3:
                for k, paragraph in enumerate((8, 9, 10)):
                    value = self._number(block, paragraph) & 0xFFFF
                    record[3 + 2 * k] = value >> 8
                    record[4 + 2 * k] = value & 0xFF
            elif segment_type == 4:
                value = self._number(block, 11) & 0xFFFF
                record[3] = value >> 8
                record[4] = value & 0xFF
                record[5] = self._number(block, 12) & 0xFF
            elif segment_type == 5:
                for k, paragraph in enumerate((13, 14, 15, 16)):
                    value = self._number(block, paragraph) & 0xFFFF
                    record[3 + 2 * k] = value >> 8
                    record[4 + 2 * k] = value & 0xFF
            elif segment_type == 10:
                record[2] = 0
                record[3] = _div(length, 256) & 0xFF
                record[4] = _mod(length, 256) & 0xFF
            elif segment_type == 11:
                record[3] = _div(length, 16777216) & 0xFF
                record[4] = _div(length, 65536) & 0xFF
                record[5] = _div(length, 256) & 0xFF
                record[6] = _mod(length, 16777216) & 0xFF
            else:
                return None
        return bytes(record)

    def _number(self, block: int, paragraph: int) -> int:
        """DATA_LONG of the paragraph ``paragraph`` of ``block``; 0 when missing or not unique."""
        rows = self._paragraphs.get((block, paragraph), [])
        if len(rows) == 1:
            value = rows[0]["DATA_LONG"]
            return 0 if value is None else _int(value, "DATA_LONG")
        if rows:
            _LOGGER.warning(
                "S19_BLOCK_PARAGRAPH of type %d for S19_BLOCK %d is not unique",
                paragraph,
                block,
            )
            return 0
        for row in self._by_block.get(block, []):
            _LOGGER.debug(
                "Found S19_BLOCK_PARAGRAPH with BLOCK_ID=%d and PT_COLUMN_ID=%s and DATA_LONG=%s",
                block,
                _text(row["PT_COLUMN_ID"]),
                _text(row["DATA_LONG"]),
            )
        _LOGGER.info(
            "Missing S19_BLOCK_PARAGRAPH of type %d for S19_BLOCK %d in ApplicationProgram %s",
            paragraph,
            block,
            self._message_application_id(block),
        )
        return 0

    def _message_application_id(self, block: int) -> str:
        """The application program id for the log message; an id that cannot be built (no
        program, a version that is no number) only changes the message, not the import."""
        try:
            return self._application_id(block)
        except ArchiveError:
            return "?"

    def _binary(self, block: int) -> bytes | None:
        """DATA_BINARY of the binary paragraph of ``block``; None when missing or not unique."""
        if not self._has_paragraphs:
            return None
        rows = self._paragraphs.get((block, _BINARY_PARAGRAPH), [])
        if len(rows) == 1:
            value = rows[0]["DATA_BINARY"]
            return value if isinstance(value, bytes) else None
        if rows:
            _LOGGER.warning(
                "S19_BLOCK_PARAGRAPH of type 18 for S19_BLOCK %d is not unique", block
            )
        return None

    def _application_id(self, block: int) -> str:
        """The application program id of ``block`` (as the converter names it in messages)."""
        if self._programs is None:
            self._programs = {}
            programs = self._db.table("application_program")
            if programs is not None:
                for row in programs:
                    self._programs.setdefault(row["PROGRAM_ID"], row)
            self._block_programs = {}
            for row in self._blocks:
                self._block_programs.setdefault(row["BLOCK_ID"], row["PROGRAM_ID"])
        program = self._programs.get(self._block_programs.get(block))
        if program is None:
            raise ArchiveError(
                f"The load procedure step {block} belongs to no application program"
            )
        manufacturer = _int(program["MANUFACTURER_ID"], "MANUFACTURER_ID")
        device_type = _int(program["DEVICE_TYPE"], "DEVICE_TYPE")
        version = _int(program["PROGRAM_VERSION"], "PROGRAM_VERSION")
        text = f"M-{_hex(manufacturer, 4)}_A-{_hex(device_type, 4)}-{_hex(version, 2)}-0000"
        if not program.has("ORIGINAL_MANUFACTURER_ID"):
            raise ArchiveError(
                "The application programs of the product database have no original manufacturer"
            )
        original = program["ORIGINAL_MANUFACTURER_ID"]
        if original is not None:
            text += f"-O{_hex(_int(original, 'ORIGINAL_MANUFACTURER_ID'), 4)}"
        return text


def _put_range(record: bytearray, at: int, address: int, end: int) -> None:
    """Start and end address (big endian) at ``record[at:at + 4]``."""
    record[at] = address >> 8
    record[at + 1] = address & 0xFF
    record[at + 2] = _div(end, 256) & 0xFF
    record[at + 3] = _mod(end, 256) & 0xFF


def _int(value: Value, column: str) -> int:
    """A column value as a number; NULL or text that is no number is an error."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _INTEGER.fullmatch(text):
            return int(text)
    raise ArchiveError(f"The product database holds {column} {value!r} (no number)")


def _byte(value: Value, column: str) -> int:
    return _checked(value, 0xFF, column)


def _optional(value: Value, maximum: int, column: str) -> int:
    """A column value converted to an unsigned type (0 when NULL); out of range is an error."""
    return 0 if value is None else _checked(value, maximum, column)


def _checked(value: Value, maximum: int, column: str) -> int:
    number = _int(value, column)
    if not 0 <= number <= maximum:
        raise ArchiveError(
            f"The product database holds {column} {number} (out of range)"
        )
    return number


def _wrap32(value: int) -> int:
    """Wrap to a signed 32-bit integer (unchecked arithmetic)."""
    return (value + 0x80000000) % 0x100000000 - 0x80000000


def _div(a: int, b: int) -> int:
    """Integer division truncating toward zero."""
    q = abs(a) // b
    return q if a >= 0 else -q


def _mod(a: int, b: int) -> int:
    """Remainder with the sign of the dividend."""
    return a - _div(a, b) * b


def _hex(value: int, digits: int) -> str:
    """Upper-case hex of a 32-bit integer (two's complement for negative values)."""
    return f"{value & 0xFFFFFFFF:0{digits}X}"


def _text(value: Value) -> str:
    return "" if value is None else str(value)

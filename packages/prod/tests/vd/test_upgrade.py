"""Upgrading a product database to schema 6.3 (load control records, media table, renames)."""

from __future__ import annotations

import pytest

from xknxeditor.prod.errors import ArchiveError
from xknxeditor.prod.vd import Dump, read_dump
from xknxeditor.prod.vd.database import Database
from xknxeditor.prod.vd.upgrade import SCHEMA_VERSION, upgrade

from ._samples import SAMPLES as _SAMPLES

Spec = tuple[int, int, bool, str]  # column type, size, nullable, name
Cell = int | str | None

_PROGRAMS: tuple[list[Spec], list[list[Cell]]] = (
    [
        (1, 4, False, "PROGRAM_ID"),
        (1, 4, True, "MANUFACTURER_ID"),
        (1, 4, True, "DEVICE_TYPE"),
        (3, 5, True, "PROGRAM_VERSION"),
        (1, 4, True, "ORIGINAL_MANUFACTURER_ID"),
    ],
    [[1, 250, 0x1234, "17", None], [2, 8, 0xA0B, " 3", 0x83]],
)

_BLOCK_COLUMNS: list[Spec] = [
    (1, 4, False, "BLOCK_ID"),
    (1, 4, False, "PROGRAM_ID"),
    (3, 50, True, "BLOCK_NAME"),
    (2, 2, True, "CONTROL_CODE"),
    (2, 2, True, "SEGMENT_TYPE"),
    (2, 2, True, "SEGMENT_ID"),
    (1, 4, True, "SEGMENT_ADDRESS"),
    (1, 4, True, "SEGMENT_LENGTH"),
    (2, 2, True, "ACCESS_ATTRIBUTES"),
    (2, 2, True, "MEMORY_TYPE"),
    (2, 2, True, "MEMORY_ATTRIBUTES"),
]
_RECORD_COLUMNS: list[Spec] = [
    (6, 16, True, "Record"),
    (2, 2, True, "MERGE_ID"),
    (2, 2, False, "PROC_MASK"),
]

# BLOCK_ID, PROGRAM_ID, CONTROL_CODE, SEGMENT_TYPE, SEGMENT_ID, SEGMENT_ADDRESS, SEGMENT_LENGTH,
# ACCESS_ATTRIBUTES, MEMORY_TYPE, MEMORY_ATTRIBUTES, and the record the converter builds from them
_BLOCKS: list[tuple[list[Cell], str | None]] = [
    ([1, 1, 0x18, None, None, 0x1234, 16, None, None, None], "1812341243"),
    (
        [2, 1, 0x1A, None, None, 0x100, 32, None, None, None],
        "1A0100011F0102030405060708090A0B",
    ),
    ([3, 1, 0x0A, None, None, 0x200, 4, None, None, None], "0A02000203"),
    (
        [4, 1, 5, None, None, None, None, None, None, None],
        "0512340567890A0B0C0D0E0F10111213",
    ),
    ([5, 2, 6, None, None, None, None, None, None, None], "06"),
    ([6, 1, 13, None, None, None, None, None, None, None], "0D2345"),
    ([7, 1, 3, 0, 1, 0x1000, 0x100, 255, 3, 128], "030001100010FFFF0380"),
    ([8, 1, 0x13, 1, 2, 0x2000, 16, 1, 2, 0], "1301022000200F010200"),
    ([9, 1, 0x23, 2, 3, 0xC000, None, None, None, None], "230203C000"),
    ([10, 1, 0x33, 3, None, None, None, None, None, None], "330300000100020000"),
    ([11, 1, 0x43, 4, 5, None, None, None, None, None], "4304054321FF"),
    ([12, 1, 0x53, 5, 6, None, None, None, None, None], "530506000A0014001E0028"),
    ([13, 1, 0x63, 10, 7, None, 0x1234, None, None, None], "630A001234"),
    ([14, 1, 0x73, 11, 8, None, 0x12345678, None, None, None], "730B0812345678"),
    ([15, 1, 0x83, 6, None, None, None, None, None, None], None),
    ([16, 1, 0x93, 12, None, None, None, None, None, None], None),
    ([17, 1, 0x10, None, None, None, None, None, None, None], "10"),
    ([18, 1, 0x99, None, None, None, None, None, None, None], "99000000FF"),
    ([19, 2, 7, None, None, None, None, None, None, None], "07"),
    ([20, 1, 0x1B, None, None, 100, 5, None, None, None], "1B00640068"),
    ([21, 1, 0xF3, 0, 0, 0, 0, 0, 0, 0], "F30000000000FF"),
    ([22, 1, 0xFF, None, None, None, None, None, None, None], "FF"),
    ([23, 1, 3, 0, 0, 0xFFFF, 2, None, None, None], "030000FFFF0000"),
    ([24, 1, 13, None, None, None, None, None, None, None], "0D"),
    # the end address overflows 32 bits; negative lengths divide toward zero
    ([25, 1, 0x18, None, None, 0x10, 0x7FFFFFFF, None, None, None], "180010010E"),
    ([26, 1, 0x73, 11, 1, None, -2, None, None, None], "730B01000000FE"),
    ([27, 1, 0x63, 10, 1, None, -300, None, None, None], "630A00FFD4"),
]

_PARAGRAPHS: tuple[list[Spec], list[list[Cell]]] = (
    [
        (1, 4, False, "S19_BLOCK_PARAGRAPH_ID"),
        (1, 4, False, "BLOCK_ID"),
        (2, 2, True, "PT_COLUMN_ID"),
        (1, 4, True, "DATA_LONG"),
        (6, 16, True, "DATA_BINARY"),
    ],
    [
        [1, 2, 18, None, "0102030405060708090A0B0C"],
        [2, 3, 18, None, "0102030405"],
        [3, 4, 2, 0x12, None],
        [4, 4, 3, 0x34, None],
        [5, 4, 4, 0x0567, None],
        [6, 4, 5, 0x89, None],
        [7, 4, 18, None, "0A0B0C0D0E0F10111213"],
        [8, 6, 1, 0x12345, None],
        [9, 10, 8, 1, None],
        [10, 10, 9, 0x10002, None],
        [11, 10, 10, None, None],
        [12, 11, 11, 0x4321, None],
        [13, 11, 12, 0x1FF, None],
        [14, 12, 13, 10, None],
        [15, 12, 14, 20, None],
        [16, 12, 15, 30, None],
        [17, 12, 16, 40, None],
        [18, 19, 2, 1, None],
        [19, 19, 2, 2, None],
        [20, 19, 18, None, "01"],
        [21, 19, 18, None, "02"],
        [22, 20, 18, None, "0102030405060708090A0B"],
        [23, 20, 18, None, "0102030405060708090A0B"],
    ],
)

_TEMPLATES: dict[str, tuple[list[Spec], list[list[Cell]]]] = {
    "virtual_device": ([(1, 4, False, "VIRTUAL_DEVICE_ID")], [[1]]),
    "device_parameter": ([(1, 4, False, "DEVICE_ID")], [[1]]),
    "device_object": ([(1, 4, True, "DEVICE_ID")], [[1]]),
}

_ASSIGNMENTS: tuple[list[Spec], list[list[Cell]]] = (
    [(1, 4, False, "PROD2PROG_ID"), (1, 4, True, "PRODUCT_ID")],
    [[7, 70], [8, None]],
)
_MEDIA: tuple[list[Spec], list[list[Cell]]] = (
    [
        (1, 4, False, "PROD2PROG2MT_ID"),
        (1, 4, False, "PROD2PROG_ID"),
        (2, 2, False, "MEDIUM_TYPE_NUMBER"),
    ],
    [[1, 7, 1]],
)


def make_dump(
    version: str, tables: dict[str, tuple[list[Spec], list[list[Cell]]]]
) -> bytes:
    """An EX-IM dump of ``tables`` (values: int, text, hex for binary columns, None for NULL)."""
    lines = ["EX-IM", "N C:/data/db.vd_", f"V {version}", "H virtual_device"]
    for number, (name, (columns, rows)) in enumerate(tables.items(), start=3):
        lines += ["-------------------------------------", f"T {number} {name}"]
        for k, (kind, size, nullable, column) in enumerate(columns, start=1):
            lines.append(
                f"C{k} T{number} {kind} {size} {'Y' if nullable else 'N'} {column}"
            )
        for k, row in enumerate(rows, start=1):
            lines.append(f"R {k} T {number} {name}")
            lines += ["" if value is None else str(value) for value in row]
    lines += ["XXX", ""]
    return "\r\n".join(lines).encode("cp1252")


def sample_tables(
    version: str, *, records: bool = False
) -> dict[str, tuple[list[Spec], list[list[Cell]]]]:
    """The test database in the columns of schema ``version``; ``records`` adds the record
    columns of schema 6.2 (records NULL)."""
    columns = _BLOCK_COLUMNS + (_RECORD_COLUMNS if records else [])
    extra: list[Cell] = [None, None, 0] if records else []
    blocks = [
        [*values[:2], f"Step {values[0]}", *values[2:], *extra] for values, _ in _BLOCKS
    ]
    tables = {
        "application_program": _PROGRAMS,
        "s19_block": (columns, blocks),
        "s19_block_paragraph": _PARAGRAPHS,
        "product_to_program": _ASSIGNMENTS,
        **_TEMPLATES,
    }
    if version not in ("1.0", "2.7"):
        tables["product_to_program_to_mt"] = _MEDIA
    return tables


def _database(version: str, *, records: bool = False) -> Database:
    return Database.from_dump(
        Dump(make_dump(version, sample_tables(version, records=records)))
    )


def _records(db: Database) -> list[str | None]:
    blocks = db.tables["s19_block"]
    return [
        None if (r := row.bytes("RECORD")) is None else r.hex().upper()
        for row in blocks
    ]


def _expected_records() -> list[str | None]:
    return [None if r is None else r.ljust(32, "0") for _, r in _BLOCKS]


@pytest.mark.parametrize(
    ("version", "records"),
    [
        ("1.0", False),
        ("2.7", False),
        ("3.19", False),
        ("4.9", False),
        ("5.10", False),
        ("6.2", True),
    ],
)
def test_upgrade_builds_the_load_records(version: str, records: bool) -> None:
    db = _database(version, records=records)
    upgrade(db)
    assert db.version == SCHEMA_VERSION
    blocks = db.tables["s19_block"]
    assert blocks.columns[-3:] == ["RECORD", "MERGE_ID", "PROC_MASK"]
    assert _records(db) == _expected_records()
    assert all(row["MERGE_ID"] is None and row["PROC_MASK"] == 0 for row in blocks)


def test_schema_6_3_keeps_existing_records() -> None:
    tables = sample_tables("6.3", records=True)
    blocks = tables["s19_block"][1]
    blocks[0][-3] = "00112233445566778899AABBCCDDEEFF"
    db = Database.from_dump(Dump(make_dump("6.3", tables)))
    upgrade(db)
    expected = _expected_records()
    expected[0] = "00112233445566778899AABBCCDDEEFF"
    assert _records(db) == expected


def test_media_table_for_the_oldest_schemas() -> None:
    db = _database("1.0")
    upgrade(db)
    media = db.tables["product_to_program_to_mt"]
    assert media.name == "PRODUCT_TO_PROGRAM_TO_MT"
    assert media.columns == ["PROD2PROG_ID", "MEDIUM_TYPE_NUMBER"]
    assert media.rows == [[7, 0], [8, 0]]


def test_media_table_is_kept_from_schema_3_19() -> None:
    db = _database("3.19")
    upgrade(db)
    assert db.tables["product_to_program_to_mt"].rows == [[1, 7, 1]]


def test_media_table_twice_is_an_error() -> None:
    tables = sample_tables("2.7")
    tables["product_to_program_to_mt"] = _MEDIA
    db = Database.from_dump(Dump(make_dump("2.7", tables)))
    with pytest.raises(ArchiveError):
        upgrade(db)


def test_device_templates_are_renamed() -> None:
    db = _database("6.2", records=True)
    upgrade(db)
    assert "device_parameter" not in db.tables
    assert "device_object" not in db.tables
    assert db.tables["device_template_parameter"].name == "device_template_parameter"
    assert db.tables["device_template_object"].rows == [[1]]


def test_project_tables_are_not_renamed() -> None:
    tables = sample_tables("6.2", records=True)
    del tables["virtual_device"]
    tables["device"] = ([(1, 4, False, "DEVICE_ID")], [[1]])
    db = Database.from_dump(Dump(make_dump("6.2", tables)))
    upgrade(db)
    assert "device_parameter" in db.tables
    assert "device_object" in db.tables


def test_templates_without_any_device_table_are_not_renamed() -> None:
    tables = sample_tables("6.2", records=True)
    del tables["virtual_device"]
    db = Database.from_dump(Dump(make_dump("6.2", tables)))
    upgrade(db)
    assert "device_parameter" in db.tables
    assert "device_template_parameter" not in db.tables


def test_only_existing_templates_are_renamed() -> None:
    tables = sample_tables("6.2", records=True)
    del tables["device_object"]
    db = Database.from_dump(Dump(make_dump("6.2", tables)))
    upgrade(db)
    assert "device_template_parameter" in db.tables
    assert "device_template_object" not in db.tables


def test_no_media_table_without_assignments() -> None:
    tables = sample_tables("1.0")
    del tables["product_to_program"]
    db = Database.from_dump(Dump(make_dump("1.0", tables)))
    upgrade(db)
    assert "product_to_program_to_mt" not in db.tables


def test_devices_and_device_templates_are_an_error() -> None:
    tables = sample_tables("6.2", records=True)
    tables["device"] = ([(1, 4, False, "DEVICE_ID")], [[1]])
    with pytest.raises(ArchiveError):
        upgrade(Database.from_dump(Dump(make_dump("6.2", tables))))


def test_unknown_schema_is_an_error() -> None:
    with pytest.raises(ArchiveError, match=r"5.9"):
        upgrade(_database("5.9"))


def test_without_load_procedure() -> None:
    tables = sample_tables("4.9")
    del tables["s19_block"]
    db = Database.from_dump(Dump(make_dump("4.9", tables)))
    upgrade(db)
    assert db.version == SCHEMA_VERSION
    assert "s19_block" not in db.tables


def test_without_paragraphs() -> None:
    tables = sample_tables("4.9")
    del tables["s19_block_paragraph"]
    db = Database.from_dump(Dump(make_dump("4.9", tables)))
    upgrade(db)
    records = _records(db)
    # without paragraphs, numbers read as 0 and data bytes are missing
    assert records[1] == "1A0100011F".ljust(32, "0")
    assert records[3] == "05".ljust(32, "0")
    assert records[9] == "3303".ljust(32, "0")


def _program_names(caplog: pytest.LogCaptureFixture) -> set[str]:
    """The application programs named in the "Missing S19_BLOCK_PARAGRAPH" messages."""
    return {
        r.getMessage().rsplit(" ", 1)[1]
        for r in caplog.records
        if r.getMessage().startswith("Missing S19_BLOCK_PARAGRAPH")
    }


@pytest.mark.parametrize(
    "case", ["unknown program", "version no number", "no original"]
)
def test_an_application_id_that_cannot_be_built_only_changes_the_message(
    case: str, caplog: pytest.LogCaptureFixture
) -> None:
    # The id only names the program in the log message: the upgrade goes on without it.
    tables = sample_tables("4.9")
    if case == "unknown program":
        tables["application_program"] = (_PROGRAMS[0], [_PROGRAMS[1][0]])
    elif case == "version no number":
        tables["application_program"] = (
            _PROGRAMS[0],
            [[1, 250, 0x1234, "1.0", None], _PROGRAMS[1][1]],
        )
    else:
        tables["application_program"] = (
            _PROGRAMS[0][:4],
            [row[:4] for row in _PROGRAMS[1]],
        )
    db = Database.from_dump(Dump(make_dump("4.9", tables)))
    with caplog.at_level("INFO", logger="xknxeditor.prod.vd.upgrade"):
        upgrade(db)
    assert db.version == SCHEMA_VERSION
    assert "?" in _program_names(caplog)


def test_missing_paragraph_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    db = _database("4.9")
    with caplog.at_level("DEBUG", logger="xknxeditor.prod.vd.upgrade"):
        upgrade(db)
    messages = [r.getMessage() for r in caplog.records]
    assert (
        "Missing S19_BLOCK_PARAGRAPH of type 2 for S19_BLOCK 5 in ApplicationProgram "
        "M-0008_A-0A0B-03-0000-O0083" in messages
    )
    assert (
        "Missing S19_BLOCK_PARAGRAPH of type 1 for S19_BLOCK 24 in ApplicationProgram "
        "M-00FA_A-1234-11-0000" in messages
    )
    assert "S19_BLOCK_PARAGRAPH of type 2 for S19_BLOCK 19 is not unique" in messages
    assert "S19_BLOCK_PARAGRAPH of type 18 for S19_BLOCK 20 is not unique" in messages
    assert (
        "Found S19_BLOCK_PARAGRAPH with BLOCK_ID=19 and PT_COLUMN_ID=18 and DATA_LONG="
        in messages
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("CONTROL_CODE", 256),
        ("CONTROL_CODE", -1),
        ("SEGMENT_ADDRESS", 0x10000),
        ("MEMORY_TYPE", 300),
    ],
)
def test_values_out_of_range_are_an_error(column: str, value: int) -> None:
    db = _database("4.9")
    db.tables["s19_block"].rows[6][db.tables["s19_block"].col(column)] = value
    with pytest.raises(ArchiveError, match="out of range"):
        upgrade(db)


def test_control_code_null_is_an_error() -> None:
    db = _database("4.9")
    db.tables["s19_block"].rows[0][db.tables["s19_block"].col("CONTROL_CODE")] = None
    with pytest.raises(ArchiveError, match="CONTROL_CODE"):
        upgrade(db)


def test_load_procedure_without_record_column_is_an_error() -> None:
    db = _database("6.3")
    with pytest.raises(ArchiveError, match="no records"):
        upgrade(db)


# Records the converter built for the samples (BLOCK_ID -> RECORD).
_SAMPLE_RECORDS = {
    "EIBADUMY.VD1": {
        314832: "14000000000000000000000000000000",
        314837: "130000400047D4FF0380000000000000",
        314838: "13020040000000000000000000000000",
        314841: "23000047D54FA7FF0380000000000000",
    },
}


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("N148_12_700201.vd5", 0x60003),
        ("1152_KNX_Netzteil.vd4", 0x60002),
        ("EIBADUMY.VD1", 0x30013),
    ],
)
def test_samples(name: str, version: int) -> None:
    path = _SAMPLES / name
    if not path.exists():
        pytest.skip(f"{name} is missing")
    db = Database.from_dump(read_dump(path))
    assert db.version == version
    upgrade(db)
    assert db.version == SCHEMA_VERSION
    blocks = db.tables["s19_block"]
    assert blocks.columns[-3:] == ["RECORD", "MERGE_ID", "PROC_MASK"]
    records = {row["BLOCK_ID"]: row.bytes("RECORD") for row in blocks}
    assert all(record is not None for record in records.values())
    for block, record in _SAMPLE_RECORDS.get(name, {}).items():
        assert records[block] == bytes.fromhex(record)
    assert "device_template_parameter" in db.tables
    assert "device_parameter" not in db.tables

"""Texts in the code pages of their languages, and the mutable tables of the database."""

from __future__ import annotations

import codecs

import pytest

from xknxeditor.prod.vd.convert import ConversionError
from xknxeditor.prod.vd.database import Database
from xknxeditor.prod.vd.text import code_page, from_db, two_letter

from .test_coverage_helpers import Tables, base, program, run, text, translations, xml


def _ansi(value: str, cp: int) -> str:
    """A text as the dump holds it: the bytes of its code page read as Windows-1252."""
    return value.encode(f"cp{cp}").decode("cp1252", errors="replace")


def languages_tables() -> Tables:
    t = base()
    t["text_attribute"] = [
        text(1, 1032, 30, 10, _ansi("Φως", 1253)),
        text(2, 1041, 30, 10, _ansi("光", 932)),
        text(3, 1054, 30, 10, _ansi("แสง", 874)),
        text(4, 1081, 30, 10, "Prakash"),
        text(5, 1049, 30, 10, _ansi("Свет", 1251)),
        text(6, 9999, 30, 10, "unknown language"),
        text(7, 1031, 30, 10, "Licht\x01an"),
        text(8, 2055, 30, 10, "Liecht"),
        text(9, 1031, 99, 10, "unknown column"),
    ]
    return t


def undefined_byte_tables() -> Tables:
    t = languages_tables()
    t["text_attribute"].append(text(10, 1032, 31, 10, "aªb"))
    return t


def test_texts_in_language_code_pages() -> None:
    files, _warnings, _errors = run(languages_tables())
    root = xml(files, "M-0083/Catalog.xml")
    names = {
        language: translations(root, language).get("M-0083_CS-L", {}).get("Name")
        for language in ("el-GR", "ja-JP", "th-TH", "hi-IN", "ru-RU", "de-DE", "de-CH")
    }
    assert names == {
        "el-GR": "Φως",
        "ja-JP": "光",
        "th-TH": "แสง",
        # a language without an ANSI code page keeps Windows-1252
        "hi-IN": "Prakash",
        "ru-RU": "Свет",
        # characters XML cannot hold become spaces
        "de-DE": "Licht an",
        "de-CH": "Liecht",
    }


def test_text_helpers() -> None:
    assert code_page("xx-YY") == 1252
    assert code_page("hi-IN") == 1252
    assert two_letter("de-CH") == "de"
    assert from_db("de-DE", "") == ""
    # characters Windows-1252 cannot hold are kept as their byte, else "?"
    assert from_db("ru-RU", chr(0x81)) == chr(0x403)
    assert from_db("ru-RU", "一") == "?"
    assert codecs.decode(b"\x81", "cp1252", errors="vd-lenient") == "\x81"
    assert "一".encode("cp1252", errors="vd-lenient") == b"?"


@pytest.mark.xfail(
    reason="the converter decodes bytes a code page leaves undefined to private use "
    "characters (Windows-1253 0xAA is U+F8F9), not to the byte's code point",
    strict=True,
)
def test_undefined_byte_of_a_code_page() -> None:
    assert from_db("el-GR", "aªb") == "ab"


def test_database_tables() -> None:
    db = Database(0x60003)
    table = db.add_table("Things", ["ID", "NAME"])
    assert db.table("THINGS") is table
    assert db.require("missing").rows == []
    row = table.append({"id": 1, "name": "one"})
    table.append({"ID": 2, "NAME": None})
    assert len(table) == 2
    assert table.add_column("id") == 0
    assert table.add_column("extra", "x") == 2
    assert [r["EXTRA"] for r in table] == ["x", "x"]
    table.rename_column("extra", "more")
    assert table.has("MORE")
    assert not table.has("extra")
    table.drop_column("MORE")
    assert table.columns == ["ID", "NAME"]
    assert table.col("name") == 1
    assert set(table.index_by("ID")) == {1, 2}
    assert list(table.group_by("NAME")) == ["one", None]
    row["NAME"] = "uno"
    assert row.has("name")
    assert row.str("NAME") == "uno"
    assert row.str("ID") == "1"
    assert row.int("ID") == 1
    assert row["UNKNOWN"] is None
    with pytest.raises(KeyError):
        table.col("unknown")
    text_row = db.add_table("t", ["V", "B"]).append({"V": "42", "B": b"\x01"})
    assert text_row.int("V") == 42
    assert text_row.bytes("B") == b"\x01"
    assert text_row.bytes("missing") is None
    with pytest.raises(TypeError):
        text_row.int("B")
    with pytest.raises(TypeError):
        text_row.str("B")
    with pytest.raises(TypeError):
        text_row.bytes("V")
    db.drop_table("t")
    db.drop_table("t")
    assert db.table("t") is None


def _report(tables: Tables) -> tuple[list[str], list[str]]:
    files, _warnings, errors = run(tables)
    return sorted(files), errors


def test_program_of_an_unknown_manufacturer() -> None:
    t = base()
    t["application_program"].append(
        program(program_id=41, device_type=0x2001, manufacturer_id=99)
    )
    names, errors = _report(t)
    assert "M-0063/M-0063_A-2001-01-0000.xml" not in names
    assert "UnknownManufacturerId: M-0063" in errors


def test_failing_program_is_reported_and_skipped() -> None:
    t = base()
    t["application_program"].append(program(program_id=41, device_type=0x2001))
    t["parameter"] = [
        {
            "PROGRAM_ID": 41,
            "PARAMETER_ID": 200,
            "PARAMETER_NUMBER": 0,
            "PARAMETER_NAME": "Bad",
            "PARAMETER_HIGH_ACCESS": 7,
            "ADDRESS_SPACE": 1,
        }
    ]
    names, errors = _report(t)
    assert "M-0083/M-0083_A-2001-01-0000.xml" not in names
    assert "M-0083/M-0083_A-1234-01-0000.xml" in names
    assert errors[0].startswith(
        "ApplicationProgramConversionFailed: Unable to convert ApplicationProgram with Id = 41"
    )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("PROGRAM_TYPE", 3, "ProgramType"),
        ("MASK_ID", 59, "MaskVersion"),
    ],
)
def test_conversion_errors_stop_the_conversion(
    column: str, value: int, message: str
) -> None:
    t = base()
    t["application_program"][0][column] = value
    t["mask"].append({"MASK_ID": 59, "MASK_VERSION": 0x4711})
    with pytest.raises(ConversionError, match=message):
        run(t)

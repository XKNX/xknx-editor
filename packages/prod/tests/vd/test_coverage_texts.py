"""Texts in the code pages of their languages, and the mutable tables of the database."""

from __future__ import annotations

import codecs
import hashlib

import pytest

from xknxeditor.prod.vd import codepages
from xknxeditor.prod.vd.convert import ConversionError
from xknxeditor.prod.vd.database import Database
from xknxeditor.prod.vd.text import _decode, code_page, from_db, two_letter

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


def test_undefined_byte_of_a_code_page() -> None:
    # bytes a code page leaves undefined decode as there: private use or C1 characters
    assert from_db("el-GR", "a\xaab") == "a\uf8f9b"
    assert from_db("el-GR", "\x81") == "\x81"
    assert from_db("th-TH", "\xdb") == "\uf8c1"
    assert from_db("he-IL", "\xca") == "\u05ba"
    assert from_db("lt-LT", "\xa1") == "\uf8fc"
    files, _warnings, _errors = run(undefined_byte_tables())
    root = xml(files, "M-0083/Catalog.xml")
    assert translations(root, "el-GR")["M-0083_CS-L"]["VisibleDescription"] == (
        "a\uf8f9b"
    )


@pytest.mark.parametrize(
    ("language", "text", "expected"),
    [
        # a lead byte takes the next byte, also where the pair is undefined (one character)
        ("ja-JP", "\x81\x0aA", "\u30fbA"),
        ("ja-JP", _ansi("光", 932) + "\x81", "光\u30fb"),
        # a byte that is no lead byte stands alone
        ("ja-JP", "\xa0\xfd", "\uf8f0\uf8f1"),
        ("zh-CN", "€\x81", "€?"),
        ("zh-CN", "\xaa\xa1", "\ue000"),
        ("ko-KR", "\xc9\xa1", "\ue000"),
        ("zh-TW", "\x81\x40\x80", "\ueeb8\x80"),
    ],
)
def test_double_byte_code_pages(language: str, text: str, expected: str) -> None:
    assert from_db(language, text) == expected


# SHA-256 of the UTF-16LE decodings of every byte, then every pair of each lead byte, as the
# converter's runtime decodes them
_DIGESTS = {
    874: "32f78caea158a0dd47a634082c15619c1f57303152fc76dfff287575d88ee8c8",
    932: "92467b13c912474d67634cd22e8bceac5a4e5ad7dc9071f399407720a7ea6810",
    936: "e759cadc3621cc0e5cbd29a673d4a9197c63ed5ba69e3b1bcb5b2e04de169b3d",
    949: "85697587f80acd919ccee3f6eb1f5e4ddb071bdff8f5e46c8a2da83c65c5cf17",
    950: "498db6a5323736499cb14b9517b15874c1323ecc4392b3aab038b46d4392f658",
    1250: "083e8699d4ea3429b02b429a7f25b809849cf81e2116ef4ed0d4b96eb34ef90a",
    1251: "b1ce72e8de9ab26261094800e12f0eb3cdd2c35a23667aefad6669164a603bf3",
    1252: "0048a1d8df12622c52e110e4bd0b4da053b1e0be5ba70d92c0233763d1be53f9",
    1253: "097593b13c9f6206acad41030446caf6f292c9b734a5ab7a4975c21574bfc90b",
    1254: "52c57e6a29dd1c1bbe58a19de2f94374440080b833a7b2f0ce61164a1e7c851a",
    1255: "a11dfae23379da61e918f23bbf66f896ac4ef2b2debf032f5e0971085cbe2a90",
    1256: "544c3592214b8762df16607b99334d4d53df3148f5b79ab787992271f78ba593",
    1257: "6c47fd11e87361031ba004e3f3ef5056be0bbf97df14f6e3b21377bd30f41d6e",
    1258: "525ad65d40749cdcd9847fdc09d2d10b8d042deb594df4aa92ad3a33a8c14319",
}


@pytest.mark.parametrize("cp", sorted(_DIGESTS))
def test_code_page_tables_decode_every_byte(cp: int) -> None:
    parts = [_decode(bytes([b]), cp) for b in range(256)]
    if cp in codepages.LEAD_BYTES:
        for low, high in codepages.LEAD_BYTES[cp]:
            for lead in range(low, high + 1):
                parts.extend(_decode(bytes([lead, t]), cp) for t in range(256))
    digest = hashlib.sha256("".join(parts).encode("utf-16-le")).hexdigest()
    assert digest == _DIGESTS[cp]


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
        program(program_id=41, device_type=0x2001, manufacturer_id=0x41)
    )
    names, errors = _report(t)
    assert "M-0041/M-0041_A-2001-01-0000.xml" not in names
    assert "UnknownManufacturerId: M-0041" in errors


@pytest.mark.parametrize(
    "columns", [{"MANUFACTURER_ID": 99}, {"ORIGINAL_MANUFACTURER_ID": 99}]
)
def test_program_of_a_manufacturer_missing_in_the_master_data(
    columns: dict[str, int],
) -> None:
    t = base()
    t["application_program"].append(
        program(program_id=41, device_type=0x2001, **columns)
    )
    with pytest.raises(ConversionError, match="manufacturer id = M-0063"):
        _report(t)


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

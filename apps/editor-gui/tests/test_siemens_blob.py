"""Regression tests for the Siemens N141 BLOB_PARA reader/writer/download pipeline.

The fixture ``siemens_n141_blob_para.dat`` is a real ``BLOB_PARA`` entry extracted from a
Siemens DALI gateway in an exported .knxproj (raw-deflate over UTF-16-LE XML in the
``N141_21_Parameters`` namespace). Until now only the bus scan was covered, so the reader,
the ``apply_edit``/``add_item``/``remove_item`` writer path, and the download property builder
had no tests - which is how a batch of type errors went unnoticed. These lock the functional
contract against real data.
"""

from __future__ import annotations

from pathlib import Path

from editor_gui.plugin_blob_readers.siemens_dali import reader, writer
from editor_gui.plugin_blob_readers.siemens_dali.download import compile_configuration

_BLOB = Path(__file__).parent / "fixtures" / "siemens_n141_blob_para.dat"


def _blob() -> bytes:
    return _BLOB.read_bytes()


def _all_rows(sections: list) -> list:  # type: ignore[type-arg]
    return [p for s in sections for p in s.parameters]


def test_reader_parses_real_blob() -> None:
    sections = reader.read(_blob())
    assert sections, "reader produced no sections"
    rows = _all_rows(sections)
    assert rows, "reader produced no rows"
    # Every reader row carries a stable siemens_blob locator the writer can target.
    assert all(p.ref_id.startswith("siemens_blob:") for p in rows if p.ref_id)


def test_apply_edit_same_value_is_idempotent() -> None:
    data = _blob()
    rows = _all_rows(reader.read(data))
    row = next(p for p in rows if p.ref_id.startswith("siemens_blob:") and p.value)
    out = writer.apply_edit(data, row.ref_id, row.value)
    # Re-read preserves the value (and the blob stays valid).
    again = {p.ref_id: p.value for s in reader.read(out) for p in s.parameters}
    assert again[row.ref_id] == row.value


def test_apply_edit_changes_value_and_blob() -> None:
    data = _blob()
    rows = _all_rows(reader.read(data))
    target = next(
        p
        for p in rows
        if p.ref_id.startswith("siemens_blob:") and p.value and p.value.isdigit()
    )
    new_value = str(int(target.value) + 1)
    out = writer.apply_edit(data, target.ref_id, new_value)
    assert out != data
    got = {p.ref_id: p.value for s in reader.read(out) for p in s.parameters}
    assert got[target.ref_id] == new_value


def test_add_and_remove_collection_item_round_trip() -> None:
    data = _blob()
    sections = reader.read(data)
    binding = next(s.collection for s in sections if s.collection is not None)
    assert binding.item_types, "collection offers no item types"
    type_id = binding.item_types[0][0]  # (id, label)

    added = writer.add_item(data, binding, type_id, "TestItem")
    assert added != data
    # The mutated blob is still parseable.
    assert reader.read(added)

    # Removing the freshly-added slot (nr 1 on a previously empty SensorList) stays valid.
    removed = writer.remove_item(added, binding, "1")
    assert reader.read(removed)


def test_download_builds_complete_property_map() -> None:
    config = compile_configuration(_blob(), "M-0001_A-9837-11-449E")
    assert len(config.writes) == 350
    assert {w.odx for w in config.writes} == {
        0,
        11,
        14,
        15,
        17,
        18,
        20,
        22,
        24,
        26,
        28,
        30,
        32,
        33,
        34,
        42,
    }

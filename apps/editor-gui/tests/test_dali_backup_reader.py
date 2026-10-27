"""Regression tests for the DALI backup-blob viewer sections.

Fixtures are real backup blobs extracted from exported projects: ``dali_gc16_backup.dat`` from an
MDT gateway (``type="gc16"``, coloured scene + ColourHSV template), and ``dali_e64pro_backup.dat``
from an IPAS DaliControl e64 Pro (``type="e64pro"``, with groups, ECGs, sensor/button/input
configs and colour effects). The reader surfaces the gateway's full DCA state - all of which lives
only in this blob, not in the KNX parameters - so it is findable in the editor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from editor_gui.plugin_blob_readers.dali_backup import read

_FIXTURES = Path(__file__).parent / "fixtures"


def _sections(name: str) -> dict[str, list[tuple[str, str]]]:
    secs = read((_FIXTURES / name).read_bytes())
    return {s.section: [(p.label, p.value) for p in s.parameters] for s in secs}


def test_gc16_blob_surfaces_scenes_ecgs_and_templates() -> None:
    secs = _sections("dali_gc16_backup.dat")
    assert {"DALI ECGs", "DALI Scenes", "DALI Time Control (Templates)"} <= set(secs)
    # Coloured scene items expose their colour on its own labelled row (previously invisible).
    scene_labels = " ".join(label for label, _ in secs["DALI Scenes"])
    assert "color" in scene_labels
    # Template time-control commands expose function, time and the weekday schedule, each on a
    # readable row (not a cryptic compound dump).
    tpl_labels = " ".join(label for label, _ in secs["DALI Time Control (Templates)"])
    assert "func" in tpl_labels
    assert "time" in tpl_labels and "weekdays" in tpl_labels
    assert "ECG" in " ".join(label for label, _ in secs["DALI ECGs"])


def test_e64pro_blob_surfaces_every_container() -> None:
    secs = _sections("dali_e64pro_backup.dat")
    for expected in (
        "DALI Groups",
        "DALI ECGs",
        "DALI Motion Detectors",
        "DALI Push-Buttons",
        "DALI General Inputs",
        "DALI Scenes",
        "DALI Effects",
        "DALI Time Control (Templates)",
    ):
        assert expected in secs, expected
    # Effect steps carry their per-step fade time and delay on their own rows.
    effect_labels = " ".join(label for label, _ in secs["DALI Effects"])
    assert "fadetime" in effect_labels and "delay" in effect_labels


def test_group_name_surfaced() -> None:
    secs = _sections("dali_e64pro_backup.dat")
    group_labels = " ".join(label for label, _ in secs["DALI Groups"])
    assert "'" in group_labels  # named items render a quoted description in the head


def test_writable_rows_are_names_enums_bools_or_editable_scalars() -> None:
    # A row is writable only when it is a known-editable field: a select box (choices), the
    # item name, or a plain config scalar. Physical / derived fields stay read-only.
    from editor_gui.plugin_blob_readers.dali_backup import _EDITABLE_SCALARS

    for s in read(
        (_FIXTURES / "dali_gc16_backup.dat").read_bytes(), name="DaliGC16-Backup-Store"
    ):
        for p in s.parameters:
            if not p.writable:
                continue
            attr = p.ref_id.rsplit(":", 1)[-1]
            assert p.choices or attr in _EDITABLE_SCALARS, p.label
            # Every writable row carries a routable dali_backup ref.
            assert p.ref_id.startswith("dali_backup:")


def test_nested_scene_value_is_editable_and_round_trips() -> None:
    from editor_gui.plugin_blob_readers.dali_backup import apply_edit, read

    data = (_FIXTURES / "dali_gc16_backup.dat").read_bytes()
    row = next(
        p
        for s in read(data, name="DaliGC16-Backup-Store")
        for p in s.parameters
        if ":leaf:" in p.ref_id and p.ref_id.endswith(":value") and p.writable
    )
    assert apply_edit(data, row.ref_id, row.value) == data  # no-op byte-exact
    out = apply_edit(data, row.ref_id, "200")
    again = next(
        p
        for s in read(out, name="DaliGC16-Backup-Store")
        for p in s.parameters
        if p.ref_id == row.ref_id
    )
    assert again.value == "200"


def test_boolean_field_is_a_true_false_select_box() -> None:
    rows = [
        p
        for s in read(
            (_FIXTURES / "dali_gc16_backup.dat").read_bytes(),
            name="DaliGC16-Backup-Store",
        )
        for p in s.parameters
        if p.ref_id.endswith(":isOverwritable")
    ]
    assert rows
    assert rows[0].writable
    assert dict(rows[0].choices) == {"True": "True", "False": "False"}


def test_non_dali_blob_returns_empty() -> None:
    assert read((_FIXTURES / "siemens_n141_blob_para.dat").read_bytes()) == []
    assert read(b"\x00\x01\x02not xml") == []


def test_blob_only_enum_is_editable_select_box() -> None:
    # Template command "func" is blob-only (no KNX-parameter mirror) -> an editable dropdown.
    data = (_FIXTURES / "dali_gc16_backup.dat").read_bytes()
    rows = [
        p
        for s in read(data, name="DaliGC16-Backup-Store")
        for p in s.parameters
        if p.ref_id.startswith("dali_backup:") and p.ref_id.endswith(":func")
    ]
    assert rows, "no editable func dropdown surfaced"
    assert rows[0].writable
    assert ("SetValue", "SetValue") in rows[0].choices


def test_param_mirrored_enum_is_readonly_label() -> None:
    # ECG "type" and template "mode" also exist as KNX parameters; editing only the blob copy
    # would desync, so they are read-only and show the humanised label (not the raw integer).
    secs = {
        s.section: s.parameters
        for s in read(
            (_FIXTURES / "dali_gc16_backup.dat").read_bytes(),
            name="DaliGC16-Backup-Store",
        )
    }
    ecg_type = next(p for p in secs["DALI ECGs"] if p.label.endswith(": type"))
    assert not ecg_type.writable
    assert ecg_type.value == "LED module"  # raw 6 resolved to its label
    mode = next(
        p for p in secs["DALI Time Control (Templates)"] if p.label.endswith(": mode")
    )
    assert not mode.writable
    assert mode.value in ("Enabled", "Disabled")


def test_apply_edit_changes_value_and_is_byte_exact_on_noop() -> None:
    from editor_gui.plugin_blob_readers.dali_backup import apply_edit, read

    data = (_FIXTURES / "dali_gc16_backup.dat").read_bytes()
    # Edit a blob-only scalar (scene KNX number).
    row = next(
        p
        for s in read(data, name="DaliGC16-Backup-Store")
        for p in s.parameters
        if p.ref_id.startswith("dali_backup:") and p.ref_id.endswith(":knxScene")
    )
    assert apply_edit(data, row.ref_id, row.value) == data  # no-op byte-exact
    out = apply_edit(data, row.ref_id, "42")
    assert out != data
    again = next(
        p
        for s in read(out, name="DaliGC16-Backup-Store")
        for p in s.parameters
        if p.ref_id == row.ref_id
    )
    assert again.value == "42"


def test_apply_edit_rejects_foreign_or_missing_refs() -> None:
    from editor_gui.plugin_blob_readers.dali_backup import apply_edit

    data = (_FIXTURES / "dali_gc16_backup.dat").read_bytes()
    with pytest.raises(ValueError):
        apply_edit(data, "siemens_blob:Foo[0]", "1")
    with pytest.raises(ValueError):
        apply_edit(data, "dali_backup:DaliGC16-Backup-Store:ecgs:999:type", "1")


def test_second_channel_sections_are_disambiguated() -> None:
    from editor_gui.plugin_blob_readers.dali_backup import read

    data = (_FIXTURES / "dali_gc16_backup.dat").read_bytes()
    base = {s.section for s in read(data, name="DaliGC16-Backup-Store")}
    ch2 = {s.section for s in read(data, name="DaliGC16-Backup-Store.1")}
    assert any("(Channel 2)" in s for s in ch2)
    assert not any("(Channel" in s for s in base)

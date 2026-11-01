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
from editor_gui.plugin_blob_readers.dali_backup import PluginParameter, read

_FIXTURES = Path(__file__).parent / "fixtures"


def _sections(name: str, blob: str = "") -> dict[str, list[PluginParameter]]:
    return {
        s.section: list(s.parameters)
        for s in read((_FIXTURES / name).read_bytes(), name=blob)
    }


def test_gc16_blob_surfaces_scenes_ecgs_and_templates() -> None:
    secs = _sections("dali_gc16_backup.dat")
    assert {"DALI ECGs", "DALI Scenes", "DALI Time Control (Templates)"} <= set(secs)
    # Coloured scene items expose their colour on its own labelled row (previously invisible).
    scene_labels = " ".join(p.label for p in secs["DALI Scenes"])
    assert "Colour" in scene_labels
    # Template time-control commands expose function, time and the weekday schedule, each on a
    # readable row (not a cryptic compound dump).
    tpl_labels = " ".join(p.label for p in secs["DALI Time Control (Templates)"])
    assert "Function" in tpl_labels
    assert "Time" in tpl_labels and "Weekdays" in tpl_labels
    # The ECG item it belongs to names the item in its group (tree node), not the field label.
    assert any("ECG" in p.group for p in secs["DALI ECGs"])


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
    # Effect steps carry their per-step fade time and delay on their own readable rows.
    effect_labels = " ".join(p.label for p in secs["DALI Effects"])
    assert "Fade time" in effect_labels and "Delay" in effect_labels


def test_rows_carry_a_group_so_the_panel_can_build_a_tree() -> None:
    # Every blob row belongs to an item group (``[N] Scene 'name'`` etc.) so the panel nests the
    # long list under one collapsible node per item instead of one endless flat table.
    secs = _sections("dali_e64pro_backup.dat")
    for section in ("DALI Groups", "DALI Scenes"):
        rows = secs[section]
        assert rows
        assert all(p.group for p in rows), section
        # A section has several groups (several items), otherwise a tree buys nothing.
        assert len({p.group for p in rows}) > 1, section


def test_group_name_surfaced_in_group_head() -> None:
    secs = _sections("dali_e64pro_backup.dat")
    heads = " ".join({p.group for p in secs["DALI Groups"]})
    assert "'" in heads  # named items render a quoted description in the tree-node head


def test_group_colour_fields_are_readable_readonly_labels() -> None:
    # ``cmode`` / ``ctype`` mirror the per-channel DCA colour parameters, so they stay read-only
    # but are resolved from the cryptic integer to a label ("Colour type" = "Temperature + HSV").
    secs = _sections("dali_e64pro_backup.dat")
    groups = secs["DALI Groups"]
    ctype = [p for p in groups if p.label == "Colour type"]
    assert ctype, "no group colour-type row surfaced"
    assert all(not p.writable for p in ctype)
    # The raw integers in the fixture resolve to their EtsColorType names, never a bare number.
    assert all(not p.value.isdigit() for p in ctype), [p.value for p in ctype]
    cmode = [p for p in groups if p.label == "Colour mode"]
    assert cmode and all(p.value in ("Normal", "Master", "Slave") for p in cmode)


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


def test_scene_colour_value_is_editable_but_type_is_a_readonly_label() -> None:
    # The scene colour VALUE lives only in the blob, so it is editable (a same-space tweak, e.g.
    # the Kelvin number, round-trips verbatim). The colour-TYPE (``color-type``) is read-only: it
    # is shown as a humanised label, but changing the colour space alone would leave the value and
    # the ``setting`` flags inconsistent, so switching it needs a dedicated picker, not a raw
    # dropdown. ``color`` keeps the ``dali_backup:`` ref so an edit routes back to the blob.
    rows = [
        p
        for s in read(
            (_FIXTURES / "dali_gc16_backup.dat").read_bytes(),
            name="DaliGC16-Backup-Store",
        )
        for p in s.parameters
    ]
    colour_types = [p for p in rows if p.ref_id.endswith(":color-type")]
    assert colour_types, "no scene colour-type row surfaced"
    assert all(not p.writable and not p.value.isdigit() for p in colour_types)
    colours = [p for p in rows if p.ref_id.endswith(":color")]
    assert colours
    assert all(p.writable and p.ref_id.startswith("dali_backup:") for p in colours)


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
    secs = _sections("dali_gc16_backup.dat", "DaliGC16-Backup-Store")
    ecg_type = next(p for p in secs["DALI ECGs"] if p.label == "Type")
    assert not ecg_type.writable
    assert ecg_type.value == "LED module"  # raw 6 resolved to its label
    mode = next(p for p in secs["DALI Time Control (Templates)"] if p.label == "Mode")
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

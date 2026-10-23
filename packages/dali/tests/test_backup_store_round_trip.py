"""Byte-exact round-trip test for the MDT ``DaliGC16-Backup-Store`` serializer.

Fixture ``mdt_dali_backup_store.dat`` was extracted from a real .knxproj export (project
password "test", decrypted via :func:`xknxeditor.proj.core.knxproj_protect.ets6_zip_password`).
It is the on-disk BinaryData entry MDT's DCA writes for device DI-1 (channel 0) of a project
that touched one scene (knxScene=1) and one template (mode=Enabled, one SetValue command).

The round-trip guarantees that we can read a store MDT wrote, hand it back to our writer, and
get the exact same bytes - which is the only safe way to persist DCA-side edits into a
re-exported .knxproj.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.dali.backup_store import (
    dump_backup_store,
    load_backup_store,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_FIXTURE = _FIXTURES / "mdt_dali_backup_store.dat"

# Every real MDT blob we have extracted from a .knxproj export. They exercise, in turn: a plain
# single-channel config; a second-channel blob (".1" BinaryData name, a 2x64 gateway); a channel
# with coloured scene items (color-type/color) and a ColourHSV template; and a simple template.
_REAL_BLOBS = [
    "mdt_dali_backup_store.dat",
    "mdt_dali_backup_ch0_colour.dat",
    "mdt_dali_backup_ch1.dat",
    "mdt_dali_backup_simple.dat",
]


@pytest.mark.parametrize("name", _REAL_BLOBS)
def test_real_blob_round_trip_is_byte_exact(name: str) -> None:
    """load -> dump reproduces every real exported blob byte-for-byte."""
    original = (_FIXTURES / name).read_bytes()
    rebuilt = dump_backup_store(load_backup_store(original))
    assert rebuilt == original


def test_multichannel_and_colour_blobs_parse() -> None:
    """The 2x64 channel-1 blob and the coloured-scene blob carry the expected rich content."""
    ch1 = load_backup_store((_FIXTURES / "mdt_dali_backup_ch1.dat").read_bytes())
    colour = load_backup_store(
        (_FIXTURES / "mdt_dali_backup_ch0_colour.dat").read_bytes()
    )
    # channel-1 blob has a ColourTemp template command.
    ch1_cmds = [c for t in ch1.templates for c in t.commands]
    assert any(c.func == "ColourTemp" for c in ch1_cmds)
    # colour blob has scene items carrying color-type + color CSV.
    col_items = [i for s in colour.scenes for i in s.items if i.color_type is not None]
    assert col_items
    assert any(c.func == "ColourHSV" for t in colour.templates for c in t.commands)


def test_fixture_round_trip_is_byte_exact() -> None:
    """load -> dump reproduces the original fixture byte-for-byte."""
    original = _FIXTURE.read_bytes()
    store = load_backup_store(original)
    rebuilt = dump_backup_store(store)
    assert rebuilt == original


def test_fixture_header_matches_mdt_shape() -> None:
    store = load_backup_store(_FIXTURE.read_bytes())
    # Verified against the real export: type = "gc16", version = "4.0.1.0" was written there.
    assert store.type == "gc16"
    assert store.version == "4.0.1.0"
    assert store.individual_address == "1.1.1"
    assert store.project == "mdtdali"
    assert store.can_restore is False


def test_fixture_container_counts_are_fixed() -> None:
    store = load_backup_store(_FIXTURE.read_bytes())
    assert len(store.groups) == 16
    assert len(store.devecgs) == 64
    assert len(store.ecgs) == 64
    assert len(store.scenes) == 16
    assert len(store.templates) == 16


def test_fixture_populated_scene_zero() -> None:
    store = load_backup_store(_FIXTURE.read_bytes())
    sc = store.scenes[0]
    assert sc.index == 0
    assert sc.is_dirty is True
    assert sc.fade_time == 0  # MDT writes fadeTime as the raw integer.
    assert sc.knx_scene == 1
    assert len(sc.items) == 1
    item = sc.items[0]
    assert item.itemtype == "Dali.DcaApp.Group"
    assert item.itemindex == 0
    assert item.value == 0
    assert (
        item.color_type is None
    )  # colour-type/color are only emitted for coloured items


def test_fixture_populated_template_zero() -> None:
    store = load_backup_store(_FIXTURE.read_bytes())
    tpl = store.templates[0]
    assert tpl.mode == "Enabled"
    assert tpl.is_dirty is True
    assert tpl.is_overwritable is False
    assert len(tpl.members) == 1
    assert len(tpl.commands) == 1
    cmd = tpl.commands[0]
    assert cmd.func == "SetValue"
    assert cmd.fade_in == "VFadeIn0"
    assert cmd.time == "00:00"
    assert cmd.weekdays == "1111111"  # 7-char "1"/"0" string, bit0=Monday


def test_merge_preserves_templates_and_descriptions() -> None:
    from xknxeditor.dali.model import (
        DaliBusState,
        EcgAssignment,
        EcgState,
        Scene,
        SceneValue,
    )
    from xknxeditor.dali.project_persistence import (
        load_backup_store,
        merge_commissioning_blob,
    )

    original = _FIXTURE.read_bytes()
    before = load_backup_store(original)
    # The fixture has a populated template 0 and ECG descriptions; both must survive a merge.
    assert before.templates[0].mode == "Enabled"
    assert len(before.templates[0].commands) == 1
    assert before.ecgs[0].description == "dasdsads"

    state = DaliBusState(
        channel=0,
        ecgs=[
            EcgState(slot=2, present=True, ecg_type=6, ets_ecg_index=7, group_index=3)
        ],
    )
    merged = merge_commissioning_blob(
        existing=original,
        state=state,
        assignments=[EcgAssignment(slot=2, ets_index=7, group_index=3)],
        scenes=[
            Scene(
                index=5,
                fade_time=2,
                knx_scene=6,
                values=[SceneValue(scene=5, target_index=3, level=200)],
            )
        ],
        individual_address="1.1.1",
        project_name="mdtdali",
    )
    after = load_backup_store(merged)
    # Untouched template + description preserved.
    assert after.templates[0].mode == "Enabled"
    assert len(after.templates[0].commands) == 1
    assert after.ecgs[0].description == "dasdsads"
    # Assignment binds physical slot 2 to logical ETS ECG 7. The two index spaces:
    #   <ECG index=7> (logical)  -> group, short-address = physical slot 2, flags Assigned
    #   <DevECG index=2> (slot)  -> group, short-address = logical ECG 7
    assert after.ecgs[7].groupindex == 3
    assert after.ecgs[7].short_address == 2
    assert after.ecgs[7].flags == 2
    assert after.devecgs[2].groupindex == 3
    assert after.devecgs[2].short_address == 7
    assert after.devecgs[2].type == 6
    # The logical row at the physical slot number is NOT the one that was touched.
    assert after.ecgs[2].groupindex == 255
    # Edited scene applied; its KNX number set and one item written.
    assert after.scenes[5].knx_scene == 6
    assert after.scenes[5].fade_time == 2
    assert len(after.scenes[5].items) == 1
    # A scene we did not touch keeps its default KNX mapping (index+1).
    assert after.scenes[10].knx_scene == 11


def test_merge_without_existing_seeds_from_scan() -> None:
    from xknxeditor.dali.model import DaliBusState, EcgAssignment, EcgState
    from xknxeditor.dali.project_persistence import (
        load_backup_store,
        merge_commissioning_blob,
    )

    state = DaliBusState(
        channel=0,
        ecgs=[
            EcgState(
                slot=0,
                present=True,
                ecg_type=6,
                ets_ecg_index=1,
                group_index=2,
                long_address=0x123456,
                min_value=10,
            )
        ],
    )
    blob = merge_commissioning_blob(
        existing=None,
        state=state,
        assignments=[EcgAssignment(slot=0, ets_index=1, group_index=2)],
        scenes=[],
        individual_address="1.1.9",
        project_name="fresh",
    )
    store = load_backup_store(blob)
    assert store.individual_address == "1.1.9"
    assert store.project == "fresh"
    assert len(store.devecgs) == 64
    # Physical slot 0 bound to logical ETS ECG 1.
    assert store.devecgs[0].type == 6
    assert store.devecgs[0].long_address == 0x123456
    assert store.devecgs[0].groupindex == 2
    assert store.devecgs[0].short_address == 1  # logical ECG number at this slot
    # The logical ECG row is index 1 (the ETS number), not index 0 (the slot).
    assert store.ecgs[1].groupindex == 2
    assert store.ecgs[1].short_address == 0  # physical slot it is bound to
    assert store.ecgs[1].flags == 2
    assert store.ecgs[1].type == 6
    # All 16 templates materialised even on a fresh blob.
    assert len(store.templates) == 16


def test_merge_preserves_colour_scenes_and_templates() -> None:
    """A group-assignment edit must not disturb coloured scene items or colour templates."""
    from xknxeditor.dali.model import DaliBusState, EcgAssignment, EcgState
    from xknxeditor.dali.project_persistence import merge_commissioning_blob

    original = (_FIXTURES / "mdt_dali_backup_ch0_colour.dat").read_bytes()
    before = load_backup_store(original)
    before_col = [
        (i.color_type, i.color)
        for s in before.scenes
        for i in s.items
        if i.color_type is not None
    ]
    before_cmds = [
        (c.func, c.value, c.fade_in) for t in before.templates for c in t.commands
    ]
    assert before_col and before_cmds

    merged = merge_commissioning_blob(
        existing=original,
        state=DaliBusState(
            channel=0,
            ecgs=[
                EcgState(
                    slot=1, present=True, ecg_type=6, ets_ecg_index=1, group_index=4
                )
            ],
        ),
        assignments=[EcgAssignment(slot=1, ets_index=1, group_index=4)],
        scenes=[],
        individual_address="1.1.2",
        project_name="mdtdali",
    )
    after = load_backup_store(merged)
    after_col = [
        (i.color_type, i.color)
        for s in after.scenes
        for i in s.items
        if i.color_type is not None
    ]
    after_cmds = [
        (c.func, c.value, c.fade_in) for t in after.templates for c in t.commands
    ]
    assert after_col == before_col  # coloured scene items untouched
    assert after_cmds == before_cmds  # colour template commands untouched
    # The edited assignment landed on both index spaces.
    assert after.ecgs[1].groupindex == 4
    assert after.devecgs[1].groupindex == 4

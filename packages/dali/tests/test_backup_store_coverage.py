"""Serialize/deserialize round-trip coverage for every ``DaliGC16-Backup-Store`` schema branch.

The real .knxproj fixtures we have only exercise a near-empty configuration (no colour-control
ECGs, no populated DevECGs, only a ``SetValue`` template command, a single DALI channel). These
tests construct a store that populates every optional field and assert that
``dump -> load -> dump`` is a byte-exact fixed point, so the branches that no fixture reaches
(colour temperature, coloured scene items, every template function, channel-N naming) stay
locked even though they are not byte-verified against a vendor export.
"""

from __future__ import annotations

from xknxeditor.dali.backup_store import (
    BackupDevEcg,
    BackupEcg,
    BackupGroup,
    BackupScene,
    BackupSceneItem,
    BackupStore,
    BackupTpl,
    BackupTplCommand,
    BackupTplMember,
    backup_store_name,
    dump_backup_store,
    load_backup_store,
)


def _rich_store() -> BackupStore:
    """A store that touches every optional attribute and child element."""
    return BackupStore(
        can_restore=True,
        individual_address="1.1.17",
        project="rich",
        type="gc16",
        version="4.0.1.0",
        groups=[
            BackupGroup(index=0, description="Kitchen"),
            BackupGroup(index=1),
        ],
        devecgs=[
            # Colour-control ballast: type 8 + subtype bit1 -> min/max ctemp emitted.
            BackupDevEcg(
                index=0,
                type=8,
                subtype=2,
                groupindex=3,
                short_address=0,
                long_address=0xA1B2C3,
                min_value=5,
                description="Downlight",
                min_ctemp=2700,
                max_ctemp=6500,
            ),
            # Plain ballast: no ctemp attributes.
            BackupDevEcg(
                index=1,
                type=6,
                subtype=0,
                groupindex=255,
                short_address=255,
                long_address=0xFFFFFF,
                min_value=0,
            ),
        ],
        ecgs=[
            BackupEcg(
                index=0,
                groupindex=3,
                short_address=0,
                flags=2,
                type=8,
                description="D1",
            ),
            BackupEcg(index=1, groupindex=255, short_address=255, flags=0, type=0),
        ],
        scenes=[
            BackupScene(
                index=0,
                is_dirty=True,
                fade_time=4,
                knx_scene=1,
                description="Evening",
                items=[
                    BackupSceneItem(
                        itemtype="Dali.DcaApp.Group", itemindex=3, value=200, setting=0
                    ),
                    # Coloured ECG target: color-type + color CSV emitted.
                    BackupSceneItem(
                        itemtype="Dali.DcaApp.ECG",
                        itemindex=0,
                        value=254,
                        setting=1,
                        color_type=4,
                        color="2700;0;0;0",
                    ),
                ],
            ),
            BackupScene(index=1, knx_scene=2),
        ],
        templates=[
            BackupTpl(
                index=0,
                mode="Enabled",
                is_dirty=True,
                is_overwritable=True,
                description="Tpl",
                members=[
                    BackupTplMember(itemtype="Dali.DcaApp.Group", itemindex=3),
                    BackupTplMember(itemtype="Dali.DcaApp.ECG", itemindex=0),
                ],
                commands=[
                    # Value function -> "V" fade prefix.
                    BackupTplCommand(
                        func="SetValue",
                        value="128",
                        fade_in="VFadeIn2",
                        time="06:30",
                        weekdays="1111100",
                    ),
                    # Colour function -> "C" fade prefix.
                    BackupTplCommand(
                        func="ColourTemp",
                        value="4000;0;0;0",
                        fade_in="CFadeIn3",
                        time="18:00",
                        weekdays="0000011",
                    ),
                ],
            ),
            BackupTpl(index=1),
        ],
    )


def test_rich_store_dump_load_is_fixed_point() -> None:
    """dump -> load -> dump reproduces the first dump byte-for-byte for a fully-populated store."""
    store = _rich_store()
    first = dump_backup_store(store)
    second = dump_backup_store(load_backup_store(first))
    assert first == second


def test_colour_devecg_emits_ctemp_only_when_colour() -> None:
    store = _rich_store()
    reloaded = load_backup_store(dump_backup_store(store))
    colour = next(d for d in reloaded.devecgs if d.index == 0)
    plain = next(d for d in reloaded.devecgs if d.index == 1)
    assert colour.min_ctemp == 2700
    assert colour.max_ctemp == 6500
    assert plain.min_ctemp is None
    assert plain.max_ctemp is None


def test_coloured_scene_item_round_trips_colour_fields() -> None:
    store = _rich_store()
    reloaded = load_backup_store(dump_backup_store(store))
    scene = next(s for s in reloaded.scenes if s.index == 0)
    plain_item = scene.items[0]
    colour_item = scene.items[1]
    assert plain_item.color_type is None
    assert colour_item.color_type == 4
    assert colour_item.color == "2700;0;0;0"


def test_template_command_fade_prefix_round_trips() -> None:
    store = _rich_store()
    reloaded = load_backup_store(dump_backup_store(store))
    cmds = reloaded.templates[0].commands
    assert cmds[0].func == "SetValue"
    assert cmds[0].fade_in == "VFadeIn2"
    assert cmds[1].func == "ColourTemp"
    assert cmds[1].fade_in == "CFadeIn3"


def test_channel_blob_name_suffix() -> None:
    assert backup_store_name(0) == "DaliGC16-Backup-Store"
    assert backup_store_name(1) == "DaliGC16-Backup-Store.1"
    assert backup_store_name(2) == "DaliGC16-Backup-Store.2"

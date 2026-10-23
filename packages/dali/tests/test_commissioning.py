"""Flow tests for MdtDaliCommissioner (scan / identify / commission / write_scenes)."""

from _dali_fakes import FakeProgrammer

from xknxeditor.dali.commissioning import MdtDaliCommissioner
from xknxeditor.dali.connection import MdtDaliConnection
from xknxeditor.dali.model import (
    GROUP_MISSING,
    GROUP_UNASSIGNED,
    MAX_ECG,
    MAX_TEMPLATES,
    EcgAssignment,
    Scene,
    SceneValue,
    Template,
    TemplateCommand,
)
from xknxeditor.dali.properties import DeviceCommand, PropertyType
from xknxeditor.dali.structs import EcgProp, EcgStatic, decode_table


def _evg_prop_table(entries: dict[int, EcgProp]) -> bytes:
    default = EcgProp(0, 0, GROUP_MISSING, 0)
    return b"".join(entries.get(slot, default).encode() for slot in range(MAX_ECG))


def _setup() -> tuple[MdtDaliCommissioner, FakeProgrammer]:
    prog = FakeProgrammer()
    return MdtDaliCommissioner(MdtDaliConnection(prog, 0)), prog


async def test_scan_reports_present_ecgs() -> None:
    comm, prog = _setup()
    prog.set_read(25, bytes((4, 0, 1)))  # firmware
    prog.set_read(
        PropertyType.EVG_PROP,
        _evg_prop_table({0: EcgProp(8, 2, 5, 0), 3: EcgProp(6, 0, 16, 3)}),
    )
    prog.set_read(PropertyType.EVG_STATUS, b"\x00\x02\x00\x00\x00" * MAX_ECG)
    prog.set_read(
        PropertyType.EVG_STATIC, (b"\x00\x00\x2a" + b"\x0a\x00\x00\x00\x00") * MAX_ECG
    )
    prog.set_read(PropertyType.GROUP_DYN, b"\x00" * (6 * 16))
    prog.set_read(PropertyType.GROUP_STATUS, b"\x00" * (8 * 16))
    prog.set_read(PropertyType.GROUP_FAILURE, b"\x00" * (6 * 16))

    state = await comm.scan()
    assert state.firmware == (4, 0, 1)
    present = {e.slot: e for e in state.present_ecgs}
    assert set(present) == {0, 3}
    assert present[0].group_index == 5 and present[0].ecg_type == 8
    assert present[0].long_address == 0x2A and present[0].alarm == 0x02
    assert len(state.groups) == 16


async def test_identify_sends_expected_command_bytes() -> None:
    comm, prog = _setup()
    await comm.switch_ecg(5, on=True)
    await comm.blink_ecg(5, start=True)
    await comm.broadcast(on=False)
    cmds = [w["data"] for w in prog.writes_for(PropertyType.COMMAND)]
    assert cmds[0] == bytes((DeviceCommand.SET_ECG_VALUE, 5, 0xFF))
    assert cmds[1] == bytes((DeviceCommand.BLINK, 5 | 0x40, 0))
    assert cmds[2] == bytes((DeviceCommand.SET_ALL_ECG, 0, 0))


async def test_commission_writes_sync_table_and_verifies() -> None:
    comm, prog = _setup()
    prog.queue_read(PropertyType.COMMAND, bytes((0, 0, 0)))  # PLUGIN_SYNC done
    prog.set_read(
        PropertyType.EVG_PROP, _evg_prop_table({0: EcgProp(8, 2, 5, 0)})
    )  # verify

    ok = await comm.commission([EcgAssignment(slot=0, ets_index=0, group_index=5)])
    assert ok is True
    (sync,) = prog.writes_for(PropertyType.COMMISSION_SYNC)
    assert sync["count"] == MAX_ECG
    table = decode_table(bytes(sync["data"]), 2)  # type: ignore[arg-type]
    assert table[0] == bytes((0, 5))  # slot 0 -> ets 0, group 5
    # unspecified slots are seeded from the current EVG_PROP (here: missing -> ets 0, group 255),
    # never blindly wiped to unassigned.
    assert table[1] == bytes((0, GROUP_MISSING))
    assert bytes((DeviceCommand.PLUGIN_SYNC, 0, 0)) in [
        w["data"] for w in prog.writes_for(PropertyType.COMMAND)
    ]


async def test_new_installation_sends_command_reports_progress_and_rescans() -> None:
    comm, prog = _setup()
    # poll: running (found=2), then done; scan afterwards reads the (new) bus.
    prog.queue_read(PropertyType.COMMAND, bytes((5, 2, 0)), bytes((0, 4, 0)))
    prog.set_read(25, bytes((4, 0, 1)))
    prog.set_read(PropertyType.EVG_PROP, _evg_prop_table({0: EcgProp(8, 0, 32, 0)}))
    prog.set_read(PropertyType.EVG_STATUS, b"\x00" * (5 * MAX_ECG))
    prog.set_read(PropertyType.EVG_STATIC, b"\x00" * (8 * MAX_ECG))
    prog.set_read(PropertyType.GROUP_DYN, b"\x00" * (6 * 16))
    prog.set_read(PropertyType.GROUP_STATUS, b"\x00" * (8 * 16))
    prog.set_read(PropertyType.GROUP_FAILURE, b"\x00" * (6 * 16))

    seen: list[int] = []
    state = await comm.new_installation(
        pre_assign_group=1, interval=0.0, on_progress=lambda c: seen.append(c.param1)
    )
    (cmd,) = [w["data"] for w in prog.writes_for(PropertyType.COMMAND)]
    assert cmd == bytes((DeviceCommand.NEW_INSTALLATION, 0, 1))
    assert seen == [2]  # live found counter surfaced from the running readback
    assert len(state.present_ecgs) == 1


async def test_post_installation_command_bytes() -> None:
    comm, prog = _setup()
    prog.queue_read(PropertyType.COMMAND, bytes((0, 0, 0)))
    for pid in (
        PropertyType.EVG_PROP,
        PropertyType.EVG_STATUS,
        PropertyType.EVG_STATIC,
        PropertyType.GROUP_DYN,
        PropertyType.GROUP_STATUS,
        PropertyType.GROUP_FAILURE,
    ):
        prog.set_read(pid, b"\x00" * (8 * MAX_ECG))
    await comm.post_installation(flags=3, pre_assign_group=2, interval=0.0)
    (cmd,) = [w["data"] for w in prog.writes_for(PropertyType.COMMAND)]
    assert cmd == bytes((DeviceCommand.POST_INSTALLATION, 3, 2))


async def test_write_scenes_builds_tables_and_syncs() -> None:
    comm, prog = _setup()
    prog.set_read(
        PropertyType.SCENE_KNX_MAP, bytes((0, 0))
    )  # size 0 -> write size + map
    prog.queue_read(PropertyType.COMMAND, bytes((0, 0, 0)))  # SCENE_SYNC done

    await comm.write_scenes(
        [Scene(index=0, fade_time=3, knx_scene=7, values=[SceneValue(0, 16, 254)])]
    )
    (assign,) = prog.writes_for(PropertyType.SCENE_ASSIGN)
    assert bytes(assign["data"])[0] == 3 and assign["count"] == 16  # type: ignore[index]
    (values,) = prog.writes_for(PropertyType.SCENE_GROUP_VALUE)
    data = bytes(values["data"])  # type: ignore[arg-type]
    assert data[:8] == bytes((0, 16, 254, 0, 0, 0, 0, 0))  # first record
    assert data[-8:] == b"\xff" * 8  # terminator
    assert bytes((DeviceCommand.SCENE_SYNC, 0, 0)) in [
        w["data"] for w in prog.writes_for(PropertyType.COMMAND)
    ]


async def test_write_scenes_merges_untouched_slots() -> None:
    comm, prog = _setup()
    # Pre-seed existing scene tables: fade 5 for scene 2, knx-map 42 for scene 2,
    # and a scene-2 value record that must survive an edit of scene 0.
    existing_assign = bytearray(16)
    existing_assign[2] = 5
    prog.set_read(PropertyType.SCENE_ASSIGN, bytes(existing_assign))
    existing_knx = bytearray(16)
    existing_knx[2] = 42
    prog.queue_read(PropertyType.SCENE_KNX_MAP, bytes((0, 16)))  # size = 16
    prog.set_read(PropertyType.SCENE_KNX_MAP, bytes(existing_knx))
    scene2_rec = bytes((2, 20, 128, 0, 0, 0, 0, 0))
    prog.set_read(PropertyType.SCENE_GROUP_VALUE, scene2_rec + b"\xff" * 8)
    prog.queue_read(PropertyType.COMMAND, bytes((0, 0, 0)))  # SCENE_SYNC done

    await comm.write_scenes(
        [Scene(index=0, fade_time=3, knx_scene=7, values=[SceneValue(0, 16, 254)])]
    )
    (assign,) = prog.writes_for(PropertyType.SCENE_ASSIGN)
    assign_bytes = bytes(assign["data"])  # type: ignore[arg-type]
    assert assign_bytes[0] == 3  # new fade for scene 0
    assert assign_bytes[2] == 5  # untouched fade for scene 2 preserved

    (values,) = prog.writes_for(PropertyType.SCENE_GROUP_VALUE)
    val_bytes = bytes(values["data"])  # type: ignore[arg-type]
    # kept record for scene 2 appears before the new scene-0 record, terminator at the end
    assert val_bytes[:8] == scene2_rec
    assert val_bytes[8:16] == bytes((0, 16, 254, 0, 0, 0, 0, 0))
    assert val_bytes[-8:] == b"\xff" * 8

    knx_writes = prog.writes_for(PropertyType.SCENE_KNX_MAP)
    knx_data = bytes(knx_writes[-1]["data"])  # type: ignore[arg-type]
    assert knx_data[0] == 7  # new knx scene for 0
    assert knx_data[2] == 42  # untouched scene 2 preserved


async def test_write_ecg_props_writes_full_padded_table() -> None:
    comm, prog = _setup()
    await comm.write_ecg_props([EcgProp(8, 2, 5, 0), EcgProp(6, 0, 16, 1)])
    (w,) = prog.writes_for(PropertyType.EVG_PROP)
    data = bytes(w["data"])  # type: ignore[arg-type]
    assert w["count"] == MAX_ECG and len(data) == MAX_ECG * 4
    table = decode_table(data, 4)
    assert EcgProp.decode(table[0]) == EcgProp(8, 2, 5, 0)
    assert EcgProp.decode(table[1]) == EcgProp(6, 0, 16, 1)
    assert EcgProp.decode(table[2]) == EcgProp(0, 0, GROUP_UNASSIGNED, 2)  # padded slot


async def test_write_ecg_static_writes_full_padded_table() -> None:
    comm, prog = _setup()
    await comm.write_ecg_static([EcgStatic(0x2A, 10, 2700, 6500)])
    (w,) = prog.writes_for(PropertyType.EVG_STATIC)
    data = bytes(w["data"])  # type: ignore[arg-type]
    assert w["count"] == MAX_ECG and len(data) == MAX_ECG * 8
    table = decode_table(data, 8)
    assert EcgStatic.decode(table[0]) == EcgStatic(0x2A, 10, 2700, 6500)
    assert EcgStatic.decode(table[1]) == EcgStatic(0, 0, 0, 0)  # padded slot


async def test_restore_follows_dca_order_and_returns_verify() -> None:
    comm, prog = _setup()
    assignment = EcgAssignment(slot=0, ets_index=9, group_index=5)
    # EVG_PROP readback (seeding + verify): slot 0 matches the requested assignment.
    prog.set_read(PropertyType.EVG_PROP, _evg_prop_table({0: EcgProp(6, 0, 5, 9)}))
    prog.set_read(PropertyType.SCENE_KNX_MAP, bytes((0, 0)))
    # PLUGIN_SYNC and SCENE_SYNC each poll COMMAND once for a "done" readback.
    prog.queue_read(PropertyType.COMMAND, bytes((0, 0, 0)), bytes((0, 0, 0)))

    ok = await comm.restore(
        static=[EcgStatic(0x2A, 10, 2700, 6500)],
        props=[EcgProp(6, 0, 5, 9)],
        assignments=[assignment],
        scenes=[
            Scene(index=0, fade_time=3, knx_scene=7, values=[SceneValue(0, 16, 254)])
        ],
    )
    assert ok is True
    order = [w["property_id"] for w in prog.writes]
    assert (
        order.index(PropertyType.EVG_STATIC)
        < order.index(PropertyType.EVG_PROP)
        < order.index(PropertyType.COMMISSION_SYNC)
    )


async def test_write_templates_builds_event_and_member_tables() -> None:
    comm, prog = _setup()
    tpl = Template(
        index=0,
        mode=1,
        overwritable=True,
        groups=(0, 3),
        ecgs=(5,),
        commands=[
            TemplateCommand(
                func=4,
                hour=6,
                minute=30,
                value=b"\x11\x22\x33\x44",
                fade_time=2,
                weekdays=0x7F,
            )
        ],
    )
    await comm.write_templates([tpl])

    (ev,) = prog.writes_for(PropertyType.COLOUR_C_EVENT)
    data = bytes(ev["data"])  # type: ignore[arg-type]
    assert ev["count"] == 2 and len(data) == 20  # 1 command + terminator
    # hour=6 -> HM low5; minute=30 split; mode=1 bits6-7; overwritable bit5
    hm = (6 & 0x1F) | ((30 << 2) & 0xE0)
    mm = (30 & 0x07) | (1 << 6) | 0x20
    assert data[:10] == bytes((0, 4, 0x11, 0x22, 0x33, 0x44, 2, 0x7F, hm, mm))
    assert data[10:20] == b"\xff" * 10  # terminator

    (members,) = prog.writes_for(PropertyType.COLOUR_C_ASSIGN)
    mdata = bytes(members["data"])  # type: ignore[arg-type]
    assert members["count"] == MAX_TEMPLATES and len(mdata) == MAX_TEMPLATES * 10
    table = decode_table(mdata, 10)
    # template 0: groups 0,3 -> byte0 bits; ecg 5 -> byte2 bit5
    assert table[0] == bytes((0b00001001, 0, 0x20, 0, 0, 0, 0, 0, 0, 0))
    assert table[1] == b"\x00" * 10  # no template at index 1


async def test_write_templates_empty_clears_tables() -> None:
    comm, prog = _setup()
    await comm.write_templates([])
    (ev,) = prog.writes_for(PropertyType.COLOUR_C_EVENT)
    assert ev["count"] == 1 and bytes(ev["data"]) == b"\xff" * 10  # type: ignore[arg-type]
    (members,) = prog.writes_for(PropertyType.COLOUR_C_ASSIGN)
    assert bytes(members["data"]) == b"\x00" * (MAX_TEMPLATES * 10)  # type: ignore[arg-type]

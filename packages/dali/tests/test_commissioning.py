"""Flow tests for MdtDaliCommissioner (scan / identify / commission / write_scenes)."""

import pytest
from _dali_fakes import FakeProgrammer

from xknxeditor.dali.commissioning import MdtDaliCommissioner
from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.model import (
    GROUP_MISSING,
    GROUP_UNASSIGNED,
    MAX_ECG,
    MAX_TEMPLATES,
    DaliBusState,
    EcgAssignment,
    Scene,
    SceneValue,
    Template,
    TemplateCommand,
)
from xknxeditor.dali.profiles import PROFILES
from xknxeditor.dali.properties import DeviceCommand, PropertyType, element_size_for
from xknxeditor.dali.structs import EcgProp, EcgStatic, decode_table


async def test_level_edit_preserves_other_targets_colours_and_scene_metadata() -> None:
    comm, prog = _setup()
    original = [
        bytes((2, 4, 50, 1, 2, 3, 4, 6)),
        bytes((2, 19, 90, 5, 6, 7, 8, 0)),
        bytes((7, 0, 20, 0, 0, 0, 0, 0)),
    ]
    prog.set_read(PropertyType.SCENE_GROUP_VALUE, b"".join(original) + b"\xff" * 8)
    await comm.write_scenes(
        [Scene(2, values=[SceneValue(2, 4, 128), SceneValue(2, 5, 255)])],
        levels_only=True,
    )
    writes = prog.writes_for(PropertyType.SCENE_GROUP_VALUE)
    assert b"".join(w["data"] for w in writes) == b"".join(
        [
            bytes((2, 4, 128, 1, 2, 3, 4, 4)),
            original[1],
            original[2],
            bytes((2, 5, 255, 0, 0, 0, 0, 1)),
            b"\xff" * 8,
        ]
    )
    assert not prog.writes_for(PropertyType.SCENE_ASSIGN)
    assert not prog.writes_for(PropertyType.SCENE_KNX_MAP)


async def test_scene_terminator_is_identified_by_scene_number():
    comm, prog = _setup()
    prog.set_read(PropertyType.SCENE_GROUP_VALUE, b"\xff" + bytes(7))
    await comm.write_scenes(
        [Scene(0, values=[SceneValue(0, 1, 128)])], levels_only=True
    )
    assert [(p, start, count) for p, start, count in prog.reads if p == 214] == [
        (214, 1, 1)
    ]
    assert bytes(prog.writes_for(214)[0]["data"])[-8:] == b"\xff" * 8


def _evg_prop_table(entries: dict[int, EcgProp]) -> bytes:
    default = EcgProp(0, 0, GROUP_MISSING, 0)
    return b"".join(entries.get(slot, default).encode() for slot in range(MAX_ECG))


def _setup() -> tuple[MdtDaliCommissioner, FakeProgrammer]:
    prog = FakeProgrammer()
    prog.set_read(PropertyType.SCENE_ASSIGN, bytes(16))
    prog.set_read(PropertyType.SCENE_GROUP_VALUE, b"\xff" * 8)
    prog.set_read(PropertyType.COMMAND, bytes(3))
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


@pytest.mark.parametrize("profile_index", [0, 1, 2])
async def test_replacement_polls_before_reading_new_inventory(profile_index):
    from unittest.mock import AsyncMock

    from xknxeditor.dali.profiles import PROFILES

    programmer = FakeProgrammer()
    programmer.set_read(PropertyType.COMMAND, bytes(3))
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, 1, profile=PROFILES[profile_index])
    )
    scan = DaliBusState(channel=1)
    commissioner.scan = AsyncMock(return_value=scan)
    assert await commissioner.replace_ecg() is scan
    assert programmer.writes[0]["data"] == bytes([19, 0, 0])
    assert (
        programmer.writes[0]["object_index"]
        == PROFILES[profile_index].object_index_base + 1
    )
    commissioner.scan.assert_awaited_once_with()


@pytest.mark.parametrize("error", [7, 8, 9, 10, 11])
async def test_replacement_errors_keep_gateway_reason_and_skip_readback(error):
    from unittest.mock import AsyncMock

    from xknxeditor.dali.profiles import PROFILES

    programmer = FakeProgrammer()
    programmer.set_read(PropertyType.COMMAND, bytes([255, error, 3]))
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[0])
    )
    commissioner.scan = AsyncMock()
    with pytest.raises(DaliCommissioningError, match=f"result={error}, detail=3"):
        await commissioner.replace_ecg()
    commissioner.scan.assert_not_awaited()


async def test_cancelled_replacement_aborts_and_never_reports_success():
    import asyncio
    from unittest.mock import AsyncMock

    from xknxeditor.dali.profiles import PROFILES

    programmer = FakeProgrammer()
    connection = MdtDaliConnection(programmer, profile=PROFILES[0])
    connection.poll_result = AsyncMock(side_effect=asyncio.CancelledError)
    commissioner = MdtDaliCommissioner(connection)
    commissioner.scan = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await commissioner.replace_ecg()
    assert [w["data"] for w in programmer.writes] == [
        bytes([19, 0, 0]),
        bytes([3, 0, 0]),
    ]
    commissioner.scan.assert_not_awaited()


async def test_replacement_requires_a_known_profile():
    commissioner, programmer = _setup()
    with pytest.raises(DaliCommissioningError, match="unsupported"):
        await commissioner.replace_ecg()
    assert not programmer.writes


async def test_identify_sends_expected_command_bytes() -> None:
    comm, prog = _setup()
    await comm.switch_ecg(5, on=True)
    await comm.blink_ecg(5, start=True)
    await comm.broadcast(on=False)
    cmds = [w["data"] for w in prog.writes_for(PropertyType.COMMAND)]
    assert cmds[0] == bytes((DeviceCommand.SET_ECG_VALUE, 5, 0xFF))
    assert cmds[1] == bytes((DeviceCommand.BLINK, 5 | 0x40, 0))
    assert cmds[2] == bytes((DeviceCommand.SET_ALL_ECG, 0, 0))


@pytest.mark.parametrize("profile", PROFILES)
async def test_group_blink_keeps_group_and_ecg_addressing_distinct(profile):
    prog = FakeProgrammer()
    comm = MdtDaliCommissioner(MdtDaliConnection(prog, 0, profile=profile))
    await comm.blink_group(15)
    await comm.blink_group(15, False)
    await comm.blink_ecg(15)
    await comm.blink_ecg(63, False)
    assert [w["data"] for w in prog.writes_for(PropertyType.COMMAND)] == [
        bytes((4, 15, 0)),
        bytes((4, 143, 0)),
        bytes((4, 79, 0)),
        bytes((4, 191, 0)),
    ]


@pytest.mark.parametrize(
    ("method", "invalid"),
    [
        ("blink_ecg", -1),
        ("blink_ecg", 64),
        ("switch_ecg", 256),
        ("switch_ecg", -1),
        ("blink_group", 16),
        ("blink_group", -1),
        ("switch_group", 256),
        ("switch_group", -1),
    ],
)
async def test_identify_rejects_invalid_targets_before_sending(method, invalid):
    comm, prog = _setup()
    with pytest.raises(DaliCommissioningError):
        await getattr(comm, method)(invalid, True)
    assert not prog.writes


async def test_commission_writes_sync_table_and_verifies() -> None:
    comm, prog = _setup()
    prog.queue_read(PropertyType.COMMAND, bytes((0, 0, 0)))  # PLUGIN_SYNC done
    prog.set_read(
        PropertyType.EVG_PROP,
        _evg_prop_table({0: EcgProp(8, 2, 5, 0), 3: EcgProp(6, 0, 9, 12)}),
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
    assert table[3] == bytes((12, 9))
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
        prog.set_read(pid, b"\x00" * (element_size_for(pid) * MAX_ECG))
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


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.key)
async def test_vendor_scene_fade_pid_and_group_status_stride(profile):
    prog = FakeProgrammer()
    wire_fade = 212 if profile.key == "mdt" else 213
    stride = 8 if profile.key == "mdt" else 10
    prog.element_sizes[208] = stride
    prog.set_read(wire_fade, bytes(range(16)))
    prog.set_read(214, b"\xff" * 8)
    prog.set_read(218, bytes(3))
    comm = MdtDaliCommissioner(MdtDaliConnection(prog, profile=profile))
    await comm.write_scenes([Scene(0, fade_time=9)])
    assert bytes(prog.writes_for(wire_fade)[0]["data"]) == bytes([9, *range(1, 16)])
    assert not prog.writes_for(213 if wire_fade == 212 else 212)
    prog.set_read(206, bytes(6 * 16))
    prog.set_read(209, bytes(6 * 16))
    prog.set_read(
        208, b"".join(bytes([0, 0, 0, i]) + bytes(stride - 4) for i in range(16))
    )
    assert [g.knx_value for g in await comm._read_groups()] == list(range(16))


@pytest.mark.parametrize("failed", [212, 214, 228, "descriptor"])
async def test_scene_read_failure_never_writes(failed):
    comm, prog = _setup()
    prog.queue_read(228, bytes([0, 16]))
    prog.set_read(228, bytes(16))
    if failed == "descriptor":
        prog._queues[228] = [b"\x00"]
    else:
        prog.set_read(failed, b"")
    with pytest.raises(DaliCommissioningError):
        await comm.write_scenes([Scene(0, values=[SceneValue(0, 0, 100)])])
    assert prog.writes == []


@pytest.mark.parametrize("existing_count", [129, 1023, 1024])
async def test_scene_preservation_beyond_128_and_at_capacity(existing_count):
    comm, prog = _setup()
    records = [
        bytes([i // 64, i % 64, 30, 0, 0, 0, 0, 0]) for i in range(existing_count)
    ]
    prog.set_read(
        214, b"".join(records) + (b"\xff" * 8 if existing_count < 1024 else b"")
    )
    await comm.write_scenes(
        [Scene(0, values=[SceneValue(0, i, 42) for i in range(64)])]
    )
    result = decode_table(bytes(prog.writes_for(214)[0]["data"]), 8)
    assert result[: existing_count - 64] == records[64:]
    assert len(result) == (existing_count + 1 if existing_count < 1024 else 1024)
    assert (result[-1] == b"\xff" * 8) == (existing_count < 1024)


async def test_scene_capacity_overflow_fails_before_any_write():
    comm, prog = _setup()
    prog.set_read(214, bytes([1, 0, 30, 0, 0, 0, 0, 0]) * 1024)
    with pytest.raises(DaliCommissioningError, match="capacity"):
        await comm.write_scenes([Scene(0, values=[SceneValue(0, 0, 42)])])
    assert not prog.writes


@pytest.mark.parametrize(
    "scene",
    [
        Scene(-1),
        Scene(16),
        Scene(0, knx_scene=65),
        Scene(0, values=[SceneValue(1, 0, 10)]),
        Scene(0, values=[SceneValue(0, -1, 10)]),
    ],
)
async def test_invalid_scene_never_reads_or_writes(scene):
    comm, prog = _setup()
    with pytest.raises(DaliCommissioningError):
        await comm.write_scenes([scene])
    assert not prog.writes and not prog.reads


@pytest.mark.parametrize("count", [0, 299, 300, 301])
async def test_template_capacity_reset_and_sync(count):
    _, prog = _setup()
    comm = MdtDaliCommissioner(MdtDaliConnection(prog, profile=PROFILES[2]))
    template = Template(
        0, reset_on_midnight=False, commands=[TemplateCommand(1, 7, 15)] * count
    )
    if count > 300:
        with pytest.raises(DaliCommissioningError, match="capacity"):
            await comm.write_templates([template])
        assert not prog.writes
        return
    await comm.write_templates([template])
    event = prog.writes_for(225)[0]
    assert event["count"] == min(count + 1, 300)
    data = bytes(event["data"])
    if count:
        assert data[9] & 0x10
    assert (data[-10:] == b"\xff" * 10) == (count < 300)
    assert prog.writes[-1]["data"] == bytes([27, 0, 0])


async def test_template_member_validation_precedes_event_write():
    comm, prog = _setup()
    with pytest.raises(DaliCommissioningError):
        await comm.write_templates(
            [Template(0, groups=(-1,), commands=[TemplateCommand(1, 1, 0)])]
        )
    assert not prog.writes


async def test_reassignment_removes_previous_physical_mapping_and_verifies_it():
    comm, prog = _setup()
    current = _evg_prop_table({0: EcgProp(8, 2, 3, 9), 1: EcgProp(8, 2, 16, 7)})
    updated = _evg_prop_table({0: EcgProp(8, 2, 32, 255), 1: EcgProp(8, 2, 2, 9)})
    prog.queue_read(
        PropertyType.EVG_PROP,
        *(
            data[o : o + 60]
            for data in (current, updated)
            for o in range(0, len(data), 60)
        ),
    )
    await comm.commission([EcgAssignment(1, 9, 2)])
    table = prog.writes_for(PropertyType.COMMISSION_SYNC)[0]["data"]
    assert table[:4] == bytes([255, 32, 9, 2])
    prog.queue_read(
        PropertyType.EVG_PROP,
        *(
            data[o : o + 60]
            for data in (current, current)
            for o in range(0, len(data), 60)
        ),
    )
    with pytest.raises(DaliCommissioningError, match="verify"):
        await comm.commission([EcgAssignment(1, 9, 2)])


async def test_duplicate_logical_ecg_mapping_fails_before_transport():
    comm, prog = _setup()
    with pytest.raises(DaliCommissioningError, match="assignment"):
        await comm.commission([EcgAssignment(1, 9, 2), EcgAssignment(2, 9, 2)])
    assert not prog.reads and not prog.writes


@pytest.mark.parametrize(
    "invalid", ["template", "scene", "scene_read", "static", "props", "assignment"]
)
async def test_restore_prepares_every_table_before_writing(invalid):
    comm, prog = _setup()
    static = [EcgStatic(42, 10, 2700, 6500)]
    props = [EcgProp(8, 2, 5, 0)]
    assignments = [EcgAssignment(0, 0, 5)]
    scenes = [Scene(0, values=[SceneValue(0, 0, 128)])]
    templates = [Template(0)]
    if invalid == "template":
        templates[0].groups = (-1,)
    elif invalid == "scene":
        scenes[0].values[0].level = 256
    elif invalid == "scene_read":
        prog.set_read(PropertyType.SCENE_GROUP_VALUE, b"")
    elif invalid == "static":
        static = [EcgStatic(0x1000000, 10, 2700, 6500)]
    elif invalid == "props":
        props = [EcgProp(8, 256, 5, 0)]
    else:
        assignments.append(EcgAssignment(0, 1, 6))
    with pytest.raises(DaliCommissioningError):
        await comm.restore(
            static=static,
            props=props,
            assignments=assignments,
            scenes=scenes,
            templates=templates,
        )
    assert not prog.writes


@pytest.mark.parametrize(
    "template",
    [
        Template(0, reset_on_midnight=False),
        Template(0, commands=[TemplateCommand(18, 1, 0)]),
    ],
)
async def test_mdt_rejects_extended_template_features(template):
    comm, prog = _setup()
    with pytest.raises(DaliCommissioningError, match="unsupported"):
        await comm.write_templates([template])
    assert not prog.writes

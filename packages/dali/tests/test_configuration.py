from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from _dali_fakes import FakeProgrammer

from xknxeditor.dali.commissioning import MdtDaliCommissioner
from xknxeditor.dali.configuration import (
    color_bytes,
    compile_configuration,
    inputs_from_xml,
    level,
    template_command,
)
from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.effects import effect_plan
from xknxeditor.dali.model import Effect, EffectStep, Template, TemplateCommand
from xknxeditor.dali.profiles import PROFILES
from xknxeditor.dali.properties import DeviceCommand, PropertyType

DATA = (Path(__file__).parent / "fixtures/ipas_e64pro_backup.dat").read_bytes()


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (2, "3000", "b80b0000"),
        (1, "0.5;0.25;1", "00800040"),
        (8, "255;128;7;42", "ff80072a"),
        (4, "255;128;7;42", "ff800700"),
        (32, "0.5;0.25;1;42", "8040ff2a"),
        (16, "0.5;0.25;1", "8040ff00"),
    ],
)
def test_color_wire_encodings(kind, value, expected):
    assert color_bytes(kind, value).hex() == expected


def test_compile_real_configuration_and_command_scale():
    config = compile_configuration(DATA)
    assert len(config.scenes) == len(config.templates) == len(config.effects) == 16
    assert len(config.inputs) == 768
    assert level("100") == 255 and level("35") == 89
    color = template_command(
        ET.fromstring(
            '<TplCmd func="ColourTemp" value="3000" time="07:15" weekdays="0011111" fadeIn="CFadeIn60"/>'
        )
    )
    assert (color.value, color.fade_time, color.weekdays) == (
        bytes.fromhex("b80b0000"),
        14,
        31,
    )
    start = template_command(
        ET.fromstring(
            '<TplCmd func="CallEffect" value="15" scaleMode="ScaleNoneEffectStart" time="07:15" weekdays="0011111" fadeIn="VFadeIn60"/>'
        )
    )
    assert start.value == bytes([15, 1, 0, 0]) and start.fade_time == 0


def test_readback_preserves_missing_map_and_legacy_target_color():
    from xknxeditor.dali.configuration_readback import merge_scenes
    from xknxeditor.dali.model import Scene, SceneValue

    legacy = (
        Path(__file__).parent / "fixtures/mdt_dali_backup_ch0_colour.dat"
    ).read_bytes()
    scene = Scene(
        0, 0, 0, [SceneValue(0, 0, 0, bytes([1, 2, 3, 4]))], knx_mapping_available=False
    )
    before = ET.fromstring(legacy).find("scenes/Scene")
    result = ET.fromstring(merge_scenes(legacy, [scene])).find("scenes/Scene")
    assert result.get("knxScene") == before.get("knxScene")
    assert result.find("SceneItem").get("color") == "1;2;3;4"
    assert result.find("SceneItem").get("color-type") == "8"


@pytest.mark.parametrize("group_index", [0, 15, 64])
def test_readback_omits_stale_grouped_ecg_targets_in_scenes_and_effects(group_index):
    from xknxeditor.dali.configuration_readback import merge_effects, merge_scenes
    from xknxeditor.dali.model import Scene, SceneValue

    root = ET.fromstring(DATA)
    root.find("ecgs/ECG").set("groupindex", str(group_index))
    scenes = [Scene(0, values=[SceneValue(0, 16, 128), SceneValue(0, 0, 255)])]
    effects = [Effect(0, steps=[EffectStep(16, 128), EffectStep(0, 255)])]
    merged = merge_effects(merge_scenes(ET.tostring(root), scenes), effects)
    actual = compile_configuration(merged)
    assert [value.target_index for value in actual.scenes[0].values] == [0]
    assert [step.target_index for step in actual.effects[0].steps] == [0]


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "invalid_color", "too_many"]
)
def test_compile_fails_before_transport_on_invalid_configuration(change):
    root = ET.fromstring(DATA)
    if change == "missing":
        root.remove(root.find("templates"))
    elif change == "duplicate":
        root.find("effects").append(deepcopy(root.find("effects/Effect")))
    elif change == "invalid_color":
        root.find("scenes/Scene/SceneItem").set("color", "nan;1;1")
    else:
        step = root.find("effects/Effect/EffectStep")
        for _ in range(501):
            root.find("effects/Effect").append(deepcopy(step))
    with pytest.raises(DaliCommissioningError):
        compile_configuration(ET.tostring(root))


async def test_effect_write_read_round_trip_and_capacity(monkeypatch):
    async def no_delay(_):
        pass

    monkeypatch.setattr("xknxeditor.dali.effects.asyncio.sleep", no_delay)
    programmer = FakeProgrammer()
    connection = MdtDaliConnection(programmer, profile=PROFILES[2])
    commissioner = MdtDaliCommissioner(connection)
    programmer.set_read(PropertyType.COMMAND, b"\x00\x00\x00")
    effects = [Effect(15, 1, [EffectStep(79, 128, bytes([1, 2, 3, 4]), 4, 3, 14)])]
    await commissioner.write_effects(effects)
    assert programmer.writes[-1]["data"] == bytes(
        [DeviceCommand.UPDATE_EFFECT_TABLE, 0, 0]
    )
    data, count, modes = effect_plan(effects)
    assert count == 2 and data[:10] == bytes([15, 79, 128, 1, 2, 3, 4, 4, 3, 14])
    programmer.set_read(PropertyType.EFFECT_STEP, data)
    programmer.set_read(PropertyType.EFFECT_MODE, modes)
    assert (await commissioner.read_effects())[15] == effects[0]
    effects[0].steps *= 500
    assert effect_plan(effects)[1] == 500
    before = len(programmer.writes)
    effects[0].steps.append(effects[0].steps[0])
    with pytest.raises(DaliCommissioningError, match="capacity"):
        await commissioner.write_effects(effects)
    assert len(programmer.writes) == before
    with pytest.raises(DaliCommissioningError, match="unsupported"):
        await MdtDaliCommissioner(
            MdtDaliConnection(programmer, profile=PROFILES[0])
        ).write_effects([])


async def test_template_read_roundtrip_and_single_slot_test(monkeypatch):
    async def no_delay(_):
        pass

    monkeypatch.setattr("xknxeditor.dali.commissioning.asyncio.sleep", no_delay)
    programmer = FakeProgrammer()
    programmer.set_read(PropertyType.COMMAND, bytes(3))
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[2])
    )
    template = Template(
        15,
        mode=2,
        overwritable=True,
        groups=(15,),
        ecgs=(63,),
        commands=[TemplateCommand(4, 23, 59, bytes.fromhex("b80b0000"), 14, 31)],
        reset_on_midnight=False,
    )
    await commissioner.write_templates([template])
    for prop in [PropertyType.COLOUR_C_EVENT, PropertyType.COLOUR_C_ASSIGN]:
        programmer.set_read(prop, programmer.writes_for(prop)[0]["data"])
    assert (await commissioner.read_templates())[15] == template
    await commissioner.test_template_command(template, 0)
    assert programmer.writes[-2]["start_index"] == 16
    assert programmer.writes[-2]["count"] == 1
    assert programmer.writes[-1]["data"] == bytes.fromhex("04b80b0000000f00")


@pytest.mark.parametrize("group", [0, 15])
async def test_template_group_test_does_not_change_saved_members(group):
    programmer = FakeProgrammer()
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[2])
    )
    template = Template(
        4, commands=[TemplateCommand(4, 7, 15, bytes.fromhex("b80b0000"))]
    )
    await commissioner.test_template_command(template, 0, group=group)
    assert len(programmer.writes) == 1
    assert programmer.writes_for(PropertyType.EXT_COMMAND)[0]["data"] == (
        bytes.fromhex("04b80b000002") + bytes([group, 0])
    )
    assert not programmer.writes_for(PropertyType.COLOUR_C_ASSIGN)


@pytest.mark.parametrize(
    "group,func,row", [(-1, 4, 0), (16, 4, 0), (0, 17, 0), (0, 18, 0), (0, 4, 1)]
)
async def test_invalid_template_group_test_never_writes(group, func, row):
    programmer = FakeProgrammer()
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[2])
    )
    with pytest.raises(DaliCommissioningError):
        await commissioner.test_template_command(
            Template(0, commands=[TemplateCommand(func, 0, 0)]), row, group=group
        )
    assert not programmer.writes


@pytest.mark.parametrize(
    "func,value,expected",
    [
        (17, bytes([3, 0, 0, 0]), bytes([7, 3, 0])),
        (18, bytes([5, 1, 0, 0]), bytes([15, 5, 1])),
    ],
)
async def test_template_calls_use_direct_scene_and_effect_commands(
    func, value, expected
):
    programmer = FakeProgrammer()
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[2])
    )
    await commissioner.test_template_command(
        Template(0, commands=[TemplateCommand(func, 0, 0, value)]), 0
    )
    assert programmer.writes[-1]["data"] == expected
    assert len(programmer.writes) == 1


async def test_input_range_status_and_identify():
    programmer = FakeProgrammer()
    table = bytearray(b"\xff" * 384)
    table[12:18] = bytes.fromhex("12345601c800")
    programmer.set_read(PropertyType.INPUT_TABLE, bytes(table))
    programmer.set_read(
        PropertyType.INSTANCE_TABLE,
        b"\xff" * 200 * 8 + bytes([2, 4, 7, 10, 0, 1, 17, 0]),
    )
    programmer.set_read(
        PropertyType.INPUT_STATUS, bytes(200 * 5) + bytes([1, 1, 244, 0, 0])
    )
    programmer.set_read(PropertyType.COMMAND, bytes(3))
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[2])
    )
    inputs = await commissioner.read_inputs()
    assert (
        len(inputs) == 1
        and inputs[0].address == 2
        and inputs[0].long_address == 0x123456
    )
    assert inputs[0].instances[0].index == 200 and inputs[0].instances[0].value == 500
    assert (PropertyType.INSTANCE_TABLE, 201, 1) in programmer.reads
    await commissioner.identify_input(2)
    assert programmer.writes[-1]["data"] == bytes([51, 2, 1])
    before = len(programmer.writes)
    with pytest.raises(DaliCommissioningError):
        await commissioner.identify_input(64)
    assert len(programmer.writes) == before


def test_input_reassignment_does_not_erase_another_new_link():
    root = ET.fromstring(
        """<device><inputs><DevInput index="0" start-index="0" noi="2"><Instances><DevInputInstance index="0" number="0" type="0" ets-index="2" ets-detail="0"/><DevInputInstance index="1" number="1" type="0" ets-index="10" ets-detail="0"/></Instances></DevInput></inputs><gis><GenInput index="0"><Instances><EtsInstance index="0" type="0" ref-idx="0" origin="1"/></Instances></GenInput><GenInput index="1"><Instances><EtsInstance index="0" type="0" ref-idx="1" origin="0"/></Instances></GenInput></gis></device>"""
    )
    assert inputs_from_xml(root)[:6] == bytes([2, 0, 1, 10, 0, 1])


@pytest.mark.parametrize(
    "filename",
    [
        "mdt_dali_backup_simple.dat",
        "mdt_dali_backup_store.dat",
        "mdt_dali_backup_ch1.dat",
        "mdt_dali_backup_ch0_colour.dat",
        "theben_e64pro_backup.dat",
        "theben_e64pro_backup2.dat",
        "ipas_e64pro_backup.dat",
    ],
)
def test_all_captured_configurations_compile(filename):
    config = compile_configuration(
        (Path(__file__).parent / "fixtures" / filename).read_bytes()
    )
    assert len(config.scenes) == 16 and len(config.templates) == 16


async def test_full_configuration_readback_and_failure(monkeypatch):
    async def no_delay(_):
        pass

    monkeypatch.setattr("xknxeditor.dali.commissioning.asyncio.sleep", no_delay)

    class Gateway(FakeProgrammer):
        def __init__(self):
            super().__init__()
            self.damage = False

        async def write_property(
            self, object_index, property_id, data, *, count=1, start_index=1
        ):
            result = await super().write_property(
                object_index, property_id, data, count=count, start_index=start_index
            )
            if property_id != PropertyType.COMMAND and start_index != 0:
                self.set_read(property_id, data)
            if property_id == PropertyType.SCENE_GROUP_VALUE and self.damage:
                altered = bytearray(data)
                altered[2] ^= 1
                self.set_read(property_id, bytes(altered))
            return result

    programmer = Gateway()
    programmer.element_sizes[PropertyType.GROUP_STATUS] = 10
    for prop, size, count in [
        (PropertyType.EVG_PROP, 4, 64),
        (PropertyType.EVG_STATIC, 8, 64),
        (PropertyType.EVG_STATUS, 5, 64),
        (PropertyType.GROUP_DYN, 6, 16),
        (PropertyType.GROUP_STATUS, 10, 16),
        (PropertyType.GROUP_FAILURE, 6, 16),
        (PropertyType.INPUT_TABLE, 6, 64),
    ]:
        programmer.set_read(prop, bytes(size * count))
    programmer.set_read(PropertyType.SCENE_ASSIGN, bytes(16))
    programmer.set_read(213, bytes(16))
    programmer.set_read(PropertyType.SCENE_GROUP_VALUE, b"\xff" * 8)
    programmer.set_read(PropertyType.COMMAND, bytes(3))
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[2])
    )
    await commissioner.write_configuration(compile_configuration(DATA))
    assert [w["data"][0] for w in programmer.writes_for(PropertyType.COMMAND)] == [
        25,
        18,
        27,
        50,
    ]
    programmer.damage = True
    with pytest.raises(DaliCommissioningError, match="scene readback"):
        await commissioner.write_configuration(compile_configuration(DATA))

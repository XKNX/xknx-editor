"""Byte/shape tests for the MDT DALI ``DaliGC16-Backup-Store`` serializer.

Verifies the document invariants:
    - UTF-16LE encoding, no XML declaration, no BOM
    - fixed container counts (16 groups, 64 devecgs, 64 ecgs, 16 scenes, 16 templates)
    - attribute shapes (hex long address, "True"/"False" booleans, raw-integer fade time)
    - channel-N BinaryData name suffix
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from xknxeditor.dali.model import (
    MAX_ECG,
    MAX_GROUP,
    MAX_SCENES,
    DaliBusState,
    EcgState,
    Scene,
    SceneValue,
    Template,
    TemplateCommand,
)
from xknxeditor.dali.project_persistence import (
    BACKUP_STORE_BASE_NAME,
    backup_store_name,
    build_backup_store_xml,
)


def _parse(payload: bytes) -> ET.Element:
    text = payload.decode("utf-16-le")
    return ET.fromstring(text)


def _empty_state(channel: int = 0) -> DaliBusState:
    return DaliBusState(channel=channel)


def test_backup_store_name_channel_suffix() -> None:
    assert backup_store_name(0) == BACKUP_STORE_BASE_NAME
    assert backup_store_name(1) == f"{BACKUP_STORE_BASE_NAME}.1"
    assert backup_store_name(3) == f"{BACKUP_STORE_BASE_NAME}.3"


def test_payload_is_utf16_le_without_declaration_or_bom() -> None:
    payload = build_backup_store_xml(
        state=_empty_state(), individual_address="1.1.5", project_name="P"
    )
    # UTF-16LE encoding of "<device" starts with "<" (0x3C 0x00).
    assert payload[:2] == b"<\x00"
    assert not payload.startswith(b"\xff\xfe") and not payload.startswith(b"\xfe\xff")
    text = payload.decode("utf-16-le")
    assert not text.startswith("<?xml")


def test_root_header_attributes() -> None:
    payload = build_backup_store_xml(
        state=_empty_state(),
        individual_address="1.1.5",
        project_name="MyProj",
        can_restore=False,
        dca_version="3.9.2.0",
    )
    root = _parse(payload)
    assert root.tag == "device"
    assert root.attrib == {
        "CanRestore": "False",
        "IndividualAddress": "1.1.5",
        "Project": "MyProj",
        "type": "gc16",
        "version": "3.9.2.0",
    }


def test_container_counts_are_fixed() -> None:
    payload = build_backup_store_xml(state=_empty_state(), individual_address="1.1.5")
    root = _parse(payload)
    assert len(root.findall("groups/Group")) == MAX_GROUP == 16
    assert len(root.findall("devecgs/DevECG")) == MAX_ECG == 64
    assert len(root.findall("ecgs/ECG")) == MAX_ECG == 64
    assert len(root.findall("scenes/Scene")) == MAX_SCENES == 16


def test_container_order_is_groups_devecgs_ecgs_scenes_templates() -> None:
    payload = build_backup_store_xml(state=_empty_state(), individual_address="1.1.5")
    root = _parse(payload)
    assert [el.tag for el in root] == [
        "groups",
        "devecgs",
        "ecgs",
        "scenes",
        "templates",
    ]


def test_templates_omitted_when_empty_and_appended_last_when_present() -> None:
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
                weekdays=0b0111111,
            )
        ],
    )
    with_tpl = build_backup_store_xml(
        state=_empty_state(),
        templates=[tpl],
        individual_address="1.1.5",
    )
    without_tpl = build_backup_store_xml(
        state=_empty_state(), individual_address="1.1.5"
    )
    # ``<templates>`` is always emitted on MDT (fixed 16 slots). An empty-template call still
    # produces the container with all 16 default slots.
    assert _parse(with_tpl).find("templates") is not None
    assert _parse(without_tpl).find("templates") is not None
    assert len(_parse(without_tpl).findall("templates/Tpl")) == 16
    root = _parse(with_tpl)
    assert [el.tag for el in root] == [
        "groups",
        "devecgs",
        "ecgs",
        "scenes",
        "templates",
    ]
    cmd = root.find("templates/Tpl[@index='0']/Commands/TplCmd")
    assert cmd is not None
    assert cmd.attrib["func"] == "ColourTemp"
    assert cmd.attrib["time"] == "06:30"
    assert cmd.attrib["value"] == "17;34;51;68"
    assert cmd.attrib["fadeIn"] == "CFadeIn2"  # colour func -> C prefix
    # MDT weekdays is a 7-char "1"/"0" string, bit0 = Monday.
    assert cmd.attrib["weekdays"] == "1111110"


def test_tpl_value_func_uses_v_fade_prefix() -> None:
    tpl = Template(
        index=0,
        mode=0,
        groups=(),
        ecgs=(),
        commands=[
            TemplateCommand(
                func=0, hour=12, minute=0, value=b"\x05\x00\x00\x00", fade_time=3
            )
        ],
    )
    payload = build_backup_store_xml(
        state=_empty_state(), templates=[tpl], individual_address="1.1.5"
    )
    cmd = _parse(payload).find("templates/Tpl/Commands/TplCmd")
    assert cmd is not None
    assert cmd.attrib["fadeIn"] == "VFadeIn3"


def test_devecg_long_address_is_uppercase_6_hex() -> None:
    ecg = EcgState(slot=0, present=True, ecg_type=0, long_address=0x1A2B3C)
    state = DaliBusState(channel=0, ecgs=[ecg])
    payload = build_backup_store_xml(state=state, individual_address="1.1.5")
    first = _parse(payload).find("devecgs/DevECG[@index='0']")
    assert first is not None
    assert first.attrib["long-address"] == "1A2B3C"
    assert first.attrib["short-address"] == "0"


def test_devecg_absent_slot_defaults() -> None:
    payload = build_backup_store_xml(state=_empty_state(), individual_address="1.1.5")
    first = _parse(payload).find("devecgs/DevECG[@index='0']")
    assert first is not None
    assert first.attrib["groupindex"] == "255"
    assert first.attrib["short-address"] == "255"
    assert first.attrib["long-address"] == "FFFFFF"
    assert first.attrib["type"] == "-1"


def test_scene_fade_name_and_item_targets() -> None:
    scene = Scene(
        index=0,
        fade_time=3,
        knx_scene=7,
        values=[
            SceneValue(scene=0, target_index=0, level=200),  # group target
            SceneValue(scene=0, target_index=16, level=128),  # ECG target (index 0)
            SceneValue(
                scene=0, target_index=5, level=255, color=b"\x11\x22\x33\x44"
            ),  # coloured group
        ],
    )
    payload = build_backup_store_xml(
        state=_empty_state(), scenes=[scene], individual_address="1.1.5"
    )
    first_scene = _parse(payload).find("scenes/Scene[@index='0']")
    assert first_scene is not None
    # MDT stores fadeTime as the raw integer, not as the "CFadeInN" enum name.
    assert first_scene.attrib["fadeTime"] == "3"
    assert first_scene.attrib["knxScene"] == "7"
    assert first_scene.attrib["isDirty"] == "False"
    items = first_scene.findall("SceneItem")
    assert len(items) == 3
    assert items[0].attrib["itemtype"] == "Dali.DcaApp.Group"
    assert items[0].attrib["itemindex"] == "0"
    assert items[1].attrib["itemtype"] == "Dali.DcaApp.ECG"
    assert items[1].attrib["itemindex"] == "0"
    assert items[2].attrib["color"] == "17;34;51;68"
    assert items[2].attrib["color-type"] == "2"


def test_templates_always_16_slots_even_when_empty() -> None:
    payload = build_backup_store_xml(state=_empty_state(), individual_address="1.1.5")
    tpls = _parse(payload).findall("templates/Tpl")
    # Verified against the real MDT .knxproj export: all 16 slots are materialised.
    assert len(tpls) == 16
    for i, tpl in enumerate(tpls):
        assert tpl.attrib["index"] == str(i)
        assert tpl.attrib["mode"] == "Disabled"
        assert tpl.attrib["isOverwritable"] == "False"
        assert tpl.find("Members") is not None
        assert tpl.find("Commands") is not None


def test_populated_template_slot_keeps_members_and_commands() -> None:
    tpl = Template(
        index=2,
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
                weekdays=0b0111111,
            )
        ],
    )
    payload = build_backup_store_xml(
        state=_empty_state(),
        templates=[tpl],
        individual_address="1.1.5",
    )
    root = _parse(payload)
    all_tpls = root.findall("templates/Tpl")
    assert len(all_tpls) == 16
    populated = root.find("templates/Tpl[@index='2']")
    assert populated is not None
    assert populated.attrib["mode"] == "Enabled"
    cmd = populated.find("Commands/TplCmd")
    assert cmd is not None
    assert cmd.attrib["func"] == "ColourTemp"
    assert cmd.attrib["time"] == "06:30"
    assert cmd.attrib["value"] == "17;34;51;68"
    assert cmd.attrib["fadeIn"] == "CFadeIn2"
    assert cmd.attrib["weekdays"] == "1111110"


def test_commissioning_flag_derived_from_presence_and_binding() -> None:
    state = DaliBusState(
        channel=0,
        ecgs=[
            EcgState(slot=0, present=True, ecg_type=0, ets_ecg_index=5),
            EcgState(slot=1, present=True, ecg_type=0),  # present but unassigned
            EcgState(slot=2, present=False, ecg_type=0),  # absent
        ],
    )
    payload = build_backup_store_xml(state=state, individual_address="1.1.5")
    ecgs = {el.attrib["index"]: el for el in _parse(payload).findall("ecgs/ECG")}
    assert ecgs["0"].attrib["flags"] == "2"  # assigned
    assert ecgs["1"].attrib["flags"] == "1"  # not assigned
    assert ecgs["2"].attrib["flags"] == "0"  # not present

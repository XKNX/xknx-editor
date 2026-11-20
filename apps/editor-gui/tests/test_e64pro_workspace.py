from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from editor_gui.plugin_blob_readers.dali_backup.e64pro import (
    Edit,
    apply,
    document,
    rows,
)

FIXTURE = (
    next(p for p in Path(__file__).parents if (p / "packages/dali").exists())
    / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat"
)


def test_effect_order_and_unknown_content():
    root = ET.fromstring(FIXTURE.read_bytes())
    steps = root.findall("./effects/Effect")[0]
    first, second = list(steps)[:2]
    first.set("future", "retain")
    first.append(ET.Element("Extension", payload="unchanged"))
    before = ET.tostring(root, encoding="utf-16le")
    after = apply(before, Edit("effects", "0", "down", 0))
    effect = document(after).findall("./effects/Effect")[0]
    assert effect[0].attrib == second.attrib
    assert ET.tostring(effect[1]) == ET.tostring(first)
    assert effect.get("isDirty") == "True"
    assert apply(after, Edit("effects", "0", "up", 0)) == after


def test_scene_members_and_keep_bits():
    data = FIXTURE.read_bytes()
    data = apply(data, Edit("scenes", "0", "add", target="ecgs", index="5"))
    with pytest.raises(ValueError, match="already"):
        apply(data, Edit("scenes", "0", "add", target="ecgs", index="5"))
    for field, expected in [("keepColor", "1"), ("keepValue", "3")]:
        data = apply(data, Edit("scenes", "0", "set", 0, field, "True"))
        assert (
            document(data).findall("./scenes/Scene/SceneItem")[0].get("setting")
            == expected
        )
    with pytest.raises(ValueError):
        apply(data, Edit("scenes", "0", "set", 0, "value", "101"))
    data = apply(data, Edit("scenes", "0", "remove", 3))
    assert len(document(data).findall("./scenes/Scene")[0]) == 3


def test_template_weekday_copy_and_members():
    original = FIXTURE.read_bytes()
    data = apply(original, Edit("templates", "0", "copy", 0))
    data = apply(data, Edit("templates", "0", "set", 1, "weekdays", "0111111"))
    data = apply(
        data,
        Edit("templates", "0", "member", value="False", target="groups", index="0"),
    )
    slot = document(data).findall("./templates/Tpl")[0]
    commands = rows(slot, "templates")
    assert [cmd.get("weekdays") for cmd in commands[:2]] == ["1111111", "0111111"]
    assert commands[0].get("time") == commands[1].get("time")
    assert "0" not in [m.get("itemindex") for m in slot.findall("Members/TplItem")]
    for bad_time in ("24:00", "07:60", "7:15"):
        with pytest.raises(ValueError, match="HH:MM"):
            apply(data, Edit("templates", "0", "set", 1, "time", bad_time))


def test_unknown_device_and_noop():
    with pytest.raises(ValueError):
        document(b'<device type="gc16"/>')
    data = FIXTURE.read_bytes()
    assert (
        apply(data, Edit("scenes", "0", "header", field="knxScene", value="1")) is data
    )
    with pytest.raises(ValueError):
        apply(data, Edit("templates", "0", "set", 99, "time", "12:00"))

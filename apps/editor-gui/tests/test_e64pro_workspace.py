from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from editor_gui.plugin_blob_readers.dali_backup.e64pro import (
    Edit,
    apply,
    document,
    export_configuration,
    import_configuration,
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


@pytest.mark.parametrize(
    "section,slot_tag,row_tag",
    [
        ("scenes", "Scene", "SceneItem"),
        ("effects", "Effect", "EffectStep"),
    ],
)
@pytest.mark.parametrize("target,target_tag", [("groups", "Group"), ("ecgs", "ECG")])
@pytest.mark.parametrize(
    "ctype,mode,value",
    [
        ("10", "8", "255;120;57;121"),
        ("11", "4", "255;120;57;0"),
        ("12", "16", "0.5;0.6;0.7"),
        ("13", "32", "0.5;0.6;0.7;121"),
    ],
)
@pytest.mark.parametrize("flags", [0, 3, 131])
def test_colour_switch_updates_alternate_bit(
    section, slot_tag, row_tag, target, target_tag, ctype, mode, value, flags
):
    data = f"""<device type="e64pro">
      <{target}><{target_tag} index="0" ctype="{ctype}" /></{target}>
      <{section}><{slot_tag} index="0" isDirty="False">
        <{row_tag} itemtype="Dalie64.DcaApp.{target_tag}" itemindex="0"
          color-type="2" color="3000" setting="{flags}" future="unchanged" />
      </{slot_tag}></{section}>
    </device>""".encode()
    path = f"{section}/{slot_tag}/{row_tag}"
    original = ET.tostring(document(data))
    changed = apply(data, Edit(section, "0", "colour", 0, mode, value))
    item = document(changed).find(path)
    assert item is not None
    assert item.get("color-type") == mode
    assert item.get("color") == value
    assert item.get("setting") == str(flags | 4)
    assert item.get("future") == "unchanged"
    assert document(changed).find(f"{section}/{slot_tag}").get("isDirty") == "True"
    changed = apply(changed, Edit(section, "0", "colour", 0, "2", "3200"))
    assert document(changed).find(path).get("setting") == str(flags)
    assert document(changed).find(path).get("color") == "3200"
    assert ET.tostring(document(data)) == original


@pytest.mark.parametrize(
    "section,slot_tag,row_tag",
    [
        ("scenes", "Scene", "SceneItem"),
        ("effects", "Effect", "EffectStep"),
    ],
)
@pytest.mark.parametrize("action", ["colour", "set", "details"])
@pytest.mark.parametrize("ctype,flags,expected", [("10", 3, 7), ("6", 7, 3)])
def test_colour_edit_repairs_mode_flag(
    section, slot_tag, row_tag, action, ctype, flags, expected
):
    data = f"""<device type="e64pro">
      <groups><Group index="0" ctype="{ctype}" /></groups>
      <{section}><{slot_tag} index="0">
        <{row_tag} itemtype="Dalie64.DcaApp.Group" itemindex="0"
          color-type="8" color="1;2;3;4" setting="{flags}" />
      </{slot_tag}></{section}>
    </device>""".encode()
    if action == "details":
        from editor_gui.plugin_blob_readers.dali_backup import apply_edit

        ref = f"dali_backup:Dalie64-Backup-Store:{section}:0:leaf:0:color"
        with pytest.raises(ValueError):
            apply_edit(data, ref, "256;3;2;1")
        changed = apply_edit(data, ref, "4;3;2;1")
    else:
        changed = apply(
            data,
            Edit(
                section,
                "0",
                action,
                0,
                "8" if action == "colour" else "color",
                "4;3;2;1",
            ),
        )
    assert document(changed).find(f"{section}/{slot_tag}/{row_tag}").get(
        "setting"
    ) == str(expected)
    assert document(changed).find(f"{section}/{slot_tag}").get("isDirty") == "True"


def _capacity_payload(section: str, count: int) -> bytes:
    root = ET.fromstring(FIXTURE.read_bytes())
    slots = root.findall(
        "templates/Tpl" if section == "templates" else "effects/Effect"
    )
    for slot in slots:
        for row in rows(slot, section):
            (slot.find("Commands") if section == "templates" else slot).remove(row)
    for n in range(count):
        slot = slots[n % 2]
        if section == "templates":
            parent = slot.find("Commands")
            if parent is None:
                parent = ET.SubElement(slot, "Commands")
            ET.SubElement(
                parent,
                "TplCmd",
                func="SetValue",
                value="0",
                time="00:00",
                weekdays="1111111",
                fadeIn="VFadeIn0",
            )
        else:
            ET.SubElement(
                slot,
                "EffectStep",
                itemtype="Dalie64.DcaApp.Group",
                itemindex="0",
                value="0",
                setting="0",
                fadetime="0",
                delay="0",
            )
    return ET.tostring(root, encoding="utf-8")


@pytest.mark.parametrize(
    "section,limit,action",
    [
        ("templates", 300, "add"),
        ("templates", 300, "copy"),
        ("effects", 500, "add"),
    ],
)
def test_global_capacity_allows_last_row_and_removal(section, limit, action):
    data = _capacity_payload(section, limit - 1)
    edit = Edit(section, "0", action, row=0, target="groups", index="0")
    full = apply(data, edit)
    path = (
        "templates/Tpl/Commands/TplCmd"
        if section == "templates"
        else "effects/Effect/EffectStep"
    )
    assert len(document(full).findall(path)) == limit
    with pytest.raises(ValueError, match=f"At most {limit}"):
        apply(full, edit)
    assert len(document(full).findall(path)) == limit
    updated = apply(full, Edit(section, "0", "set", 0, "value", "25"))
    assert document(updated).findall(path)[0].get("value") == "25"
    reduced = apply(updated, Edit(section, "0", "remove", 0))
    assert len(document(apply(reduced, edit)).findall(path)) == limit


@pytest.mark.parametrize("section,limit", [("templates", 300), ("effects", 500)])
def test_import_export_capacity_and_repair(section, limit):
    data = _capacity_payload(section, limit + 1)
    with pytest.raises(ValueError, match=f"At most {limit}"):
        import_configuration(data, "1.1.1", "Project")
    with pytest.raises(ValueError, match=f"At most {limit}"):
        export_configuration(data)
    repaired = apply(data, Edit(section, "0", "remove", 0))
    imported = import_configuration(repaired, "1.1.1", "Project")
    assert document(export_configuration(imported)).get("IndividualAddress") == "1.1.1"


@pytest.mark.parametrize("section", ["scenes", "effects", "templates"])
def test_description_limit_in_workspace_and_details(section):
    from editor_gui.plugin_blob_readers.dali_backup import apply_edit

    maximum = 60
    data = FIXTURE.read_bytes()
    valid = "ä" * maximum
    updated = apply(
        data, Edit(section, "0", "header", field="description", value=valid)
    )
    ref = f"dali_backup:Dalie64-Backup-Store:{section}:0:description"
    assert apply_edit(updated, ref, "b" * maximum)
    for value in ("x" * (maximum + 1), "\U0001f600" * (maximum // 2 + 1)):
        with pytest.raises(ValueError, match=str(maximum)):
            apply(
                updated, Edit(section, "0", "header", field="description", value=value)
            )
        with pytest.raises(ValueError, match=str(maximum)):
            apply_edit(updated, ref, value)


def test_shared_parser_preserves_extensions_and_leaf_addresses():
    from editor_gui.plugin_blob_readers.dali_backup import apply_edit, read

    data = b"""<?xml version="1.0" encoding="utf-8"?><device type="e64pro">
      <scenes><Scene index="0" description="Scene">
        <!-- extension --><?vendor retained?>
        <SceneItem itemtype="Dalie64.DcaApp.Group" itemindex="0" value="20" />
      </Scene></scenes>
    </device>"""
    sections = read(data)
    value = next(
        p
        for section in sections
        for p in section.parameters
        if p.ref_id.endswith(":leaf:0:value")
    )
    changed = apply_edit(data, value.ref_id, "40")
    text = changed.decode("utf-16le")
    assert "<!-- extension -->" in text
    assert "<?vendor retained?>" in text
    assert document(changed).find("scenes/Scene/SceneItem").get("value") == "40"


@pytest.mark.parametrize(
    "section,tag",
    [
        ("groups", "Group"),
        ("ecgs", "ECG"),
        ("mds", "Sensor"),
        ("pbs", "Button"),
        ("gis", "Input"),
        ("inputs", "Input"),
    ],
)
def test_details_description_limit_for_controller_items(section, tag):
    from editor_gui.plugin_blob_readers.dali_backup import apply_edit

    data = (
        f'<device type="e64pro"><{section}>'
        f'<{tag} index="0" description="Existing long imported description" />'
        f"</{section}></device>"
    ).encode()
    ref = f"dali_backup:Dalie64-Backup-Store:{section}:0:description"
    assert apply_edit(data, ref, "Existing long imported description") == data
    changed = apply_edit(data, ref, "a" * 60)
    assert document(changed).find(f"{section}/{tag}").get("description") == "a" * 60
    with pytest.raises(ValueError, match="60"):
        apply_edit(data, ref, "b" * 61)


def test_dca_errors_are_translated():
    from editor_gui import strings

    strings.set_locale("de")
    try:
        with pytest.raises(ValueError, match="Ungültige DCA-Konfiguration"):
            document(b"invalid")
        with pytest.raises(ValueError, match="Erwartet: HH:MM"):
            apply(
                FIXTURE.read_bytes(), Edit("templates", "0", "set", 0, "time", "25:00")
            )
    finally:
        strings.set_locale("en")

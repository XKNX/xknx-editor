from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from editor_gui.plugin_blob_readers.dali_backup import e64pro as dca

DATA = (
    Path(__file__).parents[3] / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat"
).read_bytes()


@pytest.mark.parametrize("section", dca.SLOTS)
def test_slot_export_import_preserves_other_slots_and_unknown_fields(section):
    root = deepcopy(dca.document(DATA))
    source = dca.slot_element(root, section, "0")
    source.set("extension", "keep")
    ET.SubElement(source, "Extension", {"value": "preserved"})
    exported = dca.export_slot(ET.tostring(root), section, "0")
    assert exported.startswith(b"<?xml")
    imported = ET.fromstring(exported)
    assert imported.tag == dca.SLOTS[section]
    result = dca.document(
        dca.apply(DATA, dca.Edit(section, "0", "import", value=exported.decode()))
    )
    slot = dca.slot_element(result, section, "0")
    assert slot.get("extension") == "keep"
    assert slot.find("Extension").get("value") == "preserved"
    assert slot.get("isDirty") == "True"
    original = dca.document(DATA)
    for container in original:
        if container.tag != section:
            assert ET.tostring(result.find(container.tag)) == ET.tostring(container)


@pytest.mark.parametrize("section", dca.SLOTS)
def test_import_different_index_keeps_destination_header(section):
    root = deepcopy(dca.document(DATA))
    source = dca.slot_element(root, section, "0")
    source.set("index", "31")
    source.set("description", "Incoming")
    old = dca.slot_element(root, section, "1")
    old.set("description", "Destination")
    if section == "scenes":
        old.set("knxScene", "60")
    if section == "effects":
        old.set("mode", "1")
    result = dca.document(
        dca.apply(
            ET.tostring(root),
            dca.Edit(
                section, "1", "import", value=ET.tostring(source, encoding="unicode")
            ),
        )
    )
    changed = dca.slot_element(result, section, "1")
    assert changed.get("index") == "1"
    assert changed.get("description") == (
        "Incoming" if section == "templates" else "Destination"
    )
    if section == "scenes":
        assert changed.get("knxScene") == "60"
        assert changed.get("fadeTime") == source.get("fadeTime")
    if section == "effects":
        assert changed.get("mode") == "1"


@pytest.mark.parametrize("change", ["target", "value", "colour", "duplicate", "root"])
def test_invalid_scene_import_rejected_without_mutating_snapshot(change):
    source = ET.fromstring(dca.export_slot(DATA, "scenes", "0"))
    row = source.find("SceneItem")
    if change == "target":
        row.set("itemindex", "999")
    elif change == "value":
        row.set("value", "101")
    elif change == "colour":
        row.set("color", "not a number")
    elif change == "duplicate":
        source.append(deepcopy(row))
    else:
        source.tag = "Effect"
    original = ET.tostring(dca.document(DATA))
    with pytest.raises(ValueError):
        dca.apply(
            DATA,
            dca.Edit(
                "scenes", "0", "import", value=ET.tostring(source, encoding="unicode")
            ),
        )
    assert ET.tostring(dca.document(DATA)) == original


@pytest.mark.parametrize(("section", "limit"), [("effects", 500), ("templates", 300)])
def test_import_slot_respects_global_capacity(section, limit):
    root = deepcopy(dca.document(DATA))
    source = dca.slot_element(root, section, "0")
    row = dca.rows(source, section)[0]
    parent = source.find("Commands") if section == "templates" else source
    for old in dca.rows(source, section):
        parent.remove(old)
    for _ in range(limit + 1):
        parent.append(deepcopy(row))
    with pytest.raises(ValueError):
        dca.apply(
            DATA,
            dca.Edit(
                section, "0", "import", value=ET.tostring(source, encoding="unicode")
            ),
        )


@pytest.mark.parametrize("section", dca.SLOTS)
def test_single_import_checks_new_description_but_preserves_existing_long_text(
    section,
):
    maximum = 60
    xml = ET.fromstring(dca.export_slot(DATA, section, "0"))
    xml.set("description", "x" * (maximum + 1))
    edit = dca.Edit(section, "0", "import", value=ET.tostring(xml, encoding="unicode"))
    with pytest.raises(ValueError):
        dca.apply(DATA, edit)
    original = deepcopy(dca.document(DATA))
    dca.slot_element(original, section, "0").set("description", "x" * (maximum + 1))
    dca.apply(ET.tostring(original), edit)


@pytest.mark.parametrize("section", dca.SLOTS)
@pytest.mark.parametrize("name", ["x" * 60, "\U0001f4a1" * 30])
def test_description_edit_and_slot_exchange_preserve_full_length(section, name):
    data = dca.apply(
        DATA, dca.Edit(section, "0", "header", field="description", value=name)
    )
    exported = dca.export_slot(data, section, "0")
    restored = dca.apply(
        DATA, dca.Edit(section, "0", "import", value=exported.decode())
    )
    assert (
        dca.slot_element(dca.document(restored), section, "0").get("description")
        == name
    )
    with pytest.raises(ValueError):
        dca.apply(
            data,
            dca.Edit(section, "0", "header", field="description", value=name + "x"),
        )


@pytest.mark.parametrize("section", (*dca.DESCRIPTION_SECTIONS, "inputs"))
def test_bulk_description_limit_is_consistent(section):
    root = deepcopy(dca.document(DATA))
    container = root.find(section)
    if container is None:
        container = ET.SubElement(root, section)
    if not list(container):
        ET.SubElement(container, "DevInput", index="0")
    slot = next(iter(container)).get("index")
    data = dca.apply(
        ET.tostring(root), dca.Edit(section, slot, "description", value="x" * 60)
    )
    assert dca.document(data).find(section + "/*").get("description") == "x" * 60
    with pytest.raises(ValueError):
        dca.apply(data, dca.Edit(section, slot, "description", value="x" * 61))


@pytest.mark.parametrize("func", ["CallScene", "CallEffect"])
@pytest.mark.parametrize("index", ["0", "15"])
def test_template_calls_can_be_created_and_exchanged(func, index):
    data = dca.apply(DATA, dca.Edit("templates", "0", "set", 0, "func", func))
    data = dca.apply(data, dca.Edit("templates", "0", "set", 0, "value", index))
    scale = "ScaleNoneScene"
    if func == "CallEffect":
        scale = "ScaleNoneEffectStart"
        data = dca.apply(data, dca.Edit("templates", "0", "set", 0, "scaleMode", scale))
    single = dca.export_slot(data, "templates", "0")
    imported = dca.apply(
        DATA, dca.Edit("templates", "0", "import", value=single.decode())
    )
    command = dca.document(imported).find("templates/Tpl/Commands/TplCmd")
    assert (command.get("func"), command.get("value"), command.get("scaleMode")) == (
        func,
        index,
        scale,
    )
    assert dca.export_slot(imported, "templates", "0") == single
    for invalid in ("-1", "16", "abc"):
        with pytest.raises(ValueError):
            dca.apply(data, dca.Edit("templates", "0", "set", 0, "value", invalid))


@pytest.mark.parametrize("delay", ["0", "1", "2", "3", "4", "10", "20", "200"])
def test_all_original_effect_delays_can_be_exchanged(delay):
    data = dca.apply(DATA, dca.Edit("effects", "0", "set", 0, "delay", delay))
    imported = dca.apply(
        DATA,
        dca.Edit(
            "effects",
            "0",
            "import",
            value=dca.export_slot(data, "effects", "0").decode(),
        ),
    )
    assert (
        dca.document(imported).find("effects/Effect/EffectStep").get("delay") == delay
    )


def test_max_on_value_default_and_bounds_match_original():
    data = dca.apply(
        DATA, dca.Edit("templates", "0", "set", 0, "func", "SetMaxOnValue")
    )
    assert (
        dca.document(data).find("templates/Tpl/Commands/TplCmd").get("value") == "100"
    )
    for value in ("1", "100"):
        data = dca.apply(data, dca.Edit("templates", "0", "set", 0, "value", value))
    with pytest.raises(ValueError):
        dca.apply(data, dca.Edit("templates", "0", "set", 0, "value", "0"))
    slot = ET.fromstring(dca.export_slot(data, "templates", "0"))
    slot.find("Commands/TplCmd").set("value", "0")
    imported = dca.apply(
        DATA,
        dca.Edit(
            "templates", "0", "import", value=ET.tostring(slot, encoding="unicode")
        ),
    )
    assert (
        dca.document(imported).find("templates/Tpl/Commands/TplCmd").get("value")
        == "100"
    )


def test_detail_edits_share_command_defaults_and_validation():
    from editor_gui.plugin_blob_readers.dali_backup import apply_edit, nested_leaves

    slot = dca.slot_element(dca.document(DATA), "templates", "0")
    number = nested_leaves(slot).index(slot.find("Commands/TplCmd"))
    ref = f"dali_backup:Dalie64-Backup-Store:templates:0:leaf:{number}:"
    data = apply_edit(DATA, ref + "func", "SetMaxOnValue")
    command = dca.document(data).find("templates/Tpl/Commands/TplCmd")
    assert command.get("value") == "100" and command.get("scaleMode") == "ScaleNormal"
    with pytest.raises(ValueError):
        apply_edit(data, ref + "value", "0")
    data = apply_edit(data, ref + "func", "CallEffect")
    assert (
        dca.document(data).find("templates/Tpl/Commands/TplCmd").get("scaleMode")
        == "ScaleNoneEffectStop"
    )


@pytest.mark.parametrize("mode", ["Disabled", "Enabled", "Controlled"])
def test_template_mode_edit_and_slot_import(mode):
    data = dca.apply(
        DATA, dca.Edit("templates", "0", "header", field="mode", value=mode)
    )
    xml = dca.export_slot(data, "templates", "0")
    data = dca.apply(DATA, dca.Edit("templates", "0", "import", value=xml.decode()))
    assert dca.document(data).find("templates/Tpl").get("mode") == mode

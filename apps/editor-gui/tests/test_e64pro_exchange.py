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


@pytest.mark.parametrize(
    ("section", "maximum"), [("scenes", 20), ("effects", 20), ("templates", 60)]
)
def test_single_import_checks_new_description_but_preserves_existing_long_text(
    section, maximum
):
    xml = ET.fromstring(dca.export_slot(DATA, section, "0"))
    xml.set("description", "x" * (maximum + 1))
    edit = dca.Edit(section, "0", "import", value=ET.tostring(xml, encoding="unicode"))
    with pytest.raises(ValueError):
        dca.apply(DATA, edit)
    original = deepcopy(dca.document(DATA))
    dca.slot_element(original, section, "0").set("description", "x" * (maximum + 1))
    dca.apply(ET.tostring(original), edit)

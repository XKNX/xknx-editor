from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from editor_gui.plugin_blob_readers.dali_backup import e64pro as dca
from editor_gui.plugin_blob_readers.dali_backup.e64pro_descriptions import (
    ROOTS,
    export_descriptions,
)

DATA = (
    Path(__file__).parents[3] / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat"
).read_bytes()


@pytest.mark.parametrize("section", ROOTS)
@pytest.mark.parametrize("format", ["xml", "txt"])
def test_description_exchange_preserves_category(section, format):
    root = deepcopy(dca.document(DATA))
    items = root.find(section)
    if items is None:
        items = ET.SubElement(root, section)
    if not len(items):
        ET.SubElement(items, "Controller", index="0")
        ET.SubElement(items, "Controller", index="1")
    items[0].set("description", "Licht & Wärme <1>")
    items[1].set("description", "")
    data = ET.tostring(root)
    exported = export_descriptions(data, section, format)
    if format == "xml":
        xml = ET.fromstring(exported)
        assert xml.tag == ROOTS[section]
        assert xml[0].get("index") == "1"
    cleared = dca.apply(data, dca.Edit(section, "", "clear_descriptions"))
    imported = dca.apply(
        cleared,
        dca.Edit(
            section,
            "",
            "import_descriptions",
            field=format,
            value=exported.decode("utf-8-sig"),
        ),
    )
    assert ET.tostring(dca.document(imported)) == ET.tostring(root)


@pytest.mark.parametrize(
    "text",
    [
        '<ECG_TEXT><text index="1" description="wrong category"/></ECG_TEXT>',
        '<GRP_TEXT><text index="1"/><text index="1"/></GRP_TEXT>',
        '<GRP_TEXT><text index="0"/></GRP_TEXT>',
        '<GRP_TEXT><text index="1000"/></GRP_TEXT>',
        '<GRP_TEXT><text index="1" description="first"/><text index="2" description="'
        + "x" * 61
        + '"/></GRP_TEXT>',
        "<GRP_TEXT>",
    ],
)
def test_invalid_description_import_is_atomic(text):
    original = ET.tostring(dca.document(DATA))
    with pytest.raises(ValueError):
        dca.apply(
            DATA, dca.Edit("groups", "", "import_descriptions", field="xml", value=text)
        )
    assert ET.tostring(dca.document(DATA)) == original


def test_text_import_empty_lines_bom_and_partial_input():
    before = dca.document(DATA).find("groups")
    updated = dca.document(
        dca.apply(
            DATA,
            dca.Edit(
                "groups",
                "",
                "import_descriptions",
                field="txt",
                value="\ufeffFirst   \r\n''\n\n",
            ),
        )
    ).find("groups")
    assert [e.get("description", "") for e in list(updated)[:3]] == ["First", "", ""]
    assert ET.tostring(updated[3]) == ET.tostring(before[3])
    with pytest.raises(ValueError):
        dca.apply(
            DATA,
            dca.Edit(
                "groups", "", "import_descriptions", field="txt", value="x\n" * 17
            ),
        )


@pytest.mark.parametrize("value", ["''", "trailing ", "line\nbreak", "line\rbreak"])
def test_lossy_text_export_requires_xml(value):
    data = dca.apply(DATA, dca.Edit("groups", "0", "description", value=value))
    with pytest.raises(ValueError, match="XML"):
        export_descriptions(data, "groups", "txt")
    assert (
        ET.fromstring(export_descriptions(data, "groups"))[0].get("description")
        == value
    )


def test_template_insert_sort_clear_preserve_members_headers_and_extensions():
    root = deepcopy(dca.document(DATA))
    template = root.find("templates/Tpl")
    commands = template.find("Commands")
    commands.clear()
    for func, time in [
        ("ColourTemp", "08:00"),
        ("SetValue", "08:00"),
        ("SetValue", "07:00"),
    ]:
        ET.SubElement(
            commands, "TplCmd", func=func, time=time, value="100", weekdays="1111111"
        )
    extension = ET.SubElement(commands, "Extension", value="keep")
    before = ET.tostring(root)
    inserted = dca.apply(before, dca.Edit("templates", "0", "insert", row=1))
    rows = dca.rows(dca.document(inserted).find("templates/Tpl"), "templates")
    assert [(e.get("func"), e.get("time")) for e in rows[:3]] == [
        ("ColourTemp", "08:00"),
        ("SetValue", "00:00"),
        ("SetValue", "08:00"),
    ]
    for action in ["sort_time", "sort_function"]:
        sorted_data = dca.apply(before, dca.Edit("templates", "0", action))
        target = dca.document(sorted_data).find("templates/Tpl")
        assert [e.get("time") for e in dca.rows(target, "templates")] == [
            "07:00",
            "08:00",
            "08:00",
        ]
        assert [e.get("func") for e in dca.rows(target, "templates")] == [
            "SetValue",
            "SetValue",
            "ColourTemp",
        ]
        assert ET.tostring(target.find("Commands/Extension")) == ET.tostring(extension)
        assert dca.apply(sorted_data, dca.Edit("templates", "0", action)) == sorted_data
    cleared = dca.apply(before, dca.Edit("templates", "0", "clear"))
    target = dca.document(cleared).find("templates/Tpl")
    assert not dca.rows(target, "templates")
    assert target.get("isDirty") == "True"
    assert ET.tostring(target.find("Members")) == ET.tostring(template.find("Members"))
    assert ET.tostring(target.find("Commands/Extension")) == ET.tostring(extension)
    assert target.get("description") == template.get("description")


def test_template_insert_capacity_and_invalid_selection():
    for row in [-1, 999]:
        with pytest.raises(ValueError):
            dca.apply(DATA, dca.Edit("templates", "0", "insert", row=row))
    root = deepcopy(dca.document(DATA))
    for template in root.findall("templates/Tpl"):
        for row in template.findall("Commands/TplCmd"):
            template.find("Commands").remove(row)
    commands = root.find("templates/Tpl/Commands")
    for _ in range(300):
        ET.SubElement(commands, "TplCmd", func="SetValue", time="00:00")
    with pytest.raises(ValueError):
        dca.apply(ET.tostring(root), dca.Edit("templates", "0", "insert", row=0))

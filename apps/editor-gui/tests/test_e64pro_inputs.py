from xml.etree import ElementTree as ET

import pytest
from editor_gui.plugin_blob_readers.dali_backup import e64pro as dca
from editor_gui.plugin_blob_readers.dali_backup.e64pro_inputs import compatible


def configuration():
    return ET.fromstring("""<device type="e64pro"><gis>
      <GenInput index="0"><Instances><EtsInstance index="0" type="0" origin="-1" ref-idx="-1" flags="1"/></Instances></GenInput>
      <GenInput index="1"><Instances><EtsInstance index="0" type="0" origin="-1" ref-idx="-1" flags="1"/></Instances></GenInput>
    </gis><inputs><DevInput index="3" start-index="10" noi="2"><Instances>
      <DevInputInstance index="10" number="0" type="ValueInSensor" target="-1" ets-index="255" ets-detail="0"/>
      <DevInputInstance index="11" number="1" type="EventingSensor" target="-1" ets-index="255" ets-detail="0"/>
    </Instances></DevInput></inputs></device>""")


def apply(root, reference, slot="0"):
    data = dca.apply(
        ET.tostring(root), dca.Edit("gis", slot, "assign", row=0, index=reference)
    )
    return ET.fromstring(data.decode("utf-16le"))


def test_assignment_reassignment_unlink_preserves_programmed_origin():
    root = configuration()
    first = apply(root, "10")
    logical = first.find("gis/GenInput/Instances/EtsInstance")
    assert logical.attrib == dict(
        index="0", type="0", origin="-1", **{"ref-idx": "10", "flags": "3"}
    )
    physical = first.find("inputs/DevInput/Instances/DevInputInstance")
    assert (
        physical.get("ets-index"),
        physical.get("ets-detail"),
        physical.get("target"),
    ) == ("2", "0", "-1")
    logical.set("origin", "10")
    logical.set("flags", "4")
    physical.set("target", "2")
    second = apply(first, "11")
    assert second.find("gis/GenInput/Instances/EtsInstance").get("flags") == "6"
    assert (
        second.find("inputs/DevInput/Instances/DevInputInstance").get("ets-index")
        == "255"
    )
    assert (
        second.find("inputs/DevInput/Instances/DevInputInstance").get("target") == "2"
    )
    unlinked = apply(second, "")
    logical = unlinked.find("gis/GenInput/Instances/EtsInstance")
    assert (logical.get("origin"), logical.get("ref-idx"), logical.get("flags")) == (
        "10",
        "-1",
        "2",
    )
    restored = apply(unlinked, "10")
    assert restored.find("gis/GenInput/Instances/EtsInstance").get("flags") == "4"


def test_occupied_instance_and_inconsistent_physical_mapping_are_rejected():
    first = apply(configuration(), "10")
    with pytest.raises(ValueError, match="already assigned"):
        apply(first, "10", "1")
    first.find("gis/GenInput/Instances/EtsInstance").set("ref-idx", "-1")
    with pytest.raises(ValueError, match="already assigned"):
        apply(first, "10", "1")


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (0, "ValueInSensor", True),
        (6, "GenericSensor", True),
        (3, "MotionSensor", True),
        (3, "BrightnessSensor", False),
        (5, "5", False),
        (255, "Undefined", False),
    ],
)
def test_input_type_compatibility(a, b, expected):
    assert (
        compatible(
            ET.Element("EtsInstance", type=str(a)),
            ET.Element("DevInputInstance", type=b),
        )
        is expected
    )


@pytest.mark.parametrize(
    ("section", "tag", "attributes", "types", "expected"),
    [
        (
            "mds",
            "MdInput",
            {"sensors-avail": "1", "nop": "2", "nob": "1"},
            [3, 3, 4],
            (0, 1),
        ),
        ("pbs", "BtnInput", {"noi": "2", "fold": "2"}, [1, 1, 1], (1, 1)),
    ],
)
def test_instance_detail_encoding(section, tag, attributes, types, expected):
    root = configuration()
    controller = ET.SubElement(
        ET.SubElement(root, section), tag, index="0", **attributes
    )
    instances = ET.SubElement(controller, "Instances")
    for i, t in enumerate(types):
        ET.SubElement(
            instances,
            "EtsInstance",
            index=str(i),
            type=str(t),
            origin="-1",
            **{"ref-idx": "-1", "flags": "1"},
        )
    root.find("inputs/DevInput/Instances/DevInputInstance").set("type", str(types[-1]))
    data = dca.apply(
        ET.tostring(root), dca.Edit(section, "0", "assign", row=2, index="10")
    )
    p = dca.document(data).find("inputs/DevInput/Instances/DevInputInstance")
    assert (int(p.get("ets-index")), int(p.get("ets-detail"))) == expected


def test_same_link_repairs_missing_reciprocal_reference():
    root = configuration()
    root.find("gis/GenInput/Instances/EtsInstance").set("ref-idx", "10")
    result = apply(root, "10")
    assert (
        result.find("inputs/DevInput/Instances/DevInputInstance").get("ets-index")
        == "2"
    )


@pytest.mark.parametrize("reference", ["254", "-1", "010", "99"])
def test_invalid_physical_reference(reference):
    with pytest.raises(ValueError):
        apply(configuration(), reference)


def test_physical_byte_instance_number_and_parent_near_end_of_table():
    root = configuration()
    parent = root.find("inputs/DevInput")
    parent.set("start-index", "253")
    parent.set("noi", "3")
    physical = parent.find("Instances/DevInputInstance")
    physical.set("index", "253")
    physical.set("number", "40")
    result = apply(root, "253")
    assert result.find("gis/GenInput/Instances/EtsInstance").get("ref-idx") == "253"

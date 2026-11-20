from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from xknxeditor.dali.configuration import compile_configuration, inputs_from_xml
from xknxeditor.dali.configuration_readback import merge_inputs
from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.inputs import InputDevice, InputInstance
from xknxeditor.dali.profiles import PROFILES
from xknxeditor.dali.properties import PropertyType

DATA = (Path(__file__).parent / "fixtures/ipas_e64pro_backup.dat").read_bytes()
INSTANCE = InputInstance(100, 3, 4, 7, 10, 0, 1, 118, 0, 500)


def test_input_readback_replaces_links_and_retains_metadata():
    root = ET.fromstring(DATA)
    first = root.find("mds/MdInput/Instances/EtsInstance")
    first.set("ref-idx", "42")
    first.set("origin", "42")
    first.set("flags", "4")
    before_mds = root.find("mds/MdInput").attrib.copy()
    device = InputDevice(3, 0xABCDEF, 100, (INSTANCE,))
    result = merge_inputs(ET.tostring(root), (device,))
    after = ET.fromstring(result)
    assert after.find("mds/MdInput").attrib == before_mds
    assert after.find("mds/MdInput/Instances/EtsInstance").get("ref-idx") == "-1"
    brightness = after.find("mds/MdInput/Instances/EtsInstance[@index='6']")
    assert (
        brightness.get("ref-idx"),
        brightness.get("origin"),
        brightness.get("flags"),
    ) == ("100", "100", "4")
    physical = after.find("inputs/DevInput/Instances/DevInputInstance")
    assert physical.get("target") == "256"
    assert inputs_from_xml(after)[300:303] == bytes((0, 1, 118))
    cleared = ET.fromstring(merge_inputs(result, ()))
    assert (
        cleared.find("mds/MdInput/Instances/EtsInstance[@index='6']").get("origin")
        == "-1"
    )
    assert not cleared.findall("inputs/DevInput")


@pytest.mark.parametrize(
    "changes",
    [{"controller": 3}, {"kind": 1}, {"count": 1}, {"detail": 255}, {"index": 254}],
)
def test_inconsistent_input_readback_is_rejected(changes):
    instance = replace(INSTANCE, **changes)
    with pytest.raises(DaliCommissioningError, match="layout"):
        merge_inputs(DATA, (InputDevice(3, 0xABCDEF, 100, (instance,)),))


def test_two_physical_instances_cannot_share_a_logical_input():
    other = replace(INSTANCE, index=101, number=8)
    with pytest.raises(DaliCommissioningError, match="layout"):
        merge_inputs(DATA, (InputDevice(3, 0xABCDEF, 100, (INSTANCE, other)),))


@pytest.mark.parametrize(
    "attribute,value", [("ref-idx", "-2"), ("origin", "256"), ("index", "2")]
)
def test_malformed_logical_references_fail_before_programming(attribute, value):
    root = ET.fromstring(DATA)
    root.find("gis/GenInput/Instances/EtsInstance").set(attribute, value)
    with pytest.raises(DaliCommissioningError):
        inputs_from_xml(root)


@pytest.mark.parametrize(
    "change", ["missing", "replacement", "address", "number", "kind", "resolution"]
)
@pytest.mark.parametrize("whole_configuration", [False, True])
async def test_stale_input_inventory_aborts_before_any_write(
    monkeypatch, change, whole_configuration
):
    from _dali_fakes import FakeProgrammer

    from xknxeditor.dali.commissioning import MdtDaliCommissioner

    device = InputDevice(3, 0xABCDEF, 100, (INSTANCE,))
    config = compile_configuration(merge_inputs(DATA, (device,)))
    programmer = FakeProgrammer()
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[2])
    )
    if change == "replacement":
        device = replace(device, long_address=0x123456)
    elif change == "address":
        device = replace(device, address=4)
    elif change != "missing":
        device = replace(device, instances=(replace(INSTANCE, **{change: 2}),))

    async def read_inputs(_):
        return () if change == "missing" else (device,)

    monkeypatch.setattr("xknxeditor.dali.inputs.read_inputs", read_inputs)
    with pytest.raises(DaliCommissioningError, match="inventory changed"):
        if whole_configuration:
            await commissioner.write_configuration(config)
        else:
            await commissioner.write_inputs(
                config.inputs, expected_inventory=config.input_inventory
            )
    assert not programmer.writes


async def test_input_write_ignores_volatile_status_but_verifies_mapping(monkeypatch):
    from _dali_fakes import FakeProgrammer

    from xknxeditor.dali.commissioning import MdtDaliCommissioner

    device = InputDevice(3, 0xABCDEF, 100, (INSTANCE,))
    config = compile_configuration(merge_inputs(DATA, (device,)))
    programmer = FakeProgrammer()
    programmer.set_read(PropertyType.COMMAND, bytes(3))
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, profile=PROFILES[2])
    )
    device = replace(device, instances=(replace(INSTANCE, status=1, value=600),))

    async def read_inputs(_):
        return (device,)

    monkeypatch.setattr("xknxeditor.dali.inputs.read_inputs", read_inputs)
    assert await commissioner.write_inputs(
        config.inputs, expected_inventory=config.input_inventory
    ) == (device,)
    assert programmer.writes_for(PropertyType.INPUT_COMMISSIONING)
    device = replace(device, instances=(replace(INSTANCE, detail=2),))
    with pytest.raises(DaliCommissioningError, match="assignment readback"):
        await commissioner.write_inputs(
            config.inputs, expected_inventory=config.input_inventory
        )


async def test_low_level_input_write_requires_referenced_instance_to_exist(monkeypatch):
    from _dali_fakes import FakeProgrammer

    from xknxeditor.dali.inputs import write_inputs

    programmer = FakeProgrammer()

    async def read_inputs(_):
        return ()

    monkeypatch.setattr("xknxeditor.dali.inputs.read_inputs", read_inputs)
    for code in (0, 254):
        mapping = bytearray(b"\xff" * 768)
        mapping[0:3] = bytes([code, 0, 1])
        with pytest.raises(DaliCommissioningError, match="inventory changed"):
            await write_inputs(
                MdtDaliConnection(programmer, profile=PROFILES[2]), bytes(mapping)
            )
    assert not programmer.writes


def test_zero_instance_input_is_rejected_during_configuration_preparation():
    root = ET.fromstring(DATA)
    ET.SubElement(
        root.find("inputs"), "DevInput", index="0", noi="0", **{"start-index": "0"}
    )
    with pytest.raises(DaliCommissioningError, match="invalid number"):
        inputs_from_xml(root)

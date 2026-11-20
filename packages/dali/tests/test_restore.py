from pathlib import Path
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest
from _dali_fakes import FakeProgrammer

from xknxeditor.dali.commissioning import MdtDaliCommissioner
from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.profiles import PROFILES
from xknxeditor.dali.properties import DeviceCommand, PropertyType
from xknxeditor.dali.restore import prepare_restore, restore

DATA = (Path(__file__).parent / "fixtures/ipas_e64pro_backup.dat").read_bytes()


def inventory():
    root = ET.fromstring(DATA)
    root.set("CanRestore", "True")
    ET.SubElement(
        root.find("devecgs"),
        "DevECG",
        index="3",
        type="8",
        subtype="2",
        groupindex="16",
        **{
            "short-address": "9",
            "long-address": "123456",
            "min-value": "1",
            "min-ctemp": "2700",
            "max-ctemp": "6500",
            "gtin": "1234567890",
        },
    )
    logical = root.find("ecgs/ECG[@index='9']")
    logical.set("short-address", "3")
    logical.set("groupindex", "16")
    logical.set("flags", "4")
    return root


def test_restore_requires_saved_inventory_and_reciprocal_committed_assignments():
    root = inventory()
    plan = prepare_restore(ET.tostring(root))
    assert plan.static[24:32] == bytes.fromhex("123456018c0a6419")
    assert plan.properties[12:16] == bytes([8, 2, 16, 9])
    assert int.from_bytes(plan.gtin[18:24], "big") == 1234567890
    assert plan.inputs == b"\xff" * 384
    assert plan.calibration == b"\xff" * 80
    with pytest.raises(DaliCommissioningError, match="inventory"):
        prepare_restore(DATA)
    root.find("ecgs/ECG[@index='9']").set("flags", "3")
    with pytest.raises(DaliCommissioningError, match="pending"):
        prepare_restore(ET.tostring(root))
    root.find("ecgs/ECG[@index='9']").set("flags", "4")
    root.find("devecgs/DevECG").set("short-address", "8")
    with pytest.raises(DaliCommissioningError, match="inconsistent"):
        prepare_restore(ET.tostring(root))


def test_restore_preserves_valid_calibration_without_runtime_flags():
    root = inventory()
    controller = root.find("mds/MdInput")
    controller.attrib.update(
        calibration="0",
        vmin="20",
        vmax="80",
        mmin="100",
        mmax="1000",
        bmin="50",
        bmax="500",
    )
    plan = prepare_restore(ET.tostring(root))
    assert plan.calibration[:10] == bytes.fromhex("3300640032cc03e801f4")
    assert plan.calibration[10:] == b"\xff" * 70


async def test_restore_writes_inventory_verifies_then_restarts(monkeypatch):
    async def no_delay(_):
        pass

    monkeypatch.setattr("xknxeditor.dali.restore.asyncio.sleep", no_delay)
    plan = prepare_restore(ET.tostring(inventory()))
    p = FakeProgrammer()
    c = MdtDaliCommissioner(MdtDaliConnection(p, profile=PROFILES[2]))
    monkeypatch.setattr(c, "commission", AsyncMock(return_value=True))
    monkeypatch.setattr(c, "write_configuration", AsyncMock())
    for prop, data in [
        (PropertyType.EVG_STATIC, plan.static),
        (PropertyType.INPUT_TABLE, plan.inputs),
        (PropertyType.GTIN, plan.gtin),
        (PropertyType.CONSTANT_LIGHT, plan.calibration),
    ]:
        p.set_read(prop, data)
    await restore(c, plan)
    assert p.writes[0]["data"] == bytes([DeviceCommand.PREPARE_RESTORE, 0, 0])
    assert p.writes[-1]["data"] == bytes([DeviceCommand.RESET_DEVICE, 0, 0])
    p.writes.clear()
    p.set_read(PropertyType.GTIN, bytes(len(plan.gtin)))
    with pytest.raises(DaliCommissioningError, match="readback"):
        await restore(c, plan)
    assert all(w["data"] != bytes([DeviceCommand.RESET_DEVICE, 0, 0]) for w in p.writes)


async def test_restore_recreates_inputs_before_configuration_preflight(monkeypatch):
    from xknxeditor.dali.configuration_readback import merge_inputs
    from xknxeditor.dali.inputs import InputDevice, InputInstance

    async def no_delay(_):
        pass

    monkeypatch.setattr("xknxeditor.dali.restore.asyncio.sleep", no_delay)
    inputs = (
        InputDevice(
            3, 0xABCDEF, 100, (InputInstance(100, 3, 4, 7, 10, 0, 1, 118, 0, 500),)
        ),
    )
    root = ET.fromstring(merge_inputs(DATA, inputs))
    root.set("CanRestore", "True")
    plan = prepare_restore(ET.tostring(root))

    class Gateway(FakeProgrammer):
        async def write_property(
            self, object_index, property_id, data, *, count=1, start_index=1
        ):
            result = await super().write_property(
                object_index, property_id, data, count=count, start_index=start_index
            )
            if property_id != PropertyType.COMMAND and start_index:
                size = self.element_sizes[property_id]
                old = self._reads.get(property_id, b"")
                table = bytearray(old.ljust((start_index - 1 + count) * size, b"\xff"))
                table[(start_index - 1) * size : (start_index - 1 + count) * size] = (
                    data
                )
                self.set_read(property_id, bytes(table))
            return result

    p = Gateway()
    p.element_sizes[PropertyType.GROUP_STATUS] = 10
    for prop, count in (
        (PropertyType.EVG_STATUS, 64),
        (PropertyType.GROUP_DYN, 16),
        (PropertyType.GROUP_STATUS, 16),
        (PropertyType.GROUP_FAILURE, 16),
        (PropertyType.INPUT_STATUS, 256),
    ):
        p.set_read(prop, bytes(count * p.element_sizes[prop]))
    p.set_read(213, bytes(16))
    p.set_read(PropertyType.SCENE_GROUP_VALUE, b"\xff" * 8)
    p.set_read(PropertyType.INPUT_TABLE, b"\xff" * 384)
    p.set_read(PropertyType.COMMAND, bytes(3))
    c = MdtDaliCommissioner(MdtDaliConnection(p, profile=PROFILES[2]))
    await restore(c, plan)
    recovered = await c.read_inputs()
    assert recovered[0].long_address == inputs[0].long_address
    assert recovered[0].instances[0].controller == 0
    assert recovered[0].instances[0].detail == 1
    assert p.writes[-1]["data"] == bytes([DeviceCommand.RESET_DEVICE, 0, 0])

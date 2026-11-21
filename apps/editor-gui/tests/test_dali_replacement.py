from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from xml.etree import ElementTree as ET

import pytest
from editor_gui.plugins.project.ui.dali_commissioning import DaliCommissioningPanel
from editor_gui.programming_dali import (
    finish_dali_replacement,
    prepare_dali_replacement,
    prepare_workspace_operation,
)

from xknxeditor.dali.model import (
    DaliBusState,
    EcgState,
    Scene,
    SceneValue,
    Template,
    TemplateCommand,
)
from xknxeditor.dali.profiles import PROFILES, backup_blob_name
from xknxeditor.dali.project_persistence import build_backup_store_xml

ROOT = next(p for p in Path(__file__).parents if (p / "packages/dali").exists())


@pytest.mark.parametrize("profile_index", [0, 1, 2])
async def test_full_replacement_reads_chunked_tables_and_clears_absent_slots(
    profile_index,
):
    from packages.dali.tests._dali_fakes import FakeProgrammer
    from xknxeditor.dali.commissioning import MdtDaliCommissioner
    from xknxeditor.dali.connection import MdtDaliConnection
    from xknxeditor.dali.properties import PropertyType, element_size_for
    from xknxeditor.dali.structs import EcgProp, EcgStatic

    device, _, before = fixture(profile_index)
    root = ET.fromstring(before)
    ET.SubElement(
        root.find("devecgs"),
        "DevECG",
        index="8",
        type="8",
        gtin="9876543210987",
        description="Disconnected gear",
        **{
            "short-address": "5",
            "long-address": "ABCDEF",
            "min-ctemp": "2700",
            "max-ctemp": "6500",
        },
    )
    device.binary_data[0].data = ET.tostring(root, encoding="utf-16le")
    programmer = FakeProgrammer()
    profile = PROFILES[profile_index]
    programmer.element_sizes.update(dict(profile.element_size_overrides))
    programmer.max_payload_bytes = 30
    programmer.set_read(PropertyType.HAWK_FIRMWARE, bytes((4, 0, 1)))
    programmer.set_read(PropertyType.COMMAND, bytes(3))
    programmer.set_read(
        PropertyType.EVG_PROP,
        b"".join(
            EcgProp(6, 0, 16, 12).encode()
            if i == 7
            else EcgProp(0, 0, 255, 255).encode()
            for i in range(64)
        ),
    )
    programmer.set_read(
        PropertyType.EVG_STATIC,
        b"".join(
            EcgStatic(0x123456 if i == 7 else 0, 0, 0, 0).encode() for i in range(64)
        ),
    )
    for prop, count in (
        (PropertyType.EVG_STATUS, 64),
        (PropertyType.GROUP_DYN, 16),
        (PropertyType.GROUP_STATUS, 16),
        (PropertyType.GROUP_FAILURE, 16),
    ):
        programmer.set_read(prop, bytes(element_size_for(prop, profile) * count))
    programmer.set_read(
        PropertyType.GTIN,
        b"".join(
            (1234567890000 + i if i != 8 else 0).to_bytes(6, "big") for i in range(64)
        ),
    )
    commissioner = MdtDaliCommissioner(MdtDaliConnection(programmer, profile=profile))
    plan = prepare_dali_replacement(device)
    scan, data = await plan.execute(commissioner)
    result = ET.fromstring(data)
    rows = result.findall("devecgs/DevECG")
    assert len(scan.ecgs) == len(rows) == 64
    assert [e.slot for e in scan.present_ecgs] == [7]
    missing = result.find("devecgs/DevECG[@index='8']")
    assert missing.get("groupindex") == missing.get("short-address") == "255"
    assert missing.get("long-address") == "000000"
    assert missing.get("description") == "Disconnected gear"
    assert missing.get("min-ctemp") is missing.get("max-ctemp") is None
    if profile_index:
        assert result.find("devecgs/DevECG[@index='7']").get("gtin") == "1234567890007"
        assert missing.get("gtin") == "0"
        assert any(
            pid == PropertyType.GTIN and start > 1 for pid, start, _ in programmer.reads
        )
    assert [w["data"] for w in programmer.writes] == [bytes((19, 0, 0))]
    assert {e.tag: ET.tostring(e) for e in result if e.tag != "devecgs"} == {
        e.tag: ET.tostring(e) for e in root if e.tag != "devecgs"
    }


def fixture(profile_index=0, channel=0, saved=True):
    profile = PROFILES[profile_index]
    scan = DaliBusState(
        channel=channel,
        ecgs=[
            EcgState(
                slot=7,
                present=True,
                ecg_type=8,
                sub_type=2,
                group_index=16,
                ets_ecg_index=12,
                long_address=0x123456,
                min_color_temp=2700,
                max_color_temp=6500,
            )
        ],
    )
    if profile_index == 0:
        data = build_backup_store_xml(state=scan, individual_address="1.1.1")
    else:
        data = (
            ROOT / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat"
        ).read_bytes()
    root = ET.fromstring(data)
    physical = root.find("devecgs")
    physical.clear()
    ET.SubElement(
        physical,
        "DevECG",
        index="7",
        description="Keep physical name",
        custom="keep",
        **{"long-address": "654321"},
    )
    data = ET.tostring(root, encoding="utf-16le")
    device = SimpleNamespace(
        node_id=1,
        individual_address="1.1.1",
        app=SimpleNamespace(id=profile.app_id_prefixes[0] + "21-1234"),
        binary_data=[
            SimpleNamespace(
                name=backup_blob_name(profile.backup_blob_base, channel), data=data
            )
        ]
        if saved
        else [],
    )
    commissioner = SimpleNamespace(
        connection=SimpleNamespace(
            profile=profile,
            channel=channel,
            read_table=AsyncMock(return_value=(1234567890123).to_bytes(6, "big") * 64),
        ),
        replace_ecg=AsyncMock(return_value=scan),
        read_scenes=AsyncMock(return_value=[Scene(0, values=[SceneValue(0, 2, 255)])]),
        read_templates=AsyncMock(
            return_value=[
                Template(
                    0,
                    groups=(2,),
                    commands=[TemplateCommand(1, 7, 0, bytes([255, 0, 0, 0]))],
                )
            ]
        ),
    )
    return device, commissioner, data


@pytest.mark.parametrize(
    "profile_index,channel,saved",
    [(0, 0, True), (0, 1, True), (0, 1, False), (1, 0, True), (2, 0, True)],
)
async def test_replacement_refreshes_physical_inventory_and_preserves_settings(
    profile_index, channel, saved
):
    device, commissioner, before = fixture(profile_index, channel, saved)
    plan = prepare_dali_replacement(device, channel)
    scan, data = await plan.execute(commissioner)
    root = ET.fromstring(data)
    physical = root.find("devecgs/DevECG[@index='7']")
    assert physical.get("long-address") == "123456"
    assert physical.get("short-address") == "12"
    assert physical.get("min-ctemp") == "2700"
    assert scan.channel == channel
    if saved:
        assert physical.get("description") == "Keep physical name"
        assert physical.get("custom") == "keep"
        old = ET.fromstring(before)
        assert {e.tag: ET.tostring(e) for e in root if e.tag != "devecgs"} == {
            e.tag: ET.tostring(e) for e in old if e.tag != "devecgs"
        }
        assert device.binary_data[0].data == before
        commissioner.read_scenes.assert_not_awaited()
        commissioner.read_templates.assert_not_awaited()
    else:
        commissioner.read_scenes.assert_awaited_once_with()
        commissioner.read_templates.assert_awaited_once_with()
        assert root.find("scenes/Scene/SceneItem").get("value") == "100"
        assert root.find("templates/Tpl/Commands/TplCmd").get("time") == "07:00"
    if profile_index:
        assert physical.get("gtin") == "1234567890123"
    else:
        commissioner.connection.read_table.assert_not_awaited()
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: device,
        persist_dali_backup=Mock(),
    )
    finish_dali_replacement(project, device, plan, 1, data)
    assert project.persist_dali_backup.call_args.args == (device, plan.name, data)


@pytest.mark.parametrize("changed", ["data", "address", "app", "generation", "missing"])
async def test_replacement_completion_rejects_newer_project(changed):
    device, commissioner, _ = fixture()
    plan = prepare_dali_replacement(device)
    _, data = await plan.execute(commissioner)
    current, _, _ = fixture()
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: current,
        persist_dali_backup=Mock(),
    )
    if changed == "data":
        current.binary_data[0].data = b"newer"
    elif changed == "address":
        current.individual_address = "1.1.2"
    elif changed == "app":
        current.app.id = "other"
    elif changed == "generation":
        project.generation = 2
    else:
        current = None
    with pytest.raises(ValueError, match="changed"):
        finish_dali_replacement(project, device, plan, 1, data)
    project.persist_dali_backup.assert_not_called()


async def test_workspace_replacement_uses_shared_readback_plan():
    device, commissioner, _ = fixture(2)
    plan = prepare_workspace_operation(device, "replace", "ecgs")
    result = await plan.execute(commissioner)
    assert (
        ET.fromstring(result["configuration"])
        .find("devecgs/DevECG[@index='7']")
        .get("gtin")
        == "1234567890123"
    )
    commissioner.replace_ecg.assert_awaited_once_with()


@pytest.mark.parametrize("changed", [False, True])
async def test_panel_replacement_keeps_guard_and_saves_when_hidden(changed):
    device, commissioner, _ = fixture(0, 1)
    current, _, _ = fixture(0, 1)
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: current,
        persist_dali_backup=Mock(),
    )
    queued = []
    panel = DaliCommissioningPanel(
        lambda *args: queued.append(args),
        _project=project,
        _generation=lambda: project.generation,
    )
    key = panel._key(device)
    panel._active_channel[key] = 1
    panel._dispatch(device, key, "replace")
    assert len(queued) == 1
    _, operation, channel, _, ended, writes = queued[0]
    assert channel == 1 and writes
    operation.validate_device(current)
    await operation(commissioner)
    if changed:
        current.binary_data[0].data = b"newer"
    ended()
    state = panel._state(key, 1)
    assert not state.busy and state.scan.channel == 1
    assert project.persist_dali_backup.call_count == int(not changed)
    assert bool(state.error) is changed


async def test_wrong_commissioner_is_rejected_before_replacement():
    device, commissioner, _ = fixture()
    plan = prepare_dali_replacement(device)
    commissioner.connection.channel = 1
    with pytest.raises(ValueError, match="changed"):
        await plan.execute(commissioner)
    commissioner.replace_ecg.assert_not_awaited()


def test_replacement_rejected_by_queue_releases_panel():
    device, _, _ = fixture()
    project = SimpleNamespace(generation=1)
    panel = DaliCommissioningPanel(
        Mock(side_effect=ValueError("changed while queued")),
        _project=project,
        _generation=lambda: project.generation,
    )
    key = panel._key(device)
    panel._dispatch(device, key, "replace")
    state = panel._state(key, 0)
    assert not state.busy and "changed while queued" in state.error


@pytest.mark.parametrize(
    "changed", ["invalid", "duplicate", "range", "unsupported", "missing"]
)
def test_invalid_replacement_plan_is_rejected_before_bus_access(changed):
    device, commissioner, data = fixture(2)
    root = ET.fromstring(data)
    if changed == "invalid":
        device.binary_data[0].data = b"not XML"
    elif changed in ("duplicate", "range"):
        ET.SubElement(
            root.find("devecgs"),
            "DevECG",
            index="7" if changed == "duplicate" else "64",
        )
        device.binary_data[0].data = ET.tostring(root)
    elif changed == "missing":
        device.binary_data.clear()
    else:
        device.app.id = "unknown"
    with pytest.raises((ValueError, ET.ParseError)):
        prepare_dali_replacement(device)
    commissioner.replace_ecg.assert_not_awaited()


@pytest.mark.parametrize("profile_index", [0, 2])
async def test_replacement_persistence_is_one_undo_step(
    tmp_path, monkeypatch, profile_index
):
    from dataclasses import replace

    from editor_gui.plugins.catalog.service import CatalogService
    from editor_gui.plugins.project.service import ProjectService

    from xknxeditor.prod import parse_application_xml
    from xknxeditor.prod.application import Application

    target, commissioner, before = fixture(profile_index)
    sample = parse_application_xml(
        (ROOT / "packages/download/tests/fixtures/powertool/21.xml").read_bytes(),
        "M-0007",
    )[0]
    app = Application(
        replace(sample.program, id=target.app.id),
        sample.version,
        target.app.id.split("_")[0],
    )
    project = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    project.set_logger(Mock())
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    try:
        node = project.add_device("P-1", "HP-1", "DALI", app)
        current = project.find_device_by_node_id(node)
        name = target.binary_data[0].name
        project.persist_device_blob(current, name, before)
        current = project.find_device_by_node_id(node)
        plan = prepare_dali_replacement(current)
        _, data = await plan.execute(commissioner)
        cursor = project.cursor
        finish_dali_replacement(project, current, plan, project.generation, data)
        assert project.cursor == cursor + 1
        assert (
            project.device_binary_data(project.find_device_by_node_id(node), name)
            == data
        )
        project.undo()
        assert (
            project.device_binary_data(project.find_device_by_node_id(node), name)
            == before
        )
        project.redo()
        assert (
            project.device_binary_data(project.find_device_by_node_id(node), name)
            == data
        )
    finally:
        project.close()

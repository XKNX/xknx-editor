"""Project flags, missing parameters and table reservations in saved configurations."""

import json

import pytest

from editor_gui.plugin_providers.powertool import PROVIDER
from packages.download.tests.test_prepared import MAPPING, master
from packages.vendor.tests.powertool_fixtures import open_archive
from xknxeditor.download.download import prepared_controls
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem
from xknxeditor.proj import export_knxproj, import_knxproj
from xknxeditor.vendor.powertool import BLOB_NAME, ChannelConfig, PowerToolConfig
from xknxeditor.vendor.powertool.project_writer import write_project_blob

from .test_powertool import assert_current_blob, current, native_project


def flag_project(tmp_path, monkeypatch, sample, resolved, service=None):
    project, nid = native_project(tmp_path, monkeypatch, service=service, variant="02")
    if sample:
        with open_archive("reported-samples.zip") as archive:
            data = archive.read(sample + ".dat")
    else:
        config = PowerToolConfig(
            "02",
            (
                ChannelConfig("short_long_button"),
                ChannelConfig("short_long_button"),
                ChannelConfig("blind_rocker"),
                ChannelConfig("inactive"),
            ),
            object_flags={
                "channel_0/short_long_button/object_1": 71,
                "channel_1/short_long_button/object_0": 71,
            },
        )
        data = write_project_blob(None, config)
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
    state = PROVIDER.read(current(project, nid))
    project._svc.set_parameters(project._pid, nid, list(state.parameters.items()))
    project._svc.sync_device_com_objects(
        project._pid, nid, [(o.reference, None) for o in state.objects]
    )
    rows = project._svc.devices(project._pid)[0].com_objects
    assert len(rows) == 6
    for index, row in enumerate(rows):
        row.read_flag = row.read_flag_override = False
        for flag in ("communication", "write", "transmit", "update", "read_on_init"):
            value = flag in ("communication", "transmit") or (
                flag in ("write", "update") and index < 4
            )
            setattr(row, flag + "_flag", value)
            setattr(row, flag + "_flag_override", value)
            if index in (1, 2):
                setattr(row, flag + "_flag_override", None)
                if not resolved:
                    setattr(row, flag + "_flag", None)
        ga = project.create_group_address(f"1/0/{index + 1}", "Linked object")
        project._svc.link_com_object(project._pid, row.id, ga, sending=True)
    project._refresh_device(nid)
    project._bump(structural=False)
    return project, nid, data


@pytest.mark.parametrize("sample", [None, "flags-a", "flags-b"])
@pytest.mark.parametrize("resolved", [False, True])
@pytest.mark.parametrize("action", ["import", "edit"])
def test_project_flags_win_and_survive_conversion_and_archive(
    tmp_path, monkeypatch, sample, resolved, action
):
    project, nid, original = flag_project(tmp_path, monkeypatch, sample, resolved)
    device = current(project, nid)
    before = project.prepared_download(device)
    links = project.group_communication_for(device).links
    cursor = project.cursor
    expected = bytes.fromhex("0006d700d700d700d70047004700")
    assert before.image.object_segments[3] == expected
    assert before.resource_sizes[3] == 14
    assert before.image.allocation_sizes[3] == 40
    assert project.cursor == cursor
    assert project.device_binary_data(device, "PT2EtsPlugin") == original
    if action == "import":
        lost = project.import_stored_plugin_config(device)
    else:
        lost = project.edit_plugin_parameter(
            device, "powertool:0:duration_of_long_operation_s", "500"
        )
    assert lost == []
    assert project.cursor == cursor + 1
    device = current(project, nid)
    active = assert_current_blob(project, nid)
    assert project.prepared_download(device).image.object_segments[3] == expected
    assert project.group_communication_for(device).links == links
    assert all(co.flags.write and co.flags.update for co in device.com_objects[:4])
    project.undo()
    assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == original
    assert project.group_communication_for(current(project, nid)).links == links
    project.redo()
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == active
    project.close()
    export_knxproj(tmp_path / "project.xknx", tmp_path / "saved.knxproj", schema="23")
    import_knxproj(tmp_path / "saved.knxproj", tmp_path / "restored.xknx")
    project.open(tmp_path / "restored.xknx")
    device = project.devices[0]
    assert project.device_binary_data(device, "PT2EtsPlugin") == active
    assert project.prepared_download(device).image.object_segments[3] == expected
    assert project.group_communication_for(device).links == links
    project.close()


def test_preparation_logs_default_encoding_and_effective_flags(tmp_path, monkeypatch):
    from dataclasses import replace

    from editor_gui.plugin_providers.powertool import configuration
    from editor_gui.plugins.logger.service import LogService
    from xknxeditor.vendor.powertool.project_blob import PLUGIN_BLOB_NAME

    project, nid, _ = flag_project(tmp_path, monkeypatch, None, False)
    device = current(project, nid)
    config = configuration(device)
    assert isinstance(config, PowerToolConfig)
    channels = list(config.channels)
    channels[2] = ChannelConfig("blind_rocker")
    config = replace(config, channels=tuple(channels))
    monkeypatch.setattr(
        "editor_gui.plugin_providers.powertool.configuration", lambda _: config
    )
    before = project.device_binary_data(device, PLUGIN_BLOB_NAME)
    cursor = project.cursor
    logs = LogService()
    project.prepared_download(device)
    messages = "\n".join(record.event for record in logs.get_records())
    assert "default 400 ms, encoded 0050" in messages
    assert "channel 2/blind_rocker parameter duration_of_long_operation_s" in messages
    assert "override saved flags 47 -> d7" in messages
    assert "allocation reservations {3: 40}" in messages
    assert project.device_binary_data(device, PLUGIN_BLOB_NAME) == before
    assert project.cursor == cursor
    project.close()


@pytest.mark.parametrize("source", ["base", "reference", "instance"])
def test_read_flag_inheritance_survives_first_conversion(tmp_path, monkeypatch, source):
    from xknxeditor.namespaces.intermediate import Enable
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    project, nid, _ = flag_project(tmp_path, monkeypatch, None, False)
    row = project._svc.devices(project._pid)[0].com_objects[1]
    row.read_flag = row.read_flag_override = True if source == "instance" else None
    catalog = ApplicationIndexer(current(project, nid).app.program)
    reference = catalog.com_object_refs[row.ref_id]
    base = catalog.com_objects[reference.ref_id]
    if source != "base":
        monkeypatch.setattr(base, "read_flag", Enable.DISABLED)
        monkeypatch.setattr(reference, "read_flag", Enable.ENABLED)
    if source == "instance":
        monkeypatch.setattr(reference, "read_flag", Enable.DISABLED)
    project._refresh_device(nid)
    device = current(project, nid)
    before = project.prepared_download(device)
    assert before.image.object_segments[3][4] == 0xDF
    project.import_stored_plugin_config(device)
    assert project.prepared_download(current(project, nid)).image == before.image
    assert_current_blob(project, nid)
    project.close()


async def test_reported_table_matches_memory_and_executed_allocation(
    tmp_path, monkeypatch
):
    from xknx.telegram.apci import MemoryWrite, PropertyValueWrite

    from packages.download.tests.conftest import FakeDevice
    from xknxeditor.download.procedure import LoadProcedureRunner
    from xknxeditor.download.programmer import DeviceProgrammer

    with open_archive("reported-samples.zip") as archive:
        readback = json.loads(archive.read("flags-a-readback-group-object-table.json"))
    project, nid, _ = flag_project(tmp_path, monkeypatch, "flags-a", True)
    device = current(project, nid)
    prepared = project.prepared_download(device)
    memory = bytes.fromhex(readback["obj3.memory"])
    reservation = int.from_bytes(bytes.fromhex(readback["obj3.pid27"])[:4])
    assert prepared.image.object_segments[3] == memory[:14]
    assert prepared.image.allocation_sizes[3] == reservation == 40
    controls = prepared_controls(
        device.app, prepared, master(), device.second_application()
    )
    allocation = next(
        c for c in controls if isinstance(c, LdCtrlRelSegment) and c.lsm_idx == 3
    )
    write = next(
        c for c in controls if isinstance(c, LdCtrlWriteRelMem) and c.obj_idx == 3
    )
    assert allocation.size == reservation
    assert write.size == 14
    assert not any(isinstance(c, LdCtrlWriteProp) and c.prop_id == 27 for c in controls)
    fake = FakeDevice(MAPPING, 0x07B0)
    fake.table_references = {i: 0x1000 * i for i in range(1, 6)}
    base = fake.table_references[3] = int(readback["obj3.segment"]["start"], 16)
    fake.memory.update({base + i: value for i, value in enumerate(memory)})
    for prop in prepared.image.properties:
        fake.property_element_sizes[prop.object_index, prop.property_id] = len(
            prop.data
        )
    runner = LoadProcedureRunner(
        device.app,
        prepared.image,
        DeviceProgrammer(fake),
        controls=controls,
        object_types=MAPPING,
        application2=device.second_application(),
    )
    await runner.preflight()
    assert not any(isinstance(p, (MemoryWrite, PropertyValueWrite)) for p in fake.sent)
    await runner.run()
    assert bytes(fake.memory[base + i] for i in range(reservation)) == memory
    assert any(
        isinstance(p, PropertyValueWrite)
        and p.object_index == 3
        and p.property_id == 5
        and p.data == bytes.fromhex("030b0000002800000000")
        for p in fake.sent
    )
    project.close()

"""Native configuration edits against an optional original project archive."""

import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import pytest

from editor_gui.plugin_providers.siemens_generic import PROVIDER, blob, sources
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.proj import export_knxproj
from xknxeditor.vendor.siemens_generic import parse_settings
from xknxeditor.vendor.siemens_generic.blob import decode_text

ROOT = next(p for p in Path(__file__).parents if (p / "packages/vendor").is_dir())
ARCHIVE = ROOT / ".references/siemens-generic/~siemens2.knxproj"
pytestmark = pytest.mark.skipif(
    not ARCHIVE.exists(), reason="Optional Siemens project absent"
)


@pytest.fixture
def project(tmp_path):
    service = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.import_knxproj(ARCHIVE, tmp_path / "project.xknx")
    yield service
    service.close()


def current(project, node):
    device = project.find_device_by_node_id(node)
    assert device is not None
    return device


def test_shared_parameter_cache_follows_edit_and_undo(project):
    device = project.devices[0]
    node = device.node_id

    def sections():
        device = current(project, node)
        mapping = project.plugin_mapping(device)
        assert mapping is not None
        return project.plugin_sections(device, mapping)

    initial = sections()
    assert sections() is initial
    parameter = next(
        p for s in initial for p in s.parameters if p.writable and len(p.choices) > 1
    )
    original = (
        parameter.raw_value if parameter.raw_value is not None else parameter.value
    )
    alternative = next(v for v, _ in parameter.choices if v != original)

    def value():
        p = next(
            p for s in sections() for p in s.parameters if p.ref_id == parameter.ref_id
        )
        return p.raw_value if p.raw_value is not None else p.value

    project.edit_plugin_parameter(device, parameter.ref_id, alternative)
    assert value() == alternative and sections() is not initial
    project.undo()
    assert value() == original
    project.redo()
    assert value() == alternative


def test_display_units_edit_raw_blob_and_image_with_undo(project):
    from xknxeditor.vendor.siemens_generic.codec import encode
    from xknxeditor.vendor.siemens_generic.image import compile_image
    from xknxeditor.vendor.siemens_generic.presentation import value_format

    cases = {
        "mm": ("10", "600"),
        "mm:ss": ("02:00", "120"),
        "HH:mm:ss": ("00:00:02", "2000"),
        "Percent": ("10", "25"),
    }
    tested = set()
    for device in project.devices:
        source = sources(device)
        for section in PROVIDER.sections(device):
            for row in section.parameters:
                ref = row.ref_id.removeprefix(PROVIDER.parameter_prefix)
                formatting = value_format(source.description, ref)
                key = formatting.format or formatting.clock
                if not row.writable or key not in cases or key in tested:
                    continue
                node = device.node_id
                before = blob(current(project, node))
                cursor = project.cursor
                assert row.raw_value is not None
                assert (
                    PROVIDER.edit(current(project, node), row.ref_id, row.value).data
                    == before
                )
                entered, stored = cases[key]
                project.edit_plugin_parameter(
                    current(project, node), row.ref_id, entered
                )
                changed = blob(current(project, node))
                state = parse_settings(changed)
                assert state.values[ref] == stored
                assert project.cursor == cursor + 1
                param = source.description.parameters[
                    source.description.refs[ref].parameter_id
                ]
                compiled = compile_image(
                    source.description,
                    state,
                    {"DeviceOrderNumber": device.order_number},
                )
                expected = encode(stored, source.description.types[param.type_id])
                offset = param.offset - compiled.base
                assert compiled.data[offset : offset + len(expected)] == expected
                project.undo()
                assert blob(current(project, node)) == before
                project.redo()
                assert blob(current(project, node)) == changed
                project.undo()
                tested.add(key)
    assert tested == cases.keys()


def test_original_devices_objects_and_edits_roundtrip(project, tmp_path):
    assert len(project.devices) == 7
    with ZipFile(ARCHIVE) as archive:
        original_blobs = {
            archive.read(n) for n in archive.namelist() if n.endswith(".dat")
        }
    for device in list(project.devices):
        node = device.node_id
        original = blob(device)
        assert original in original_blobs
        assert device.native_error is None
        roles = PROVIDER.read(device).objects
        assert len(roles) == len(device.com_objects) > 0
        assert {r.reference for r in roles} == {c.id for c in device.com_objects}
        assert all(c.db_id is not None for c in device.com_objects)
        source = sources(device)
        old_settings = parse_settings(original)
        assert old_settings is not None
        for ref, value in old_settings.values.items():
            from xknxeditor.vendor.siemens_generic.configuration import validate_value

            if not source.description.refs[ref].global_variable:
                validate_value(source.description, ref, value)
        parameter = next(
            p
            for s in PROVIDER.sections(device)
            for p in s.parameters
            if p.writable and p.choices and len(p.choices) > 1
        )
        alternative = next(v for v, _ in parameter.choices if v != parameter.value)
        no_dpt = {r.reference for r in roles if not r.dpt}
        for co in device.com_objects:
            if co.object_size == "1 Bit" and co.id in no_dpt:
                project.set_com_object_datapoint_type(device, co.id, "1.001")
            ga = project.create_group_address_value(
                len(project.group_addresses) + 1, co.name
            )
            project.link_com_object_to_ga(co.db_id, ga)
        device = current(project, node)
        before = {c.id: c.db_id for c in device.com_objects}
        overrides = {c.id: c.datapoint_type_override for c in device.com_objects}
        cursor = project.cursor
        lost = project.edit_plugin_parameter(device, parameter.ref_id, alternative)
        changed = current(project, node)
        assert project.cursor == cursor + 1
        assert changed.native_error is None
        after = blob(changed)
        parsed = parse_settings(after)
        assert parsed is not None and parsed.compact == old_settings.compact
        assert (
            parsed.values[parameter.ref_id.removeprefix(PROVIDER.parameter_prefix)]
            == alternative
        )
        before_xml = ET.fromstring(decode_text(original))
        after_xml = ET.fromstring(decode_text(after))
        assert (
            decode_text(after).split("?>", 1)[0]
            == decode_text(original).split("?>", 1)[0]
        )
        assert ET.tostring(before_xml.find("DownloadedSettings")) == ET.tostring(
            after_xml.find("DownloadedSettings")
        )
        remaining = {c.id: c.db_id for c in changed.com_objects}
        for ref in before.keys() & remaining.keys():
            assert before[ref] == remaining[ref]
            assert (
                changed.find_com_object(ref).datapoint_type_override == overrides[ref]
            )
            assert project._svc.com_object_links(project._pid, before[ref])
        assert len(lost) == len(before.keys() - remaining.keys())
        changed.release_dynamic_ui()
        assert changed.get_com_object_modules()
        assert len(changed.get_visible_com_objects()) == len(remaining)
        project.undo()
        assert blob(current(project, node)) == original
        assert {c.id: c.db_id for c in current(project, node).com_objects} == before
        for object_id in before.values():
            assert project._svc.com_object_links(project._pid, object_id)
        project.redo()
        assert blob(current(project, node)) == after
    expected = {d.node_id: blob(d) for d in project.devices}
    project.close()
    project.open(tmp_path / "project.xknx")
    assert {d.node_id: blob(d) for d in project.devices} == expected
    project.close()
    export_knxproj(tmp_path / "project.xknx", tmp_path / "export.knxproj", schema="20")
    project.import_knxproj(tmp_path / "export.knxproj", tmp_path / "import.xknx")
    assert sorted(blob(d) for d in project.devices) == sorted(expected.values())
    assert all(d.native_error is None for d in project.devices)
    assert all(c.db_id is not None for d in project.devices for c in d.com_objects)


def test_setup_defaults_and_rejected_imports_are_atomic(project):
    import json
    from dataclasses import replace

    for device in project.devices:
        empty = replace(device, binary_data=[])
        capabilities = PROVIDER.capabilities(empty)
        assert capabilities.supported and not capabilities.configured
        config = PROVIDER.setup(empty)
        assert parse_settings(config.data) is not None
        assert config.objects
        original = blob(device)
        cursor = project.cursor
        exported = json.loads(project.export_plugin_config(device))
        exported["values"]["unknown"] = "1"
        with pytest.raises(ValueError, match="Unknown parameter"):
            project.import_plugin_config(device, json.dumps(exported).encode())
        assert blob(current(project, device.node_id)) == original
        assert project.cursor == cursor


@pytest.mark.parametrize("without_plugin", [False, True])
async def test_invalid_configuration_never_reaches_bus(
    project, monkeypatch, without_plugin
):
    from unittest.mock import AsyncMock

    from editor_gui import programming
    from editor_gui.plugins.connection.service import ConnectionService

    device = project.devices[0]
    device.native_error = "Invalid Siemens configuration"
    bus = AsyncMock()
    monkeypatch.setattr(programming, "download", bus)
    connection = ConnectionService()
    connect = AsyncMock()
    monkeypatch.setattr(connection, "connect_for_operation", connect)
    with pytest.raises(ValueError, match="Invalid Siemens configuration"):
        connection.program_device(device, without_plugin=without_plugin)
    with pytest.raises(
        programming.DeviceProgrammingError, match="Invalid Siemens configuration"
    ):
        await programming.download_device(None, device)
    bus.assert_not_called()
    connect.assert_not_called()


def test_all_native_parameter_forms_render_with_original_choices(project):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui.native_plugin import NativePluginPanel
    from editor_gui.plugins.project.ui.plugin_params_panel import PluginParamsPanel

    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(900, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        parameters = PluginParamsPanel(lambda *args: None)
        native = NativePluginPanel()
        for device in project.devices:
            sections = PROVIDER.sections(device)
            for section in sections:
                for parameter in section.parameters:
                    if parameter.choices:
                        assert parameter.value in dict(parameter.choices)
            for width in (900, 420):
                imgui.new_frame()
                imgui.set_next_window_size(imgui.ImVec2(width, 650))
                imgui.begin("Plugin Parameters")
                native.render(device, project)
                parameters.render(device)
                imgui.end()
                imgui.end_frame()
    finally:
        imgui.destroy_context(context)


@pytest.mark.parametrize("running", [True, False])
async def test_complete_images_and_load_plan(project, monkeypatch, tmp_path, running):
    from unittest.mock import AsyncMock

    from xknx.telegram.apci import MemoryWrite, PropertyValueWrite

    from packages.download.tests.conftest import FakeDevice
    from xknxeditor.download.download import prepared_controls
    from xknxeditor.download.load_state import LoadState
    from xknxeditor.download.procedure import LoadProcedureRunner
    from xknxeditor.download.programmer import DeviceProgrammer
    from xknxeditor.download.scope import DownloadScope
    from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import (
        LdCtrlRelSegment,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
    from xknxeditor.prod import parse_master_xml

    with ZipFile(ARCHIVE) as archive:
        master = parse_master_xml(archive.read("knx_master.xml"), tmp_path / "cache")
    mapping = {0: 0, 1: 1, 2: 2, 3: 9, 4: 3, 5: 4, 6: 0xFFFF}
    monkeypatch.setattr("xknxeditor.download.procedure.asyncio.sleep", AsyncMock())
    for original in list(project.devices):
        if not original.individual_address:
            project.set_device_individual_address(
                original.node_id, "", f"1.1.{original.node_id}"
            )
        device = current(project, original.node_id)
        for co in device.com_objects:
            ga = project.create_group_address_value(
                len(project.group_addresses) + 1, co.name
            )
            project.link_com_object_to_ga(co.db_id, ga)
        device = current(project, original.node_id)
        prepared = PROVIDER.prepare(device, project.group_communication_for(device))
        controls = prepared_controls(device.app, prepared, master)
        prepared.check_scope(DownloadScope.FULL, writing=True)
        allocations = [c for c in controls if isinstance(c, LdCtrlRelSegment)]
        assert [c.lsm_idx for c in allocations].count(4) == 2
        assert all(c.size != 65536 for c in allocations)
        assert not any(
            isinstance(c, LdCtrlWriteProp) and c.prop_id == 27 for c in controls
        )
        identity_writes = [
            c for c in controls if isinstance(c, LdCtrlWriteProp) and c.prop_id == 13
        ]
        assert len(identity_writes) == 1
        assert identity_writes[0].inline_data == bytes.fromhex(
            device.app.id[2:6] + device.app.id[9:13] + device.app.id[14:16]
        )
        for c in allocations:
            assert c.size == prepared.image.allocation_sizes.get(
                c.lsm_idx, prepared.resource_sizes[c.lsm_idx]
            )
        writes = [c for c in controls if isinstance(c, LdCtrlWriteMem)]
        assert writes and all(c.size <= 10 for c in writes)
        fake = FakeDevice(mapping, 0x07B0)
        fake.table_references = {1: 0x1000, 2: 0x2000, 3: 0x3000, 4: 0x4000}
        fake.load_states[4] = LoadState.LOADED if running else LoadState.UNLOADED
        fake.properties[4, 6] = bytes([int(running)])
        fake.properties[0, 78] = sources(device).description.hardware_types[-1]
        fake.properties[4, 13] = b"\x00\x01\x98\x20\x01"
        fake.properties[4, 27] = b"\0" * 8
        fake.properties[6, 205] = b"\0"
        fake.property_element_sizes.update(
            {(0, 78): 6, (4, 13): 5, (4, 27): 8, (6, 205): 1}
        )
        memory = prepared.image.segments[0]
        fake.memory.update({memory.address + i: 0xC7 for i in range(len(memory.data))})
        runner = LoadProcedureRunner(
            device.app,
            prepared.image,
            DeviceProgrammer(fake),
            controls=controls,
            object_types=mapping,
        )
        report = await runner.preflight()
        assert report.segments
        assert not any(
            isinstance(p, (MemoryWrite, PropertyValueWrite)) for p in fake.sent
        )
        fake.sent.clear()
        await runner.run()
        for i, (value, mask) in enumerate(zip(memory.data, memory.mask, strict=True)):
            assert fake.memory[memory.address + i] == (value if mask else 0xC7)
        for i in (1, 2, 3):
            base = fake.table_references[i]
            assert (
                bytes(fake.memory[base + n] for n in range(prepared.resource_sizes[i]))
                == prepared.image.object_segments[i]
            )
        assert fake.properties[6, 205] == b"\x10"
        assert fake.restarted
        status = [
            p.data
            for p in fake.sent
            if isinstance(p, PropertyValueWrite)
            and p.object_index == 6
            and p.property_id == 205
        ]
        assert status == ([b"\x5a", b"\x10"] if running else [b"\x10"])
        assert not any(
            isinstance(p, PropertyValueWrite) and p.property_id == 27 for p in fake.sent
        )


def test_completed_download_updates_blob_and_status_atomically(project):
    from types import SimpleNamespace

    from xknxeditor.vendor.siemens_generic.image import compile_image

    device = project.devices[0]
    previous = blob(device)
    modified = project.get_device_info(device.node_id).last_modified
    prepared = PROVIDER.prepare(device, project.group_communication_for(device))
    segment_info = bytes.fromhex("00007f0011223344")
    result = SimpleNamespace(
        binary_updates=prepared.completion({(4, 27, 1): segment_info})
    )
    project.record_download(device.node_id, "full", result=result)
    updated = current(project, device.node_id)
    assert project.get_device_info(device.node_id).last_modified == modified
    downloaded = parse_settings(blob(updated))
    compiled = compile_image(
        sources(device).description,
        parse_settings(previous),
        {"DeviceOrderNumber": device.order_number},
    )
    assert downloaded.memory_image == compiled.data
    assert downloaded.memory_mask == compiled.mask
    assert (
        ET.fromstring(decode_text(blob(updated))).find("DownloadedSettings").get("Name")
        == segment_info.hex().upper()
    )
    assert downloaded.values == parse_settings(previous).values
    project.undo()
    assert blob(current(project, device.node_id)) == previous
    project.redo()
    assert (
        parse_settings(blob(current(project, device.node_id))).memory_image
        == compiled.data
    )
    before = project.cursor
    with pytest.raises(ValueError, match="changed while programming"):
        project.record_download(device.node_id, "full", result=result)
    assert project.cursor == before


@pytest.mark.parametrize("wrong_application", [False, True])
async def test_incompatible_hardware_and_missing_link_fail_before_writes(
    project, tmp_path, monkeypatch, wrong_application
):
    from dataclasses import replace

    from xknx.telegram.apci import MemoryWrite, PropertyValueWrite

    from packages.download.tests.conftest import FakeDevice
    from xknxeditor.download.download import prepared_controls
    from xknxeditor.download.errors import CompareMismatch
    from xknxeditor.download.procedure import LoadProcedureRunner
    from xknxeditor.download.programmer import DeviceProgrammer
    from xknxeditor.download.project_data import GroupObjectLink
    from xknxeditor.prod import parse_master_xml

    device = project.devices[0]
    gc = project.group_communication_for(device)
    with pytest.raises(ValueError, match="no compatible native object role"):
        PROVIDER.prepare(
            device, replace(gc, links=[GroupObjectLink("unknown", 1, True)])
        )
    prepared = PROVIDER.prepare(device, gc)
    if wrong_application:
        from editor_gui.plugin_providers import siemens_generic

        source = sources(device)
        monkeypatch.setattr(
            siemens_generic,
            "sources",
            lambda _: replace(
                source,
                description=replace(
                    source.description, application_ids=("00019820**",)
                ),
            ),
        )
        prepared = PROVIDER.prepare(device, gc)
    with ZipFile(ARCHIVE) as archive:
        master = parse_master_xml(archive.read("knx_master.xml"), tmp_path / "cache")
    controls = prepared_controls(device.app, prepared, master)
    fake = FakeDevice({0: 0, 1: 1, 2: 2, 3: 9, 4: 3, 5: 4, 6: 0xFFFF}, 0x07B0)
    fake.properties[0, 78] = (
        sources(device).description.hardware_types[0]
        if wrong_application
        else bytes.fromhex("010203040506")
    )
    fake.properties[4, 13] = bytes.fromhex("0007982002")
    fake.property_element_sizes[4, 13] = 5
    fake.property_element_sizes[0, 78] = 6
    runner = LoadProcedureRunner(
        device.app,
        prepared.image,
        DeviceProgrammer(fake),
        controls=controls,
        object_types=fake.object_types,
    )
    with pytest.raises(CompareMismatch):
        await runner.preflight()
    with pytest.raises(CompareMismatch):
        await runner.run()
    assert not any(isinstance(p, (MemoryWrite, PropertyValueWrite)) for p in fake.sent)


def test_link_order_number_flags_and_table_capacity(project):
    from dataclasses import replace

    from xknxeditor.download.errors import ImageError
    from xknxeditor.download.project_data import GroupObjectLink

    device = project.devices[0]
    co = device.com_objects[0]
    gc = project.group_communication_for(device)
    links = [GroupObjectLink(co.id, 0x1234, True), GroupObjectLink(co.id, 1, False)]
    prepared = PROVIDER.prepare(device, replace(gc, links=links))
    assert prepared.image.object_segments[1] == bytes.fromhex("000200011234")
    number = co.number.to_bytes(2, "big")
    assert (
        prepared.image.object_segments[2]
        == b"\0\x02\0\x02" + number + b"\0\x01" + number
    )
    offset = 2 + 2 * (co.number - 1)
    descriptor = prepared.image.object_segments[3][offset]
    assert descriptor & 4
    from editor_gui.device import ComObjectFlags

    flags = co.flags
    co.flags = ComObjectFlags(
        communication=flags.communication,
        read=not flags.read,
        write=flags.write,
        transmit=flags.transmit,
        update=flags.update,
        read_on_init=flags.read_on_init,
    )
    updated = PROVIDER.prepare(device, replace(gc, links=links))
    assert descriptor ^ updated.image.object_segments[3][offset] == 8
    empty = PROVIDER.prepare(device, replace(gc, links=[]))
    assert empty.image.object_segments[1] == empty.image.object_segments[2] == b"\0\0"
    assert not empty.image.object_segments[3][offset] & 4
    with pytest.raises(ImageError, match="MaxEntries=511"):
        PROVIDER.prepare(
            device,
            replace(
                gc, links=[GroupObjectLink(co.id, a, a == 1) for a in range(1, 513)]
            ),
        )


async def test_download_completion_glue_persists_only_after_success(
    project, monkeypatch
):
    from unittest.mock import AsyncMock

    from editor_gui import programming
    from xknxeditor.download.download import DownloadResult
    from xknxeditor.download.errors import DownloadError

    device = project.devices[0]
    raw = blob(device)
    segment = bytes.fromhex("00007f00aabbccdd")
    download = AsyncMock(
        return_value=DownloadResult(property_reads={(4, 27, 1): segment})
    )
    monkeypatch.setattr(programming, "download", download)
    monkeypatch.setattr(programming, "_route_limited", AsyncMock(return_value=15))
    result = await programming.download_device(
        None, device, group_communication=project.group_communication_for(device)
    )
    assert blob(current(project, device.node_id)) == raw
    assert result.binary_updates["BLOB_STRING"][0] == raw
    project.record_download(device.node_id, "full", result=result)
    assert (
        parse_settings(blob(current(project, device.node_id))).memory_image is not None
    )
    project.undo()
    download.side_effect = DownloadError("simulation failed")
    with pytest.raises(DownloadError, match="simulation failed"):
        await programming.download_device(
            None, device, group_communication=project.group_communication_for(device)
        )
    assert blob(current(project, device.node_id)) == raw


def test_memory_preview_uses_absolute_parameter_addresses(project, monkeypatch):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui.memory_preview import MemoryPreviewWindow

    device = project.devices[0]
    compiled = project.prepared_download(device)
    memory = compiled.image.segments[0]
    window = MemoryPreviewWindow(lambda: [device], lambda _: compiled)
    window.open(device)
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(900, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        imgui.new_frame()
        window.render()
        imgui.render()
        key = f"0x{memory.address:04X}"
        assert window._segments[key] == memory.data
        assert window._base_addrs[key] == memory.address
        assert "Object 4" not in window._segments
    finally:
        imgui.destroy_context(context)

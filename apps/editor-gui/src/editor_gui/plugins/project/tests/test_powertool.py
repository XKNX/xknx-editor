"""Native role rebinding, roundtrip state and the editor's read/write boundary."""

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from editor_gui.plugin_providers.powertool import configuration
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.prod import parse_application_xml
from xknxeditor.vendor.powertool import BLOB_NAME
from xknxeditor.vendor.powertool.family import FamilyConfig
from xknxeditor.vendor.powertool.native import compile_native, from_original, load

ROOT = (
    next(
        p
        for p in Path(__file__).parents
        if (p / "packages/download/tests/fixtures/powertool").exists()
    )
    / "packages/download/tests/fixtures/powertool"
)


def native_project(tmp_path, monkeypatch, service=None, variant="01"):
    app, pei = [
        parse_application_xml((ROOT / f"{v}.xml").read_bytes(), "M-0007")[0]
        for v in (["21", "31"] if variant == "01" else ["22", "32"])
    ]
    proj = service or ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "project.xknx")
    monkeypatch.setattr(proj, "_second_application_source", lambda _: lambda: pei)
    monkeypatch.setattr(proj, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(proj, "_application_source", lambda *args: None)
    node_id = proj.add_device("P-1", "HP-1", "Button", app)
    assert node_id is not None
    return proj, node_id


def current(proj, node_id):
    result = proj.find_device_by_node_id(node_id)
    assert result is not None
    return result


def assert_current_blob(proj, node_id):
    device = current(proj, node_id)
    data = proj.device_binary_data(device, "PT2EtsPlugin")
    assert data
    native = load(proj.export_plugin_config(device))
    restored = from_original(data)
    if isinstance(native, FamilyConfig):
        assert isinstance(restored, FamilyConfig)
        restored = replace(
            restored, program_ids=native.program_ids, mask_version=native.mask_version
        )
    assert compile_native(restored).pid == compile_native(native).pid
    assert proj.device_binary_data(device, "PowerTool-Original") is None
    return data


@pytest.mark.parametrize("variant", ["01", "02"])
async def test_pushbutton_programming_enabled(tmp_path, monkeypatch, variant):
    from unittest.mock import AsyncMock

    from editor_gui import programming
    from editor_gui.device_plugins import download_needs_plugin_confirmation

    project, node_id = native_project(tmp_path, monkeypatch, variant=variant)
    project.setup_plugin(current(project, node_id))
    device = current(project, node_id)
    assert not download_needs_plugin_confirmation(device)
    download = AsyncMock(return_value=object())
    monkeypatch.setattr(programming, "download", download)
    monkeypatch.setattr(programming, "_route_limited", AsyncMock(return_value=None))
    await programming.download_device(None, device)
    assert download.call_args.kwargs["prepared"].programming_allowed
    project.close()


def test_setup_updates_stored_format_and_undo_restores_original(tmp_path, monkeypatch):
    proj, nid = native_project(tmp_path, monkeypatch)
    dev = current(proj, nid)
    from .test_powertool_blob import original_blob

    original = original_blob("matrix/01-00001-default")
    proj.persist_device_blob(dev, "PT2EtsPlugin", original)
    assert proj.device_binary_data(current(proj, nid), "PT2EtsPlugin") == original
    before = len(proj.history())
    proj.setup_plugin(current(proj, nid))
    dev = current(proj, nid)
    assert len(proj.history()) == before + 1
    assert len(dev.get_visible_com_objects()) == 2
    assert all(co.db_id is not None for co in dev.com_objects)
    blob = proj.device_binary_data(dev, BLOB_NAME)
    active = assert_current_blob(proj, nid)
    dev.release_dynamic_ui()
    assert dev.get_com_object_modules()
    assert len(dev.get_visible_com_objects()) == 2
    proj.close()
    proj.open(tmp_path / "project.xknx")
    dev = current(proj, nid)
    assert proj.device_binary_data(dev, BLOB_NAME) == blob
    assert len(dev.get_visible_com_objects()) == 2
    assert all(co.db_id is not None for co in dev.com_objects)
    assert proj.device_binary_data(dev, "PT2EtsPlugin") == active
    proj.undo()
    assert proj.device_binary_data(current(proj, nid), BLOB_NAME) is None
    assert proj.device_binary_data(current(proj, nid), "PT2EtsPlugin") == original
    proj.redo()
    assert proj.device_binary_data(current(proj, nid), BLOB_NAME) == blob
    assert proj.device_binary_data(current(proj, nid), "PT2EtsPlugin") == active
    proj.close()


def test_role_shift_preserves_links_and_undo(tmp_path, monkeypatch):
    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    dev = current(proj, nid)
    led = dev.com_objects[1]
    # Use the public core link API to keep the fixture independent of UI selections.
    pid = proj._pid
    assert pid
    ga = proj.create_group_address("1/1/1", "LED")
    assert ga is not None
    assert led.db_id
    proj._svc.link_com_object(pid, led.db_id, ga)
    proj._bump(structural=False)
    old_id = led.db_id
    lost = proj.edit_plugin_parameter(
        current(proj, nid), "powertool:0:function", "dim_rocker"
    )
    assert lost == []
    dev = current(proj, nid)
    assert dev.com_objects[2].db_id == old_id
    assert dev.com_objects[2].number == 2
    assert proj._svc.com_object_links(pid, old_id)
    changed = proj.device_binary_data(dev, BLOB_NAME)
    proj.undo()
    dev = current(proj, nid)
    assert dev.com_objects[1].db_id == old_id
    assert proj._svc.com_object_links(pid, old_id)
    proj.redo()
    assert proj.device_binary_data(current(proj, nid), BLOB_NAME) == changed
    lost = proj.edit_plugin_parameter(
        current(proj, nid), "powertool:2:function", "inactive"
    )
    assert len(lost) == 1
    assert lost[0]["address"] == "1/1/1"
    assert not proj._svc.com_object_links(pid, old_id)
    proj.undo()
    assert proj._svc.com_object_links(pid, old_id)
    proj.close()


async def test_programming_guard_precedes_route_access(tmp_path, monkeypatch):
    from editor_gui.programming import DeviceProgrammingError, download_device

    proj, nid = native_project(tmp_path, monkeypatch)
    with pytest.raises(DeviceProgrammingError):
        await download_device(
            cast(Any, None), current(proj, nid), route_couplers=cast(Any, [1])
        )
    proj.close()


def test_export_import_keeps_both_blobs_and_bindings(tmp_path, monkeypatch):
    from xknxeditor.proj import export_knxproj, import_knxproj

    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    proj.edit_plugin_parameter(current(proj, nid), "powertool:0:function", "dim_rocker")
    active = assert_current_blob(proj, nid)
    linked = current(proj, nid).com_objects[2]
    ga = proj.create_group_address("1/2/3", "LED")
    assert ga is not None and linked.db_id is not None and proj._pid is not None
    proj._svc.link_com_object(proj._pid, linked.db_id, ga)
    before = proj.device_binary_data(current(proj, nid), BLOB_NAME)
    proj.close()
    export_knxproj(tmp_path / "project.xknx", tmp_path / "export.knxproj", schema="23")
    import_knxproj(tmp_path / "export.knxproj", tmp_path / "import.xknx")
    proj.open(tmp_path / "import.xknx")
    dev = proj.devices[0]
    assert proj.device_binary_data(dev, BLOB_NAME) == before
    assert proj.device_binary_data(dev, "PT2EtsPlugin") == active
    assert_current_blob(proj, dev.node_id)
    assert len(dev.get_visible_com_objects()) == 3
    assert all(co.db_id is not None for co in dev.com_objects)
    communication = proj.group_communication_for(dev)
    assert communication is not None and len(communication.links) == 1
    assert communication.links[0].com_object_ref_id == dev.com_objects[2].id
    proj.close()


@pytest.mark.parametrize("keyword", [False, True])
def test_connection_guard_blocks_without_plugin_and_auto_connect(
    tmp_path, monkeypatch, keyword
):
    from editor_gui.plugins.connection.service import ConnectionService

    proj, nid = native_project(tmp_path, monkeypatch)
    connection = ConnectionService()

    def forbidden(*args, **kwargs):
        raise AssertionError("bus access")

    monkeypatch.setattr(connection, "connect_for_operation", forbidden)
    with pytest.raises(ValueError, match="Set up"):
        if keyword:
            connection.program_device(device=current(proj, nid), without_plugin=True)
        else:
            connection.program_device(current(proj, nid), without_plugin=True)
    proj.close()


def test_config_import_failure_is_atomic(tmp_path, monkeypatch):
    from xknxeditor.vendor.powertool import default_config, to_json_bytes

    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    before = proj.device_binary_data(current(proj, nid), BLOB_NAME)
    cursor = proj.cursor
    with pytest.raises(ValueError):
        proj.import_plugin_config(
            current(proj, nid), to_json_bytes(default_config("02"))
        )
    assert proj.cursor == cursor
    assert proj.device_binary_data(current(proj, nid), BLOB_NAME) == before
    proj.close()


def test_gui_parameter_and_file_actions_share_service(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from imgui_bundle import imgui

    from editor_gui.plugin_params import collect_from_device, mapping_for
    from editor_gui.plugins.project.ui.native_plugin import NativePluginPanel
    from editor_gui.plugins.project.ui.plugin_params_panel import PluginParamsPanel
    from xknxeditor.vendor.powertool import edit_config, to_json_bytes

    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    dev = current(proj, nid)
    incoming = to_json_bytes(
        edit_config(configuration(dev), 0, "function", "dim_button")
    )
    source = tmp_path / "incoming.json"
    source.write_bytes(incoming)
    panel = NativePluginPanel()
    panel._target = (proj.generation, nid)
    panel._open = SimpleNamespace(ready=lambda: True, result=lambda: [str(source)])
    panel._poll(proj)
    assert not panel._error
    dev = current(proj, nid)
    assert configuration(dev).channels[0].function == "dim_button"
    panel._data = to_json_bytes(configuration(dev))
    output = tmp_path / "export.json"
    panel._save = SimpleNamespace(ready=lambda: True, result=lambda: str(output))
    panel._poll(proj)
    assert output.read_bytes() == panel._data
    mapping = mapping_for(dev.app.id)
    assert mapping
    rows = collect_from_device(dev, mapping)
    assert any(p.ref_id == "powertool:1:function" for s in rows for p in s.parameters)
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(400, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        generic = PluginParamsPanel(
            lambda device, ref, value: proj.edit_plugin_parameter(device, ref, value)
        )
        for _ in range(2):
            imgui.new_frame()
            imgui.begin("Native")
            panel.render(dev, proj, None)
            generic.render(dev)
            imgui.end()
            imgui.end_frame()
    finally:
        imgui.destroy_context(context)
    proj.close()


@pytest.mark.parametrize("state", ["missing", "invalid", "bindings"])
async def test_native_preflight_rejects_invalid_config_before_route(
    tmp_path, monkeypatch, state
):
    from dataclasses import replace

    from editor_gui.programming import eval_device
    from xknxeditor.vendor.powertool import to_json_bytes

    proj, nid = native_project(tmp_path, monkeypatch)
    if state != "missing":
        proj.setup_plugin(current(proj, nid))
        data = b"invalid JSON"
        if state == "bindings":
            data = to_json_bytes(
                replace(configuration(current(proj, nid)), bindings={})
            )
        proj.persist_device_blob(current(proj, nid), BLOB_NAME, data)
    with pytest.raises(ValueError):
        await eval_device(
            cast(Any, None), current(proj, nid), route_couplers=cast(Any, [1])
        )
    proj.close()


def test_flags_priority_and_status_survive_native_role_shift(tmp_path, monkeypatch):
    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    dev = current(proj, nid)
    co = dev.com_objects[1]
    proj.set_flag(dev, co.id, "read", False)
    proj.set_com_object_priority(dev, co.id, "High")
    before = proj.prepared_download(dev)
    assert before is not None
    before_objects = before.image.object_segments[3]
    proj.edit_plugin_parameter(current(proj, nid), "powertool:0:function", "dim_rocker")
    dev = current(proj, nid)
    assert dev.com_objects[2].db_id == co.db_id
    assert dev.com_objects[2].priority == "High"
    assert not dev.com_objects[2].flags.read
    after = proj.prepared_download(dev)
    assert after is not None
    assert after.image.object_segments[3][-2:] == before_objects[-2:]
    proj.undo()
    undone = proj.prepared_download(current(proj, nid))
    assert undone is not None
    assert undone.image == before.image
    proj.redo()
    redone = proj.prepared_download(current(proj, nid))
    assert redone is not None
    assert redone.image == after.image
    proj.close()


@pytest.mark.parametrize("address", ["", "bad"])
def test_preview_requires_valid_individual_address(tmp_path, monkeypatch, address):
    from editor_gui.plugin_providers import prepared_for

    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    dev = current(proj, nid)
    dev.individual_address = address
    with pytest.raises(ValueError, match="individual address"):
        prepared_for(dev)
    proj.close()


def test_new_device_after_undo_discards_native_redo(tmp_path, monkeypatch):
    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    proj.edit_plugin_parameter(current(proj, nid), "powertool:0:function", "dim_rocker")
    proj.undo()
    second = proj.add_device("P-1", "HP-1", "Second", current(proj, nid).app)
    assert second is not None
    proj.setup_plugin(current(proj, second))
    ids = [co.db_id for dev in proj.devices for co in dev.com_objects]
    assert len(ids) == len(set(ids))
    assert not proj.can_redo()
    proj.undo()
    proj.redo()
    ids = [co.db_id for dev in proj.devices for co in dev.com_objects]
    assert len(ids) == len(set(ids))
    assert configuration(current(proj, nid)).channels[0].function == "switch_rocker"
    proj.close()


def test_native_edit_undo_restores_download_status(tmp_path, monkeypatch):
    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    proj.set_device_commissioning(
        nid,
        application_program_loaded=True,
        parameters_loaded=True,
        communication_part_loaded=True,
        individual_address_loaded=True,
    )
    info = proj.get_device_info(nid)
    assert info is not None and info.application_program_loaded
    cursor = proj.cursor
    proj.edit_plugin_parameter(current(proj, nid), "powertool:0:function", "dim_rocker")
    info = proj.get_device_info(nid)
    assert info is not None
    assert not info.application_program_loaded
    assert not info.parameters_loaded
    assert not info.communication_part_loaded
    assert info.individual_address_loaded
    proj.undo()
    assert proj.cursor == cursor
    info = proj.get_device_info(nid)
    assert info is not None
    assert (
        info.application_program_loaded
        and info.parameters_loaded
        and info.communication_part_loaded
    )
    proj.redo()
    info = proj.get_device_info(nid)
    assert info is not None and not info.application_program_loaded
    proj.close()

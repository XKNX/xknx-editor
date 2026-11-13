"""Stored plugin inspection and explicit native conversion through shared UI services."""

import pytest

from editor_gui.plugin_params import collect_from_device, mapping_for_device
from editor_gui.plugin_providers.powertool import PROVIDER, configuration
from packages.vendor.tests.powertool_fixtures import open_archive
from xknxeditor.vendor.powertool import BLOB_NAME

from .test_powertool import current, native_project


def original_blob(case="matrix/01-00018-App_DimmenWippeTBA"):
    with open_archive("original-projects.zip") as archive:
        return archive.read(case + "/project.blob")


def test_stored_configuration_is_selected_without_persistence_then_edited(
    tmp_path, monkeypatch
):
    project, nid = native_project(tmp_path, monkeypatch)
    data = original_blob()
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
    device = current(project, nid)
    mapping = mapping_for_device(device)
    assert mapping is not None
    before = project.cursor
    rows = [p for s in collect_from_device(device, mapping) for p in s.parameters]
    assert rows and all(p.writable for p in rows)
    assert any(p.value == "dim_rocker" for p in rows)
    capabilities = PROVIDER.capabilities(device)
    assert capabilities.importable_blob == "PT2EtsPlugin"
    assert (
        capabilities.preflight
        and capabilities.configured
        and capabilities.stored_configuration
    )
    assert project.device_binary_data(device, BLOB_NAME) is None
    assert project.export_plugin_config(device)
    assert project.prepared_download(device)
    assert project.cursor == before
    assert (
        project.edit_plugin_parameter(device, "powertool:0:function", "dim_rocker")
        == []
    )
    assert project.cursor == before
    project.edit_plugin_parameter(device, "powertool:0:function", "switch_rocker")
    assert project.cursor == before + 1
    device = current(project, nid)
    assert configuration(device).channels[0].function == "switch_rocker"
    assert project.device_binary_data(device, "PT2EtsPlugin") == data
    assert PROVIDER.capabilities(device).preflight
    assert PROVIDER.capabilities(device).programming_error is None
    converted = project.device_binary_data(device, BLOB_NAME)
    project.undo()
    assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
    assert configuration(current(project, nid)).channels[0].function == "dim_rocker"
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == data
    project.redo()
    assert project.device_binary_data(current(project, nid), BLOB_NAME) == converted
    project.close()
    project.open(tmp_path / "project.xknx")
    assert configuration(current(project, nid)).channels[0].function == "switch_rocker"
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == data
    project.close()


def test_blob_identity_detects_other_family_without_granting_programming(
    tmp_path, monkeypatch
):
    project, nid = native_project(tmp_path, monkeypatch)
    device = current(project, nid)
    monkeypatch.setattr(device.app, "_id", "M-0007_A-9999-21-0000")
    extension = device.app.program.static.extension
    for name in ("ets_download_plugin", "ets_ui_plugin", "ets_data_handler"):
        monkeypatch.setattr(extension, name, None)
    project.persist_device_blob(
        device,
        "PT2EtsPlugin",
        original_blob("generic/ToolBSmartTouchDatabase/SmartTouch"),
    )
    device = current(project, nid)
    mapping = mapping_for_device(device)
    assert mapping is not None and mapping.provider == "powertool"
    rows = [p for s in collect_from_device(device, mapping) for p in s.parameters]
    assert len(rows) > 100
    assert not any(p.writable for p in rows)
    capabilities = PROVIDER.capabilities(device)
    assert not capabilities.supported and not capabilities.preflight
    assert capabilities.programming_error
    before = project.cursor
    with pytest.raises(ValueError):
        project.import_stored_plugin_config(device)
    assert project.cursor == before
    project.close()


def test_mismatched_or_corrupt_original_cannot_be_converted(tmp_path, monkeypatch):
    project, nid = native_project(tmp_path, monkeypatch)
    for data in (
        b"\x00invalid",
        original_blob("generic/ToolBObjektTasterDatabase/TasterBA2f"),
    ):
        project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
        device = current(project, nid)
        assert PROVIDER.capabilities(device).importable_blob is None
        before = project.cursor
        with pytest.raises(ValueError):
            project.import_stored_plugin_config(device)
        assert project.cursor == before
        assert project.device_binary_data(device, BLOB_NAME) is None
    project.close()


def test_gui_selects_stored_without_an_import_button_or_mutation(tmp_path, monkeypatch):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.strings import S
    from editor_gui.plugins.project.ui.native_plugin import NativePluginPanel

    project, nid = native_project(tmp_path, monkeypatch)
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", original_blob())
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        buttons = []
        monkeypatch.setattr(
            imgui, "button", lambda label: buttons.append(label) or False
        )
        before = project.cursor
        imgui.new_frame()
        imgui.begin("Stored Configuration")
        NativePluginPanel().render(current(project, nid), project, None)
        imgui.end()
        imgui.end_frame()
        assert configuration(current(project, nid)).channels[0].function == "dim_rocker"
        assert project.cursor == before
        assert S.PLUGIN_NATIVE_SETUP not in buttons
        assert "Use Stored Plugin Configuration" not in buttons
        assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
    finally:
        imgui.destroy_context(context)
        project.close()


@pytest.mark.parametrize("without_plugin", [False, True])
async def test_original_blob_never_allows_unknown_device_to_reach_bus(
    tmp_path, monkeypatch, without_plugin
):
    from unittest.mock import AsyncMock

    from editor_gui import programming
    from editor_gui.plugins.connection.service import ConnectionService

    project, nid = native_project(tmp_path, monkeypatch)
    device = current(project, nid)
    monkeypatch.setattr(device.app, "_id", "M-0007_A-9999-21-0000")
    for name in ("ets_download_plugin", "ets_ui_plugin", "ets_data_handler"):
        monkeypatch.setattr(device.app.program.static.extension, name, None)
    project.persist_device_blob(
        device,
        "PT2EtsPlugin",
        original_blob("generic/ToolBSmartTouchDatabase/SmartTouch"),
    )
    route = AsyncMock()
    download = AsyncMock()
    monkeypatch.setattr(programming, "_route_limited", route)
    monkeypatch.setattr(programming, "download", download)
    with pytest.raises(programming.DeviceProgrammingError):
        await programming.download_device(None, current(project, nid))
    connection = ConnectionService()
    connect = AsyncMock()
    monkeypatch.setattr(connection, "connect_for_operation", connect)
    with pytest.raises(ValueError):
        connection.program_device(current(project, nid), without_plugin=without_plugin)
    connect.assert_not_called()
    route.assert_not_awaited()
    download.assert_not_awaited()
    project.close()


def test_imported_original_survives_project_archive_roundtrip(tmp_path, monkeypatch):
    from xknxeditor.proj import export_knxproj, import_knxproj

    project, nid = native_project(tmp_path, monkeypatch)
    data = original_blob()
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
    project.import_stored_plugin_config(current(project, nid))
    native = project.device_binary_data(current(project, nid), BLOB_NAME)
    output = tmp_path / "project.knxproj"
    project.close()
    export_knxproj(tmp_path / "project.xknx", output, schema="23")
    import_knxproj(output, tmp_path / "imported.xknx")
    project.open(tmp_path / "imported.xknx")
    device = project.devices[0]
    assert project.device_binary_data(device, "PT2EtsPlugin") == data
    assert project.device_binary_data(device, BLOB_NAME) == native
    assert configuration(device).channels[0].function == "dim_rocker"
    project.close()


def host_blob(device="DI-12"):
    with open_archive("host-projects.zip") as archive:
        return archive.read(device + ".blob")


def test_host_rtc_view_groups_settings_and_collapses_internal_values():
    from editor_gui.plugin_blob_readers.powertool import read_original

    sections = read_original(host_blob())
    rtc = next(s for s in sections if s.section == "2: RTC settings")
    assert all(not p.label.startswith("Parameter ") for p in rtc.parameters)
    assert not any(p.label == "Number of objects" for p in rtc.parameters)
    assert {p.group for p in rtc.parameters} >= {"General", "Control heating"}
    internal = next(
        s for s in sections if s.section.startswith("2: RTC settings:") and s.collapsed
    )
    assert any(
        p.label == "Number of objects" and p.value == "47" for p in internal.parameters
    )
    assert all(not p.writable for s in sections for p in s.parameters)
    mode = next(p for p in rtc.parameters if p.label == "Operating mode after reset")
    assert mode.raw_value == "3" and mode.value != "3"
    assert mode.default_value
    ids = [p.ref_id for s in sections for p in s.parameters]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("device_id", ["DI-1", "DI-2"])
def test_real_6108_saved_config_is_editable_without_rebinding_on_read(
    tmp_path, monkeypatch, device_id
):
    project, nid = native_project(tmp_path, monkeypatch, variant="02")
    data = host_blob(device_id)
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
    device = current(project, nid)
    before = project.cursor
    refs = list(device.com_object_instance_refs)
    assert PROVIDER.capabilities(device).preflight
    assert any(p.writable for s in PROVIDER.sections(device) for p in s.parameters)
    assert project.prepared_download(device)
    assert project.cursor == before
    assert device.com_object_instance_refs == refs
    assert project.device_binary_data(device, BLOB_NAME) is None
    assert project.device_binary_data(device, "PT2EtsPlugin") == data
    project.close()


def test_saved_read_preserves_links_and_first_edit_reports_detach_atomically(
    tmp_path, monkeypatch
):
    from xknxeditor.download import GroupCommunication
    from xknxeditor.download.project_data import GroupObjectLink

    project, nid = native_project(tmp_path, monkeypatch)
    original = original_blob()
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", original)
    pid = project._pid
    ref = PROVIDER.read(current(project, nid)).objects[0].reference
    project._svc.sync_device_com_objects(pid, nid, [(ref, None)])
    co = project._svc.devices(pid)[0].com_objects[0]
    co_id = co.id
    ga = project.create_group_address("1/1/1", "Stored link")
    project._svc.link_com_object(pid, co_id, ga)
    project._bump(structural=False)
    device = current(project, nid)
    before = project.cursor
    PROVIDER.sections(device)
    assert project._svc.com_object_links(pid, co_id)
    assert project.cursor == before
    with pytest.raises(ValueError, match="no verified native object roles"):
        PROVIDER.prepare(
            device, GroupCommunication(0x1101, [GroupObjectLink(ref, 2305, True)])
        )
    lost = project.edit_plugin_parameter(
        device, "powertool:0:function", "switch_rocker"
    )
    assert [link["address"] for link in lost] == ["1/1/1"]
    assert project.cursor == before + 1
    assert not project._svc.com_object_links(pid, co_id)
    project.undo()
    assert project._svc.com_object_links(pid, co_id)
    assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == original
    project.redo()
    assert not project._svc.com_object_links(pid, co_id)
    project.close()


def test_setup_does_not_replace_bad_stored_blob_with_defaults(tmp_path, monkeypatch):
    project, nid = native_project(tmp_path, monkeypatch)
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", b"invalid")
    before = project.cursor
    with pytest.raises(ValueError):
        project.setup_plugin(current(project, nid))
    assert project.cursor == before
    assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
    project.close()


@pytest.mark.parametrize("readable", [True, False])
def test_gui_reports_readable_profile_without_generic_unsupported_banner(
    tmp_path, monkeypatch, readable
):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui.native_plugin import NativePluginPanel

    data = host_blob("DI-3") if readable else b"invalid"
    project, nid = native_project(tmp_path, monkeypatch)
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
    messages = []
    monkeypatch.setattr(imgui, "text_disabled", messages.append)
    monkeypatch.setattr(imgui, "text_wrapped", messages.append)
    monkeypatch.setattr(imgui, "button", lambda *_: False)
    NativePluginPanel().render(current(project, nid), project, None)
    if readable:
        assert messages == [PROVIDER.capabilities(current(project, nid)).reason]
    else:
        assert "Invalid or oversized PowerTool blob" in messages
    project.close()


def test_device_parameters_have_a_distinct_section_identity(monkeypatch):
    from dataclasses import replace

    from editor_gui.plugin_blob_readers import powertool
    from xknxeditor.vendor.powertool.project_blob import read_project_blob

    data = host_blob()
    saved = read_project_blob(data)
    parameter = next(
        p
        for p in saved.channels[0].parameters
        if not p.internal and p.value is not None
    )
    monkeypatch.setattr(
        powertool,
        "read_project_blob",
        lambda _: replace(
            saved, parameters=(replace(parameter, path="devices/0/parameters/0"),)
        ),
    )
    sections = powertool.read_original(data)
    assert len({s.section for s in sections}) == len(sections)
    assert sections[0].collapsed
    assert not sections[1].collapsed

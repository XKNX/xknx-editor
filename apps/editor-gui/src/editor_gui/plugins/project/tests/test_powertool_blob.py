"""Stored plugin inspection and explicit native conversion through shared UI services."""

import pytest

from editor_gui.plugin_params import collect_from_device, mapping_for_device
from editor_gui.plugin_providers.powertool import PROVIDER, configuration
from packages.vendor.tests.powertool_fixtures import open_archive
from xknxeditor.vendor.powertool import BLOB_NAME

from .test_powertool import assert_current_blob, current, native_project


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
    assert project.device_binary_data(device, "PT2EtsPlugin") == data
    project.edit_plugin_parameter(device, "powertool:0:function", "switch_rocker")
    assert project.cursor == before + 1
    device = current(project, nid)
    assert configuration(device).channels[0].function == "switch_rocker"
    active = assert_current_blob(project, nid)
    assert active != data
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
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == active
    assert_current_blob(project, nid)
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


def test_current_stored_format_survives_project_archive_roundtrip(
    tmp_path, monkeypatch
):
    from xknxeditor.proj import export_knxproj, import_knxproj

    project, nid = native_project(tmp_path, monkeypatch)
    data = original_blob()
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
    project.import_stored_plugin_config(current(project, nid))
    active = assert_current_blob(project, nid)
    native = project.device_binary_data(current(project, nid), BLOB_NAME)
    output = tmp_path / "project.knxproj"
    project.close()
    export_knxproj(tmp_path / "project.xknx", output, schema="23")
    import_knxproj(output, tmp_path / "imported.xknx")
    project.open(tmp_path / "imported.xknx")
    device = project.devices[0]
    assert project.device_binary_data(device, "PT2EtsPlugin") == active
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
    project._refresh_device(nid)
    device = current(project, nid)
    assert PROVIDER.prepare(
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


def stored_linked_project(
    tmp_path, monkeypatch, service=None, case="matrix/01-00001-default"
):
    project, nid = native_project(tmp_path, monkeypatch, service)
    original = original_blob(case)
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", original)
    state = PROVIDER.read(current(project, nid))
    pid = project._pid
    project._svc.set_parameters(pid, nid, list(state.parameters.items()))
    project._svc.sync_device_com_objects(
        pid, nid, [(o.reference, None) for o in state.objects]
    )
    for co in project._svc.devices(pid)[0].com_objects:
        for index in range(2):
            ga = project.create_group_address(f"1/{co.id}/{index + 1}", "Stored link")
            project._svc.link_com_object(pid, co.id, ga)
    project._refresh_device(nid)
    project._bump(structural=False)
    return project, nid, original


@pytest.mark.parametrize("action", ["import", "parameter", "function"])
def test_first_conversion_preserves_compatible_links_and_undo(
    tmp_path, monkeypatch, action
):
    project, nid, original = stored_linked_project(tmp_path, monkeypatch)
    device = current(project, nid)
    pid = project._pid
    before = project.cursor
    before_links = project.group_communication_for(device).links
    ids = [co.id for co in project._svc.devices(pid)[0].com_objects]
    prepared = project.prepared_download(device)
    assert prepared is not None and before_links
    assert prepared.image.allocation_sizes[3] == 40
    assert project.cursor == before
    if action == "import":
        lost = project.import_stored_plugin_config(device)
    elif action == "parameter":
        lost = project.edit_plugin_parameter(
            device, "powertool:0:working_mode_of_the_rocker_switch_for_switching", "1"
        )
    else:
        lost = project.edit_plugin_parameter(
            device, "powertool:0:function", "dim_rocker"
        )
    assert project.cursor == before + 1
    if action == "function":
        assert len(lost) == 2
        led = current(project, nid).com_objects[2]
        assert led.db_id == ids[1]
        assert len(project._svc.com_object_links(pid, ids[1])) == 2
        assert not project._svc.com_object_links(pid, ids[0])
    else:
        assert lost == []
        assert (
            project.group_communication_for(current(project, nid)).links == before_links
        )
    if action == "import":
        assert project.prepared_download(current(project, nid)).image == prepared.image
    if action == "parameter":
        label = project.history()[0].display_text
        assert "Button" in label and "Working mode" in label
    native = project.device_binary_data(current(project, nid), BLOB_NAME)
    active = assert_current_blob(project, nid)
    project.undo()
    assert project.group_communication_for(current(project, nid)).links == before_links
    assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == original
    project.redo()
    assert project.device_binary_data(current(project, nid), BLOB_NAME) == native
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == active
    project.close()
    project.open(tmp_path / "project.xknx")
    assert project.prepared_download(current(project, nid))
    project.close()


def test_stored_flags_survive_conversion_and_explicit_override(tmp_path, monkeypatch):
    project, nid, _ = stored_linked_project(
        tmp_path, monkeypatch, case="modified-flags"
    )
    row = project._svc.devices(project._pid)[0].com_objects[0]
    row.read_flag = False
    row.read_flag_override = None
    row.priority = "High"
    project._refresh_device(nid)
    before = project.prepared_download(current(project, nid))
    assert before is not None
    assert project.import_stored_plugin_config(current(project, nid)) == []
    device = current(project, nid)
    assert device.com_objects[0].flags.read
    assert device.com_objects[0].priority == "High"
    assert project.prepared_download(device).image == before.image
    project.set_flag(device, device.com_objects[0].id, "read", False)
    project._refresh_device(nid)
    assert not current(project, nid).com_objects[0].flags.read
    assert project.prepared_download(current(project, nid)).image != before.image
    project.close()


def test_stored_live_overrides_convert_and_update_atomically(tmp_path, monkeypatch):
    project, nid, original = stored_linked_project(tmp_path, monkeypatch)
    device = current(project, nid)
    reference = PROVIDER.read(device).objects[0].reference
    before = project.prepared_download(device)
    project.set_flag(device, reference, "read", True)
    changed = project.prepared_download(device)
    assert changed.image != before.image
    active = assert_current_blob(project, nid)
    assert active != original
    project.undo()
    assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == original
    project.redo()
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == active
    project.set_com_object_priority(device, reference, "High")
    priority = project.prepared_download(device)
    assert priority.image != changed.image
    assert_current_blob(project, nid)
    project.set_com_object_datapoint_type(device, reference, "DPT-1")
    assert project.prepared_download(device).image == priority.image
    assert project.prepared_download(current(project, nid)).image == priority.image
    project.close()


def test_first_conversion_archive_roundtrip_preserves_links(tmp_path, monkeypatch):
    from xknxeditor.proj import export_knxproj, import_knxproj

    project, nid, _ = stored_linked_project(tmp_path, monkeypatch)
    before = project.prepared_download(current(project, nid))
    project.import_stored_plugin_config(current(project, nid))
    native = project.export_plugin_config(current(project, nid))
    active = assert_current_blob(project, nid)
    project.close()
    export_knxproj(
        tmp_path / "project.xknx", tmp_path / "converted.knxproj", schema="23"
    )
    import_knxproj(tmp_path / "converted.knxproj", tmp_path / "converted.xknx")
    project.open(tmp_path / "converted.xknx")
    device = project.devices[0]
    assert project.device_binary_data(device, "PT2EtsPlugin") == active
    assert project.export_plugin_config(device) == native
    assert project.prepared_download(device).image == before.image
    assert len(project.group_communication_for(device).links) == 4
    project.close()


@pytest.mark.parametrize("mismatch", ["duplicate", "dpt", "unknown"])
def test_stored_ambiguous_or_incompatible_links_block_preparation(
    tmp_path, monkeypatch, mismatch
):
    from xknxeditor.proj.models import ComObject

    project, nid, _ = stored_linked_project(tmp_path, monkeypatch)
    row = project._svc.devices(project._pid)[0]
    obj = row.com_objects[0]
    if mismatch == "duplicate":
        from sqlalchemy.orm import object_session

        session = object_session(row)
        session.add(ComObject(device_id=nid, ref_id=obj.ref_id))
        session.flush()
        session.expire(row, ["com_objects"])
    elif mismatch == "dpt":
        obj.datapoint_type_override = "DPST-5-1"
    else:
        obj.ref_id = "missing"
    project._refresh_device(nid)
    with pytest.raises(ValueError, match="no unique compatible"):
        project.prepared_download(current(project, nid))
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

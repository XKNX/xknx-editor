"""Shared mappings and provider substitution across project, UI and programming paths."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from editor_gui import plugin_providers, programming
from editor_gui.plugin_params import PluginMapping, mapping_for_device
from editor_gui.plugin_providers import (
    handles_parameter,
    prepared_for,
    programming_error,
    provider_for,
)
from editor_gui.plugin_providers.powertool import PROVIDER
from editor_gui.plugins.project.ui.native_plugin import NativePluginPanel
from xknxeditor.download.errors import UnsupportedProcedureError
from xknxeditor.vendor.powertool import BLOB_NAME

from .test_powertool import current, native_project


def test_shared_mapping_uses_plugin_identity_and_keeps_profile_gate(
    tmp_path, monkeypatch
):
    proj, nid = native_project(tmp_path, monkeypatch)
    device = current(proj, nid)
    mapping = mapping_for_device(device)
    assert mapping is not None and mapping.provider == "powertool"
    assert provider_for(device) is PROVIDER
    app = device.app
    original_id = app.id
    monkeypatch.setattr(app, "_id", "M-0007_A-9999-21-0000")
    try:
        assert mapping_for_device(device) is mapping
        assert provider_for(device) is PROVIDER
        capabilities = PROVIDER.capabilities(device)
        assert not capabilities.supported
        assert not capabilities.preflight
        assert capabilities.programming_error
        before = proj.cursor
        with pytest.raises(ValueError, match="unsupported"):
            proj.setup_plugin(device)
        assert proj.cursor == before
    finally:
        monkeypatch.setattr(app, "_id", original_id)
    proj.close()


def test_family_mapping_does_not_grant_unknown_version_support(tmp_path, monkeypatch):
    proj, nid = native_project(tmp_path, monkeypatch)
    device = current(proj, nid)
    monkeypatch.setattr(device.app, "_id", "M-0007_A-6108-99-0000")
    assert provider_for(device) is PROVIDER
    assert programming_error(device)
    with pytest.raises(ValueError):
        prepared_for(device)
    proj.close()


def test_unregistered_provider_never_falls_back(tmp_path, monkeypatch):
    from editor_gui import plugin_params

    proj, nid = native_project(tmp_path, monkeypatch)
    device = current(proj, nid)
    monkeypatch.setattr(
        plugin_params,
        "mapping_for_device",
        lambda _: PluginMapping((), "Unavailable", (), provider="missing"),
    )
    assert "unavailable" in programming_error(device)
    with pytest.raises(ValueError, match="unavailable"):
        prepared_for(device)
    proj.close()


def test_orphaned_native_blob_never_falls_back(tmp_path, monkeypatch):
    from editor_gui import plugin_params

    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    device = current(proj, nid)
    monkeypatch.setattr(plugin_params, "mapping_for_device", lambda _: None)
    assert "no matching" in programming_error(device)
    with pytest.raises(ValueError, match="no matching"):
        prepared_for(device)
    proj.close()


async def test_another_registered_provider_uses_all_shared_paths(tmp_path, monkeypatch):
    from imgui_bundle import imgui

    from editor_gui import plugin_params

    calls = []

    class ExampleProvider:
        id = "example"
        blob_name = BLOB_NAME
        parameter_prefix = "example:"

        def capabilities(self, device):
            return replace(
                PROVIDER.capabilities(device),
                programming_error="Example programming blocked",
            )

        def setup(self, device):
            calls.append("setup")
            return PROVIDER.setup(device)

        def read(self, device):
            calls.append("read")
            return PROVIDER.read(device)

        def decode(self, device, data):
            calls.append("decode")
            return PROVIDER.decode(device, data)

        def edit(self, device, ref_id, value):
            calls.append("edit")
            return PROVIDER.edit(
                device, ref_id.replace("example:", "powertool:"), value
            )

        def serialize(self, device, config, objects):
            return PROVIDER.serialize(device, config, objects)

        def sections(self, device):
            calls.append("sections")
            return PROVIDER.sections(device)

        def install_view(self, device):
            calls.append("view")
            PROVIDER.install_view(device)

        def prepare(self, device, communication):
            calls.append("prepare")
            return PROVIDER.prepare(device, communication)

    example = ExampleProvider()
    proj, nid = native_project(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin_providers, "providers", lambda: (example,))
    monkeypatch.setattr(
        plugin_params,
        "mapping_for_device",
        lambda _: PluginMapping((), "Example", (), provider="example"),
    )
    proj.setup_plugin(current(proj, nid))
    device = current(proj, nid)
    assert handles_parameter(device, "example:0:function")
    assert not handles_parameter(device, "powertool:0:function")
    active_mapping = plugin_params.mapping_for_device(device)
    assert active_mapping is not None
    assert plugin_params.collect_from_device(device, active_mapping)
    data = proj.export_plugin_config(device)
    proj.import_plugin_config(device, data)
    proj.edit_plugin_parameter(device, "example:0:function", "dim_rocker")
    device = current(proj, nid)
    assert len(device.get_visible_com_objects()) == 3
    prepared = proj.prepared_download(device)
    assert prepared is not None
    preflight = AsyncMock(return_value=object())
    monkeypatch.setattr(programming, "preflight", preflight)
    await programming.eval_device(None, device)
    assert preflight.call_args.kwargs["prepared"] is not None
    with pytest.raises(programming.DeviceProgrammingError, match="Example"):
        await programming.download_device(None, device)
    monkeypatch.setattr(
        example,
        "capabilities",
        lambda device: replace(PROVIDER.capabilities(device), programming_error=None),
    )
    download = AsyncMock(return_value=object())
    route = AsyncMock(return_value=None)
    monkeypatch.setattr(programming, "download", download)
    monkeypatch.setattr(programming, "_route_limited", route)
    blocked = replace(
        prepared, programming_allowed=False, programming_reason="Example gate"
    )
    monkeypatch.setattr(example, "prepare", lambda *_: blocked)
    with pytest.raises(UnsupportedProcedureError, match="Example gate"):
        await programming.download_device(None, device)
    route.assert_not_awaited()
    download.assert_not_awaited()
    writable = replace(prepared, programming_allowed=True)
    monkeypatch.setattr(example, "prepare", lambda *_: writable)
    await programming.download_device(None, device)
    assert download.call_args.kwargs["prepared"] is writable
    assert download.call_args.kwargs["image"] is writable.image
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(400, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        imgui.new_frame()
        imgui.begin("Example")
        NativePluginPanel().render(device, proj, None)
        imgui.end()
        imgui.end_frame()
    finally:
        imgui.destroy_context(context)
    assert {"setup", "read", "decode", "edit", "view", "prepare", "sections"} <= set(
        calls
    )
    proj.undo()
    assert len(current(proj, nid).get_visible_com_objects()) == 2
    proj.close()


def test_invalid_config_can_be_replaced_without_trusting_old_roles(
    tmp_path, monkeypatch
):
    proj, nid = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, nid))
    device = current(proj, nid)
    data = proj.export_plugin_config(device)
    obj = device.com_objects[0]
    ga = proj.create_group_address("1/1/1", "Switch")
    proj._svc.link_com_object(proj._pid, obj.db_id, ga)
    proj.persist_device_blob(device, BLOB_NAME, b"invalid")
    device = current(proj, nid)
    capabilities = PROVIDER.capabilities(device)
    assert capabilities.configured
    assert capabilities.supported
    assert not capabilities.preflight
    assert capabilities.reason
    before = proj.cursor
    lost = proj.import_plugin_config(device, data)
    assert proj.cursor == before + 1
    assert [link["address"] for link in lost] == ["1/1/1"]
    assert PROVIDER.capabilities(current(proj, nid)).preflight
    assert not proj._svc.com_object_links(proj._pid, obj.db_id)
    proj.undo()
    assert proj.device_binary_data(current(proj, nid), BLOB_NAME) == b"invalid"
    assert proj._svc.com_object_links(proj._pid, obj.db_id)
    proj.close()

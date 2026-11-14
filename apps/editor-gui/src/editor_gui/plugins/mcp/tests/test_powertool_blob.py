"""Stored-blob inspection and conversion through MCP and batch."""

import pytest
from fastmcp import Client

from editor_gui.plugin_providers.powertool import configuration
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.tests.test_powertool import (
    assert_current_blob,
    current,
    native_project,
)
from editor_gui.plugins.project.tests.test_powertool_blob import (
    original_blob,
    stored_linked_project,
)
from xknxeditor.vendor.powertool import BLOB_NAME

from .test_server import _build_ctx, _data


async def test_mcp_imports_stored_blob_and_batch_rolls_back(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    project, nid, original = stored_linked_project(
        tmp_path,
        monkeypatch,
        ctx.api.project,
        case="matrix/01-00018-App_DimmenWippeTBA",
    )
    ctx.project_plugin = ProjectPlugin(ctx.api)
    links = project.group_communication_for(current(project, nid)).links
    async with Client(build_server(ctx)) as client:
        rows = await _data(client, "project_plugin_parameters", device_address="1.1.1")
        assert rows["sections"]
        assert all(p["writable"] for s in rows["sections"] for p in s["parameters"])
        capabilities = await _data(client, "project_plugin_capabilities", node_id=nid)
        assert capabilities["importable_blob"] == "PT2EtsPlugin"
        before = project.cursor
        result = await _data(
            client, "project_import_stored_plugin_config", device_address="1.1.1"
        )
        assert result == {"lost_links": []}
        assert project.group_communication_for(current(project, nid)).links == links
        assert project.cursor == before + 1
        assert configuration(current(project, nid)).channels[0].function == "dim_rocker"
        project.undo()
        before = project.cursor
        await _data(
            client,
            "project_batch",
            operations=[
                {"op": "import_stored_plugin_config", "params": {"node_id": nid}},
                {
                    "op": "set_plugin_parameter",
                    "params": {
                        "node_id": nid,
                        "ref_id": "powertool:0:function",
                        "value": "unknown",
                    },
                },
            ],
        )
        assert project.cursor == before
        assert project.group_communication_for(current(project, nid)).links == links
        assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
        assert (
            project.device_binary_data(current(project, nid), "PT2EtsPlugin")
            == original
        )
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "import_stored_plugin_config",
                    "params": {"device_address": "1.1.1"},
                },
            ],
        )
        assert result["applied"] == 1
        assert project.group_communication_for(current(project, nid)).links == links
        assert configuration(current(project, nid)).channels[0].function == "dim_rocker"
    project.close()


async def test_corrupt_original_reports_actionable_error_and_normalized_edit(
    tmp_path, monkeypatch
):
    ctx = _build_ctx(tmp_path)
    project, nid = native_project(tmp_path, monkeypatch, ctx.api.project)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", b"\x00invalid")
    async with Client(build_server(ctx)) as client:
        with pytest.raises(Exception, match="Invalid or oversized PowerTool blob"):
            await _data(client, "project_plugin_parameters", node_id=nid)
        project.persist_device_blob(
            current(project, nid),
            "PT2EtsPlugin",
            original_blob("matrix/01-00001-default"),
        )
        result = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=nid,
            ref_id="powertool:0:working_mode_of_the_rocker_switch_for_switching",
            value="00",
        )
        assert result["value"] == "0"
        assert result["changed"] is False
    project.close()


async def test_mcp_edits_saved_config_directly_and_batch_undoes_conversion(
    tmp_path, monkeypatch
):
    ctx = _build_ctx(tmp_path)
    project, nid = native_project(tmp_path, monkeypatch, ctx.api.project)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    original = original_blob()
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", original)
    before = project.cursor
    async with Client(build_server(ctx)) as client:
        capabilities = await _data(client, "project_plugin_capabilities", node_id=nid)
        assert capabilities["configured"] and capabilities["readable"]
        assert capabilities["stored_configuration"] and capabilities["preflight"]
        rows = await _data(client, "project_plugin_parameters", node_id=nid)
        assert any(
            p["value"] == "dim_rocker"
            for s in rows["sections"]
            for p in s["parameters"]
        )
        assert project.cursor == before
        response = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_plugin_parameter",
                    "params": {
                        "node_id": nid,
                        "ref_id": "powertool:0:function",
                        "value": "switch_rocker",
                    },
                },
                {
                    "op": "set_plugin_parameter",
                    "params": {
                        "node_id": nid,
                        "ref_id": "powertool:0:function",
                        "value": "invalid",
                    },
                },
            ],
        )
        assert response["applied"] == 0
        assert project.cursor == before
        assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
        assert (
            project.device_binary_data(current(project, nid), "PT2EtsPlugin")
            == original
        )
        result = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=nid,
            ref_id="powertool:0:function",
            value="switch_rocker",
        )
        assert result["changed"]
        assert project.cursor == before + 1
        assert (
            configuration(current(project, nid)).channels[0].function == "switch_rocker"
        )
        assert assert_current_blob(project, nid) != original
    project.close()


async def test_mcp_rtc_pages_raw_values_and_search_match_gui(tmp_path, monkeypatch):
    from editor_gui.plugin_blob_readers.powertool import read_original
    from editor_gui.plugins.project.tests.test_powertool_blob import host_blob

    ctx = _build_ctx(tmp_path)
    project, nid = native_project(tmp_path, monkeypatch, ctx.api.project)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    data = host_blob()
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
    gui = read_original(data)
    async with Client(build_server(ctx)) as client:
        result = await _data(client, "project_plugin_parameters", node_id=nid)
        assert [(s["section"], len(s["parameters"])) for s in result["sections"]] == [
            (s.section, len(s.parameters)) for s in gui
        ]
        rtc = next(s for s in result["sections"] if s["section"] == "2: RTC settings")
        assert any(p.get("group") == "Control heating" for p in rtc["parameters"])
        assert any(s.get("collapsed") for s in result["sections"])
        result = await _data(
            client, "project_plugin_parameters", node_id=nid, search="257"
        )
        assert any(
            p["value"] == "257" for s in result["sections"] for p in s["parameters"]
        )
    project.close()


async def test_mcp_archive_export_uses_plugin_writeback(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from editor_gui.plugins.project.plugin_export import binary_data_for_export
    from xknxeditor.proj import export_knxproj

    ctx = _build_ctx(tmp_path)
    project, nid, _ = stored_linked_project(tmp_path, monkeypatch, ctx.api.project)
    project.import_stored_plugin_config(current(project, nid))
    active_blob = assert_current_blob(project, nid)
    captured = {}

    def export(source, dest, **kwargs):
        assert kwargs["binary_data_transform"] is binary_data_for_export
        kwargs["fetch_master"] = False
        result = export_knxproj(source, dest, **kwargs)
        captured["result"] = result
        return result

    monkeypatch.setattr("xknxeditor.proj.export_knxproj", export)
    monkeypatch.setattr(
        "editor_gui.plugins.project.knxproj_manufacturer.collect_manufacturer_bundle",
        lambda *_: SimpleNamespace(extra_files={}, master_xml=None),
    )
    async with Client(build_server(ctx)) as client:
        await _data(
            client, "project_export_knxproj", dest=str(tmp_path / "mcp.knxproj")
        )
    import zipfile

    from xknxeditor.vendor.powertool.native import from_original

    with zipfile.ZipFile(tmp_path / "mcp.knxproj") as archive:
        active = next(n for n in archive.namelist() if n.endswith("_PT2EtsPlugin.dat"))
        assert not any(
            n.endswith("_PowerTool.2DOriginal.dat") for n in archive.namelist()
        )
        assert archive.read(active) == active_blob
        assert from_original(archive.read(active)).channels
    assert captured
    project.close()

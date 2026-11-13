"""Stored-blob inspection and conversion through MCP and batch."""

import pytest
from fastmcp import Client

from editor_gui.plugin_providers.powertool import configuration
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.tests.test_powertool import current, native_project
from editor_gui.plugins.project.tests.test_powertool_blob import original_blob
from xknxeditor.vendor.powertool import BLOB_NAME

from .test_server import _build_ctx, _data


async def test_mcp_imports_stored_blob_and_batch_rolls_back(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    project, nid = native_project(tmp_path, monkeypatch, ctx.api.project)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    original = original_blob()
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", original)
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
        assert (
            project.device_binary_data(current(project, nid), "PT2EtsPlugin")
            == original
        )
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

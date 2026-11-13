"""Generic panel configuration uses the shared service and atomic batch API."""

from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.tests.test_powertool import current
from editor_gui.plugins.project.tests.test_powertool_generic import generic_project
from xknxeditor.vendor.powertool.family import FamilyEvaluation
from xknxeditor.vendor.powertool.native import load

from .test_server import _build_ctx, _data


async def test_panel_configuration_batch_and_undo(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    project, node = generic_project(tmp_path, monkeypatch, service=ctx.api.project)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    async with Client(build_server(ctx)) as client:
        await _data(client, "project_setup_plugin", device_address="1.1.1")
        original = (await _data(client, "project_export_plugin_config", node_id=node))[
            "config_json"
        ]
        state = FamilyEvaluation(load(original.encode()))
        channel = next(
            c
            for c in state.channels
            if "App_Switch" in c.spec["applications"] and state.channel_exists(c)
        )
        reference = "powertool:family:" + channel.path
        data = await _data(client, "project_plugin_parameters", node_id=node)
        assert any(
            p["ref_id"] == reference and p["writable"]
            for section in data["sections"]
            for p in section["parameters"]
        )
        operation = {
            "op": "set_plugin_parameter",
            "params": {
                "device_address": "1.1.1",
                "ref_id": reference,
                "value": str(channel.spec["applications"].index("App_Switch")),
            },
        }
        cursor = project.cursor
        result = await _data(client, "project_batch", operations=[operation])
        assert result["applied"] and project.cursor == cursor + 1
        edited = project.export_plugin_config(current(project, node))
        assert edited != original.encode()
        project.undo()
        assert project.export_plugin_config(current(project, node)) == original.encode()
        project.redo()
        assert project.export_plugin_config(current(project, node)) == edited
        cursor = project.cursor
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "import_plugin_config",
                    "params": {
                        "node_id": node,
                        "config_json": original,
                    },
                },
                {
                    "op": "set_plugin_parameter",
                    "params": {
                        "node_id": node,
                        "ref_id": "powertool:family:missing",
                        "value": "0",
                    },
                },
            ],
        )
        assert result["rolled_back"] and project.cursor == cursor
        assert project.export_plugin_config(current(project, node)) == edited
        assert project.prepared_download(current(project, node)).programming_allowed
    project.close()


async def test_powerline_acknowledgement_mcp_and_batch(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    project, node = generic_project(
        tmp_path, monkeypatch, powerline=True, service=ctx.api.project
    )
    project.setup_plugin(current(project, node))
    co = current(project, node).get_visible_com_objects()[0]
    row = next(r for r in project._svc.devices(project._pid) if r.id == node)
    project._svc.set_fields(
        project._pid,
        [["segment", row.segment.id, {"medium_type": "MT-1", "domain_address": 0x12}]],
    )
    ga = project.create_group_address("1/2/3", "Ack")
    project.link_com_object_to_ga(co.db_id, ga)
    async with Client(build_server(ctx)) as client:
        result = await _data(
            client,
            "project_set_com_object_acknowledgement",
            device_address="1.1.1",
            com_object_ref_id=co.id,
            group_address="1/2/3",
            enabled=True,
        )
        assert result["acknowledges"]
        result = await _data(
            client,
            "project_get_com_object_links",
            node_id=node,
            com_object_ref_id=co.id,
        )
        assert result["items"][0]["acknowledges"]
        cursor = project.cursor
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_com_object_acknowledgement",
                    "params": {
                        "node_id": node,
                        "com_object_ref_id": co.id,
                        "group_address_id": ga,
                        "enabled": False,
                    },
                }
            ],
        )
        assert result["applied"] and project.cursor == cursor + 1
        assert not project.com_object_acknowledges(co.db_id, ga)
        project.undo()
        assert project.com_object_acknowledges(co.db_id, ga)
    project.close()

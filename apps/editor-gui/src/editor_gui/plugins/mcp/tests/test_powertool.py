"""Native setup, edit and batch use the project service transaction."""

from fastmcp import Client

from editor_gui.plugin_providers.powertool import configuration
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.tests.test_powertool import current, native_project

from .test_server import _build_ctx, _data


async def test_native_tools_share_project_state(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    proj, nid = native_project(tmp_path, monkeypatch, ctx.api.project)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    async with Client(build_server(ctx)) as client:
        capabilities = await _data(
            client, "project_plugin_capabilities", device_address="1.1.1"
        )
        assert capabilities["provider"] == "powertool"
        assert capabilities["supported"] is True
        assert capabilities["configured"] is False
        assert capabilities["preflight"] is False
        assert capabilities["programming_error"]
        assert (await _data(client, "project_setup_plugin", node_id=nid))[
            "lost_links"
        ] == []
        capabilities = await _data(client, "project_plugin_capabilities", node_id=nid)
        assert capabilities["configured"] is True
        assert capabilities["preflight"] is True
        initial = (await _data(client, "project_export_plugin_config", node_id=nid))[
            "config_json"
        ]
        sections = await _data(client, "project_plugin_parameters", node_id=nid)
        assert "powertool:0:function" in str(sections)
        before = len(proj.history())
        result = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=nid,
            ref_id="powertool:0:function",
            value="dim_rocker",
        )
        assert result["changed"]
        assert len(proj.history()) == before + 1
        assert configuration(current(proj, nid)).channels[0].function == "dim_rocker"
        await _data(
            client, "project_import_plugin_config", node_id=nid, config_json=initial
        )
        assert configuration(current(proj, nid)).channels[0].function == "switch_rocker"
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_plugin_parameter",
                    "params": {
                        "node_id": nid,
                        "ref_id": "powertool:0:function",
                        "value": "dim_button",
                    },
                }
            ],
        )
        assert result.get("applied") == 1, result
        assert configuration(current(proj, nid)).channels[0].function == "dim_button"
    proj.close()


async def test_native_batch_setup_import_and_rollback(tmp_path, monkeypatch):
    from xknxeditor.vendor.powertool import BLOB_NAME, default_config, to_json_bytes

    ctx = _build_ctx(tmp_path)
    proj, nid = native_project(tmp_path, monkeypatch, ctx.api.project)
    address = {"device_address": "1.1.1"}
    setup = {"op": "setup_plugin", "params": address}
    edit = {
        "op": "set_plugin_parameter",
        "params": {**address, "ref_id": "powertool:0:function", "value": "dim_rocker"},
    }
    invalid_import = {
        "op": "import_plugin_config",
        "params": {
            **address,
            "config_json": to_json_bytes(default_config("02")).decode(),
        },
    }
    before = proj.cursor
    async with Client(build_server(ctx)) as client:
        result = await _data(
            client, "project_batch", operations=[setup, edit, invalid_import]
        )
        assert result["rolled_back"] is True
        assert result["failed"] == 1
        assert result["ok"] == 2
        assert proj.cursor == before
        assert proj.device_binary_data(current(proj, nid), BLOB_NAME) is None
        valid_import = {
            "op": "import_plugin_config",
            "params": {
                "node_id": nid,
                "config_json": to_json_bytes(default_config("01")).decode(),
            },
        }
        result = await _data(
            client, "project_batch", operations=[setup, edit, valid_import]
        )
        assert result["applied"] is True
        assert result["ok"] == 3
        assert configuration(current(proj, nid)).channels[0].function == "switch_rocker"
        proj.undo()
        assert configuration(current(proj, nid)).channels[0].function == "dim_rocker"
        proj.redo()
        assert configuration(current(proj, nid)).channels[0].function == "switch_rocker"
    proj.close()


async def test_preview_enabled_and_corrupt_configuration_refusal(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET
    from types import SimpleNamespace
    from unittest.mock import Mock

    from xsdata.formats.dataclass.parsers import XmlParser

    from editor_gui.plugins.project.tests.test_powertool import ROOT
    from xknxeditor.namespaces.intermediate.mask_version_t import MaskVersion
    from xknxeditor.prod import MasterData

    root = ET.parse(ROOT / "mask.xml").getroot()
    for node in root.iter():
        node.tag = node.tag.split("}")[-1]
    mask = XmlParser().from_string(ET.tostring(root, encoding="unicode"), MaskVersion)
    master = MasterData(
        SimpleNamespace(mask_versions=SimpleNamespace(mask_version=[mask]))
    )

    ctx = _build_ctx(tmp_path)
    proj, nid = native_project(tmp_path, monkeypatch, ctx.api.project)
    proj.setup_plugin(current(proj, nid))
    connector = Mock(side_effect=AssertionError("bus access"))
    ctx.api.connection = SimpleNamespace(master=master, program_device=connector)
    async with Client(build_server(ctx)) as client:
        result = await _data(client, "project_preview_plugin", device_address="1.1.1")
        assert result["programming_allowed"] is True
        assert len(result["resources"]) == 5
        assert len(result["properties"]) == 8
        assert result["controls"]
        from xknxeditor.vendor.powertool import BLOB_NAME

        proj.persist_device_blob(current(proj, nid), BLOB_NAME, b"invalid")
        response = await client.call_tool(
            "connection_program_device",
            {"node_id": nid, "without_plugin": True},
            raise_on_error=False,
        )
        assert response.is_error
        connector.assert_not_called()
    proj.close()

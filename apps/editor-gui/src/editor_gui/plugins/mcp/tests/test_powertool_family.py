"""Family profiles expose the same editing and transaction semantics over MCP."""

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.tests.test_powertool import current
from editor_gui.plugins.project.tests.test_powertool_family import CASES, family_project
from xknxeditor.vendor.powertool import BLOB_NAME

from .test_server import _build_ctx, _data


@pytest.mark.parametrize(("case", "profile"), CASES)
async def test_family_mcp_edit_export_and_atomic_batch(
    tmp_path, monkeypatch, case, profile
):
    ctx = _build_ctx(tmp_path)
    project, node_id = family_project(
        tmp_path, monkeypatch, case, profile, ctx.api.project
    )
    ctx.project_plugin = ProjectPlugin(ctx.api)
    async with Client(build_server(ctx)) as client:
        capabilities = await _data(
            client, "project_plugin_capabilities", device_address="1.1.1"
        )
        assert capabilities["preflight"] and capabilities["programming_error"] is None
        assert capabilities["download_scopes"] == [
            "FULL",
            "INDIVIDUAL_ADDRESS",
            "OVERWRITE_INDIVIDUAL_ADDRESS",
        ]
        data = await _data(client, "project_plugin_parameters", node_id=node_id)
        rows = [p for s in data["sections"] for p in s["parameters"]]
        row = next(
            p
            for p in rows
            if p["writable"]
            and len(p.get("choices", [])) > 1
            and "/application/" in p["ref_id"]
        )
        choices = row["choices"]
        value = next(c["value"] for c in choices if c["value"] != row["value"])
        cursor = project.cursor
        result = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=node_id,
            ref_id=row["ref_id"],
            value=value,
        )
        assert result["changed"]
        assert project.cursor == cursor + 1
        exported = await _data(client, "project_export_plugin_config", node_id=node_id)
        assert (
            exported["config_json"]
            == project.export_plugin_config(current(project, node_id)).decode()
        )
        project.undo()
        assert project.device_binary_data(current(project, node_id), BLOB_NAME) is None
        cursor = project.cursor
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_plugin_parameter",
                    "params": {
                        "node_id": node_id,
                        "ref_id": row["ref_id"],
                        "value": value,
                    },
                },
                {
                    "op": "set_plugin_parameter",
                    "params": {
                        "node_id": node_id,
                        "ref_id": "powertool:family:missing",
                        "value": "0",
                    },
                },
            ],
        )
        assert result["rolled_back"]
        assert project.cursor == cursor
        assert project.device_binary_data(current(project, node_id), BLOB_NAME) is None
    project.close()


@pytest.mark.parametrize(("case", "profile"), CASES)
async def test_family_mcp_programs_without_plugin_override(
    tmp_path, monkeypatch, case, profile
):
    from concurrent.futures import Future
    from unittest.mock import Mock

    ctx = _build_ctx(tmp_path)
    project, node_id = family_project(
        tmp_path, monkeypatch, case, profile, ctx.api.project
    )
    connection = ctx.api.connection
    monkeypatch.setattr(connection, "can_reach_bus", lambda: True)
    future = Future()
    future.set_result(None)
    program = Mock(return_value=future)
    monkeypatch.setattr(connection, "program_device", program)
    async with Client(build_server(ctx)) as client:
        result = await _data(
            client, "connection_program_device", device_address="1.1.1", scope="FULL"
        )
        assert result["status"] == "programmed"
        assert result["node_id"] == node_id
        assert program.call_args.kwargs["without_plugin"] is False
        assert project.prepared_download(current(project, node_id)).programming_allowed
    project.close()


async def test_native_mcp_address_assignment_needs_no_configuration(
    tmp_path, monkeypatch
):
    from concurrent.futures import Future
    from unittest.mock import Mock

    ctx = _build_ctx(tmp_path)
    project, node_id = family_project(
        tmp_path, monkeypatch, "DI-3", "ObjektRTR", ctx.api.project
    )
    project.persist_device_blob(current(project, node_id), BLOB_NAME, b"invalid")
    monkeypatch.setattr(ctx.api.connection, "can_reach_bus", lambda: True)
    future = Future()
    future.set_result(None)
    program = Mock(return_value=future)
    monkeypatch.setattr(ctx.api.connection, "program_device", program)
    async with Client(build_server(ctx)) as client:
        result = await _data(
            client,
            "connection_program_device",
            node_id=node_id,
            scope="INDIVIDUAL_ADDRESS",
        )
        assert result["status"] == "programmed"
        assert program.call_args.args[1].individual_address_only
    project.close()

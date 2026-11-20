"""Mapped module fields share GUI edits and undo through MCP."""

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from xknxeditor.prod import parse_application_xml

from .test_server import _build_ctx, _data


async def test_unset_module_field_edit_read_only_and_undo(tmp_path, monkeypatch):
    fixture = Path(__file__).parents[5] / "tests/fixtures/dali_module_parameters.xml"
    app = parse_application_xml(fixture.read_bytes(), "M-0083")[0]
    ctx = _build_ctx(tmp_path)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    project = ctx.api.project
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    try:
        node = project.add_device("P-1", "HP-1", "DALI", app)
        assert node is not None
        first = f"{app.id}_MD-1_M-1_MI-1_P-1_R-1"
        second = f"{app.id}_MD-1_M-1_MI-2_P-1_R-1"
        locked = f"{app.id}_MD-1_M-1_MI-1_P-1_R-2"
        async with Client(build_server(ctx)) as client:

            async def rows():
                result = await _data(client, "project_plugin_parameters", node_id=node)
                return {
                    p["ref_id"]: p for s in result["sections"] for p in s["parameters"]
                }

            initial = await rows()
            assert initial[first]["value"] == "8"
            assert initial[locked]["writable"] is False
            before = project.cursor
            result = await _data(
                client,
                "project_set_plugin_parameter",
                node_id=node,
                ref_id=first,
                value="10",
            )
            assert result["changed"] and result["value"] == "10"
            assert project.cursor == before + 1
            changed = await rows()
            assert changed[second]["value"] == "8"
            with pytest.raises(Exception, match="cannot be changed"):
                await _data(
                    client,
                    "project_set_plugin_parameter",
                    node_id=node,
                    ref_id=locked,
                    value="10",
                )
            assert project.cursor == before + 1
            project.undo()
            assert (await rows())[first]["value"] == "8"
            project.redo()
            assert (await rows())[first]["value"] == "10"
    finally:
        project.close()

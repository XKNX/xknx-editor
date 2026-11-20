"""Recovered Siemens DALI-2 inventory is visible through shared GUI and MCP rows."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from editor_gui.plugin_blob_readers.siemens_dali2 import read
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.recover import siemens_dali2
from editor_gui.plugins.recover.tests.test_dali_recover import _recovered
from editor_gui.plugins.recover.tests.test_siemens_dali2_recover import (
    _application,
    _Programmer,
)

from .test_server import _build_ctx, _data


async def test_recovered_metadata_visible_in_gui_and_mcp(tmp_path, monkeypatch):
    app = _application(True)
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    node = project.add_device("P-1", "HP-1", "DALI-2", app)
    captured = await siemens_dali2.addin_reader(app)(_Programmer(2))
    siemens_dali2.apply_recovered(
        project, project.find_device_by_node_id(node), _recovered(app, captured)
    )
    try:
        device = project.find_device_by_node_id(node)
        blob = project.device_binary_data(device, "Dali2DcaBinaryData")
        mapping = project.plugin_mapping(device)
        assert mapping.blob_reader == "siemens_dali2"
        sections = project.plugin_sections(device, mapping)
        rows = [
            p
            for section in sections
            for p in section.parameters
            if p.ref_id.startswith("siemens_dali2:")
        ]
        assert rows and all(not row.writable for row in rows)
        linked = next(
            row
            for row in rows
            if row.ref_id.endswith("/2/LinkedToSensorNumber") and "Line2/" in row.ref_id
        )
        assert linked.value == "5"
        async with Client(build_server(ctx)) as client:
            result = await _data(client, "project_plugin_parameters", node_id=node)
            mcp_rows = [
                p
                for section in result["sections"]
                for p in section["parameters"]
                if p["ref_id"].startswith("siemens_dali2:")
            ]
            assert {p["ref_id"]: p["value"] for p in mcp_rows} == {
                p.ref_id: p.value for p in rows
            }
            assert all(not p["writable"] for p in mcp_rows)
            with pytest.raises(ToolError, match="cannot be changed"):
                await client.call_tool(
                    "project_set_plugin_parameter",
                    {"node_id": node, "ref_id": linked.ref_id, "value": "6"},
                )
        assert (
            project.device_binary_data(
                project.find_device_by_node_id(node), "Dali2DcaBinaryData"
            )
            == blob
        )
    finally:
        project.close()


@pytest.mark.parametrize(
    "payload", [b"garbage", b"[]", b"null", b'{"RuntimeDataEcgLine1":4}']
)
def test_invalid_blob_is_ignored(payload):
    assert read(payload, "Dali2DcaBinaryData") == []


def test_other_blob_is_not_decoded_and_unknown_fields_are_visible():
    payload = json.dumps({"RuntimeDataEcgLine1": {"7": {"NewValue": 12}}}).encode()
    assert read(payload, "other") == []
    row = read(payload, "Dali2DcaBinaryData")[0].parameters[0]
    assert row.ref_id == "siemens_dali2:RuntimeDataEcgLine1/7/NewValue"
    assert row.value == "12" and not row.writable

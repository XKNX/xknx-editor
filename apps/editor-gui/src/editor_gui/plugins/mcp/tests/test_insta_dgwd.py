"""DALI parameter exchange uses the same offline transaction over MCP and batches."""

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from xknxeditor.prod import parse_application_xml

from .test_server import _build_ctx, _data


@pytest.fixture
def setup(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    source = Path(__file__).parents[5] / "tests/fixtures/insta_dgwd_parameters.xml"
    app = parse_application_xml(source.read_bytes(), "M-0009")[0]
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    node = project.add_device("P-1", "HP-1", "DALI", app)
    yield ctx, node
    project.close()


def modified(xml):
    root = ET.fromstring(xml)
    root.find(".//ParametersColor/ParameterEntryColor").set("Value", "-16711165")
    return ET.tostring(root, encoding="unicode")


async def test_import_inline_and_host_file_is_undoable_with_revision(setup, tmp_path):
    ctx, node = setup
    project = ctx.api.project
    device = project.find_device_by_node_id(node)
    async with Client(build_server(ctx)) as client:
        before = await _data(client, "project_insta_dgwd_read", node_id=node)
        by_address = await _data(
            client, "project_insta_dgwd_read", device_address=device.individual_address
        )
        assert by_address == before
        after = await _data(
            client,
            "project_insta_dgwd_import",
            node_id=node,
            revision=before["revision"],
            xml=modified(before["xml"]),
        )
        assert after["revision"] != before["revision"]
        with pytest.raises(Exception, match="changed"):
            await _data(
                client,
                "project_insta_dgwd_import",
                node_id=node,
                revision=before["revision"],
                xml=before["xml"],
            )
        assert project.undo()
        assert await _data(client, "project_insta_dgwd_read", node_id=node) == before
        path = tmp_path / "upload.dgwd"
        path.write_text(modified(before["xml"]))
        assert (
            await _data(
                client,
                "project_insta_dgwd_import",
                device_address=device.individual_address,
                revision=before["revision"],
                path=str(path),
            )
            == after
        )


async def test_batch_dry_run_and_atomic_rollback(setup):
    ctx, node = setup
    async with Client(build_server(ctx)) as client:
        before = await _data(client, "project_insta_dgwd_read", node_id=node)
        operation = {
            "op": "import_insta_dgwd",
            "params": {
                "node_id": node,
                "revision": before["revision"],
                "xml": modified(before["xml"]),
            },
        }
        dry = await _data(client, "project_batch", operations=[operation], dry_run=True)
        assert dry["valid"]
        assert await _data(client, "project_insta_dgwd_read", node_id=node) == before
        failed = await _data(client, "project_batch", operations=[operation, operation])
        assert failed["failed"] == 1 and failed["rolled_back"]
        assert await _data(client, "project_insta_dgwd_read", node_id=node) == before
        done = await _data(client, "project_batch", operations=[operation])
        assert done["ok"]
        assert (await _data(client, "project_insta_dgwd_read", node_id=node))[
            "revision"
        ] != before["revision"]


@pytest.mark.parametrize("arguments", [{}, {"xml": "<x/>", "path": "/unused.dgwd"}])
async def test_import_requires_exactly_one_input(setup, arguments):
    ctx, node = setup
    async with Client(build_server(ctx)) as client:
        before = await _data(client, "project_insta_dgwd_read", node_id=node)
        with pytest.raises(Exception, match="exactly one"):
            await _data(
                client,
                "project_insta_dgwd_import",
                node_id=node,
                revision=before["revision"],
                **arguments,
            )
        assert await _data(client, "project_insta_dgwd_read", node_id=node) == before

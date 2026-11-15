"""Memory read tools use the same preview ranges and queue as the GUI."""

from concurrent.futures import Future

import pytest
from fastmcp import Client

from editor_gui.memory_read import MemoryReadResult
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import ProgramQueue

from .test_operations import _devices
from .test_server import _FIXTURE, _build_ctx, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="Product fixture absent")


async def test_memory_list_and_read_share_preview_without_project_edits(
    tmp_path, monkeypatch
):
    ctx = _build_ctx(tmp_path)
    plugin = ProjectPlugin(ctx.api)
    ctx.project_plugin = plugin
    requests = []

    def read(device, request):
        requests.append((device.node_id, request))
        result = MemoryReadResult(request, request.address, b"\xa5" * request.size)
        future = Future()
        future.set_result(result)
        return future

    monkeypatch.setattr(ctx.api.connection, "read_memory", read)
    plugin._program_queue = ProgramQueue(
        is_busy=lambda: False, start=lambda item: item.action.start()
    )
    async with Client(build_server(ctx)) as client:
        node = (await _devices(client, tmp_path, "1.1.5"))[0]
        before = ctx.api.project.cursor
        listing = await _data(client, "project_memory_segments", node_id=node)
        segment = listing["segments"][0]
        assert segment["size"] > 0
        result = await _data(
            client,
            "connection_read_memory",
            device_address="1.1.5",
            segment=segment["id"],
        )
        assert result["data"] == "a5" * segment["size"]
        assert result["segment"] == segment["id"]
        assert requests[0][0] == node
        assert ctx.api.project.cursor == before
        assert plugin.program_queue.history[0].origin == "MCP"
        with pytest.raises(Exception, match="Unknown memory segment"):
            await _data(
                client, "connection_read_memory", node_id=node, segment="missing"
            )
        assert len(requests) == 1
    ctx.api.project.close()

"""Classic Siemens bus reads support both lines and validate before connection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.mcp.tests.test_siemens_dali2_commissioning import setup
from xknxeditor.dali.siemens import SiemensN141BusState

from .test_server import _data


async def test_twin_line_can_be_read_through_mcp(tmp_path, monkeypatch):
    ctx, gateway, _, calls = setup(tmp_path, monkeypatch)
    gateway.app = SimpleNamespace(id="M-0001_A-9834-11-0000")
    scan = AsyncMock(return_value=SiemensN141BusState(line=1, object_index=36, ecgs=[]))
    monkeypatch.setattr("xknxeditor.dali.siemens.scan_n141_03", scan)
    async with Client(build_server(ctx)) as client:
        result = await _data(client, "connection_siemens_dali_scan", node_id=7, line=1)
    assert result["line"] == 1
    assert len(calls) == 1 and calls[0][2] is False
    assert scan.await_args.kwargs == {"line": 1}


@pytest.mark.parametrize("app,line", [("9837", 1), ("9834", 2), ("9834", -1)])
async def test_invalid_classic_line_does_not_open_connection(
    tmp_path, monkeypatch, app, line
):
    ctx, gateway, _, calls = setup(tmp_path, monkeypatch)
    gateway.app = SimpleNamespace(id=f"M-0001_A-{app}-11-0000")
    async with Client(build_server(ctx)) as client:
        with pytest.raises(ToolError, match="Invalid Siemens DALI line"):
            await _data(client, "connection_siemens_dali_scan", node_id=7, line=line)
    assert not calls

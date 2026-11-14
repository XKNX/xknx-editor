"""MCP tools for the browser UI's file folder: listed and deleted as the file dialogs do."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui import web
from editor_gui.plugins.mcp.server import build_server
from editor_gui.web.server import WebHub

from .test_server import _build_ctx, _data


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebHub:
    hub = WebHub(tmp_path / "files")
    hub.file_in_use = lambda path: path.name == "Haus.xknx"
    monkeypatch.setattr(web, "active_hub", hub)
    for name in ("export.knxproj", "Haus.xknx"):
        (hub.files.root / name).write_bytes(b"data")
    return hub


async def test_the_files_are_listed_and_deleted(tmp_path: Path, hub: WebHub) -> None:
    async with Client(build_server(_build_ctx(tmp_path))) as client:
        listed = await _data(client, "web_files_list")
        assert listed["folder"] == str(hub.files.root)
        files = {f["name"]: f for f in listed["files"]}
        assert files["export.knxproj"]["size"] == 4
        assert not files["export.knxproj"]["in_use"]
        assert files["Haus.xknx"]["in_use"]
        assert await _data(client, "web_file_delete", name="export.knxproj") == {
            "deleted": "export.knxproj"
        }
        with pytest.raises(Exception, match="belongs to the open project"):
            await client.call_tool("web_file_delete", {"name": "Haus.xknx"})
        with pytest.raises(Exception, match="no file"):
            await client.call_tool("web_file_delete", {"name": "../export.knxproj"})
    assert [e.name for e in hub.files.files()] == ["Haus.xknx"]


async def test_only_in_the_browser_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "active_hub", None)
    async with Client(build_server(_build_ctx(tmp_path))) as client:
        with pytest.raises(Exception, match="only in the browser UI"):
            await client.call_tool("web_files_list", {})

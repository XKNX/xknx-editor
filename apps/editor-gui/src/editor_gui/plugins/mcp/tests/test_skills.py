"""The skills provider exposes the bundled skill directory as an MCP resource.

Content is intentionally a placeholder for now (guidance lives in the server instructions); this only
guards that the provider is wired and the SKILL.md ships as package data."""

from __future__ import annotations

from pathlib import Path

from fastmcp import Client

from editor_gui.plugins.mcp.tests.test_server import _build


async def test_knx_skill_exposed_as_resource(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        assert "skill://knx-editing/SKILL.md" in uris, uris

        content = await client.read_resource("skill://knx-editing/SKILL.md")
        assert content[0].text.strip()  # type: ignore[union-attr]  # non-empty

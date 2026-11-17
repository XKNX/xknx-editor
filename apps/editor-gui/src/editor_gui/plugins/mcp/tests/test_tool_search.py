"""Tool Discovery: the full tool list, or a search for the tools (for AI models whose context is
too small for the whole list)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastmcp import Client

from editor_gui.plugins.mcp.server import PINNED_TOOLS, build_server

from .test_server import _build_ctx


def _server(tmp_path: Path, search: list[bool]) -> Any:
    ctx = _build_ctx(tmp_path)
    ctx.tool_search = lambda: search[0]
    return build_server(ctx)


async def _found(client: Client, query: str) -> list[str]:
    result = await client.call_tool("search_tools", {"query": query})
    payload = result.structured_content or json.loads(result.content[0].text)  # type: ignore[union-attr]
    tools = payload.get("result", payload) if isinstance(payload, dict) else payload
    return [tool["name"] for tool in tools]


async def test_all_tools_by_default(tmp_path: Path) -> None:
    async with Client(build_server(_build_ctx(tmp_path))) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert len(names) > 100
    assert "search_tools" not in names and "call_tool" not in names


async def test_search_lists_only_the_search(tmp_path: Path) -> None:
    async with Client(_server(tmp_path, [True])) as client:
        names = [tool.name for tool in await client.list_tools()]
        assert sorted(names) == sorted([*PINNED_TOOLS, "search_tools", "call_tool"])
        # The tools of a task are found by a few words of it.
        assert "connection_program_device" in await _found(
            client, "program device download"
        )
        assert "project_link_com_object" in await _found(
            client, "link group object group address"
        )
        # Every tool stays callable: through call_tool and directly.
        through = await client.call_tool(
            "call_tool", {"name": "status", "arguments": {}}
        )
        assert "editor" in json.dumps(through.structured_content)
        direct = await client.call_tool("project_list_devices", {})
        assert not direct.is_error


async def test_a_change_counts_without_a_restart(tmp_path: Path) -> None:
    search = [False]
    async with Client(_server(tmp_path, search)) as client:
        assert len(await client.list_tools()) > 100
        search[0] = True
        assert len(await client.list_tools()) == len(PINNED_TOOLS) + 2
        search[0] = False
        names = {tool.name for tool in await client.list_tools()}
        assert "search_tools" not in names and "project_list_devices" in names


async def test_every_tool_is_found_by_its_exact_name(tmp_path: Path) -> None:
    """The hard bar for Tool Discovery: a search for a tool's exact name always returns it (BM25
    alone splits the name on underscores and misses some). The pinned tools are not searched for."""
    async with Client(build_server(_build_ctx(tmp_path))) as listing:
        all_names = {tool.name for tool in await listing.list_tools()}
    searchable = sorted(n for n in all_names if n not in PINNED_TOOLS)
    assert len(searchable) > 100
    async with Client(_server(tmp_path, [True])) as client:
        for name in searchable:
            assert name in await _found(client, name), name


async def test_a_query_whose_words_are_all_in_a_name_finds_it(tmp_path: Path) -> None:
    async with Client(_server(tmp_path, [True])) as client:
        # "program device" -> connection_program_device, even though "program device download"
        # already works through BM25.
        assert "connection_program_device" in await _found(client, "program device")
        assert "project_list_devices" in await _found(client, "list devices")

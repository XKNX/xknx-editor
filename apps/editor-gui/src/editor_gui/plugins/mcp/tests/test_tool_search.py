"""Tool Discovery: full definitions, search, or a compact inventory with search."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools.base import Tool

from editor_gui.plugins.mcp.context import tool_annotations
from editor_gui.plugins.mcp.server import (
    PINNED_TOOLS,
    SEARCH_RESULTS,
    ToolSearch,
    _tool_inventory,
    build_server,
)

from .test_server import _build_ctx


def _server(tmp_path: Path, mode: list[str]) -> FastMCP:
    ctx = _build_ctx(tmp_path)
    ctx.tool_discovery = lambda: mode[0]
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
    assert not {"search_tools", "call_tool", "tools_catalog"} & names


async def test_search_lists_only_the_search(tmp_path: Path) -> None:
    async with Client(_server(tmp_path, ["search"])) as client:
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
    mode = ["all"]
    async with Client(_server(tmp_path, mode)) as client:
        assert len(await client.list_tools()) > 100
        mode[0] = "search"
        assert len(await client.list_tools()) == len(PINNED_TOOLS) + 2
        mode[0] = "catalog"
        assert len(await client.list_tools()) == len(PINNED_TOOLS) + 3
        assert "project_batch [" in (await client.call_tool("tools_catalog")).data
        mode[0] = "search"
        assert len(await client.list_tools()) == len(PINNED_TOOLS) + 2
        mode[0] = "all"
        names = {tool.name for tool in await client.list_tools()}
        assert "search_tools" not in names and "project_list_devices" in names
        assert "tools_catalog" not in names


@pytest.mark.parametrize("mode", ["search", "catalog"])
async def test_every_tool_is_found_by_its_exact_name(tmp_path: Path, mode: str) -> None:
    """The hard bar for Tool Discovery: a search for a tool's exact name always returns it (BM25
    alone splits the name on underscores and misses some). The pinned tools are not searched for."""
    async with Client(build_server(_build_ctx(tmp_path))) as listing:
        all_names = {tool.name for tool in await listing.list_tools()}
    searchable = sorted(n for n in all_names if n not in PINNED_TOOLS)
    assert len(searchable) > 100
    async with Client(_server(tmp_path, [mode])) as client:
        for name in searchable:
            assert name in await _found(client, name), name


@pytest.mark.parametrize("mode", ["search", "catalog"])
@pytest.mark.parametrize(
    "query",
    [
        "connection_program_device",
        "connection program device",
        "  CONNECTION-program_DEVICE  ",
    ],
)
async def test_exact_name_returns_only_one_tool(
    tmp_path: Path, mode: str, query: str
) -> None:
    async with Client(_server(tmp_path, [mode])) as client:
        assert await _found(client, query) == ["connection_program_device"]


@pytest.mark.parametrize("mode", ["search", "catalog"])
async def test_search_tool_is_read_only(tmp_path: Path, mode: str) -> None:
    server = _server(tmp_path, [mode])
    async with Client(server) as client:
        search = next(t for t in await client.list_tools() if t.name == "search_tools")
        assert search.annotations.read_only_hint is True
        assert search.annotations.destructive_hint is False
        assert search.annotations.open_world_hint is False
        resolved = await server.get_tool("search_tools")
        assert resolved.annotations == search.annotations


async def test_a_query_whose_words_are_all_in_a_name_finds_it(tmp_path: Path) -> None:
    async with Client(_server(tmp_path, ["search"])) as client:
        # "program device" -> connection_program_device, even though "program device download"
        # already works through BM25.
        assert "connection_program_device" in await _found(client, "program device")
        assert "project_list_devices" in await _found(client, "list devices")


@pytest.mark.parametrize("mode", ["search", "catalog"])
async def test_keyword_search_ranks_names_before_bm25_and_caps_results(
    mode: str,
) -> None:
    server = FastMCP()
    server.add_transform(ToolSearch(lambda: mode))

    def sample() -> None:
        pass

    for name in (
        "project_read_device_details",
        "read_device",
        "connection_read_device",
    ):
        server.add_tool(
            Tool.from_function(sample, name=name, description="Inspect data.")
        )
    for index in range(SEARCH_RESULTS):
        server.add_tool(
            Tool.from_function(
                sample, name=f"other_{index}", description="Read device information."
            )
        )
    async with Client(server) as client:
        found = await _found(client, "device read")
        assert len(found) == len(set(found)) == SEARCH_RESULTS
        assert found[:3] == [
            "read_device",
            "connection_read_device",
            "project_read_device_details",
        ]
        assert all(name.startswith("other_") for name in found[3:])


def _inventory_names(text: str) -> set[str]:
    return set(re.findall(r"^(\w+) \[[RWD](?:,O)?\] - ", text, re.MULTILINE))


async def test_catalog_lists_every_real_tool(tmp_path: Path) -> None:
    async with Client(build_server(_build_ctx(tmp_path))) as listing:
        all_tools = {tool.name: tool for tool in await listing.list_tools()}
    assert len(all_tools) > 100
    async with Client(_server(tmp_path, ["catalog"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert sorted(tools) == sorted(
            [*PINNED_TOOLS, "tools_catalog", "search_tools", "call_tool"]
        )
        catalog = tools["tools_catalog"]
        assert catalog.annotations.read_only_hint is True
        assert catalog.annotations.destructive_hint is False
        assert catalog.annotations.open_world_hint is False
        description = catalog.description
        output = (await client.call_tool("tools_catalog")).data
        assert isinstance(output, str)
        assert description == output
        assert _inventory_names(description) == set(all_tools)
        assert "connection_program_device [D,O] - " in output
        assert "project_get_device [R] - " in output
        assert "project_set_parameter [W] - " in output
        assert (
            "connection_master_reset_device [D,O] - "
            "reset the device over the bus with a Master Reset."
        ) in output
        assert "connection_dali_program_groups [D,O] - program the group" in output
        assert "CHANGES THE DALI BUS:" not in output
        assert "DESTRUCTIVE, not undoable:" not in output
        assert "\nproject:\n" in output and "\nconnection:\n" in output

        result = await client.call_tool("search_tools", {"query": "project_get_device"})
        definitions = result.structured_content["result"]
        assert definitions[0]["name"] == "project_get_device"
        assert (
            definitions[0]["description"] == all_tools["project_get_device"].description
        )
        assert (
            definitions[0]["inputSchema"]
            == all_tools["project_get_device"].input_schema
        )
        through = await client.call_tool(
            "call_tool", {"name": "project_list_devices", "arguments": {}}
        )
        direct = await client.call_tool("project_list_devices")
        assert not through.is_error and not direct.is_error
        assert through.content == direct.content


@pytest.mark.parametrize("domain", ["project", "catalog_", "unknown_domain"])
async def test_catalog_filters_by_prefix(tmp_path: Path, domain: str) -> None:
    async with Client(_server(tmp_path, ["catalog"])) as client:
        all_names = _inventory_names((await client.call_tool("tools_catalog")).data)
        filtered = (await client.call_tool("tools_catalog", {"domain": domain})).data
        assert _inventory_names(filtered) == {
            name for name in all_names if name.startswith(domain)
        }


async def test_catalog_reflects_tool_changes() -> None:
    server = FastMCP()
    server.add_transform(ToolSearch(lambda: "catalog"))
    async with Client(server) as client:
        await client.list_tools()

        @server.tool(annotations=tool_annotations("write", open_world=True))
        def sample_added() -> None:
            """Register a ``sample`` tool; keep its full purpose
            across multiple lines. More details are omitted."""

        description = next(
            tool.description
            for tool in await client.list_tools()
            if tool.name == "tools_catalog"
        )
        output = (await client.call_tool("tools_catalog")).data
        assert description == output
        assert _inventory_names(output) == {"sample_added"}
        assert "sample_added [W,O] - Register a sample tool" in output
        purpose = output.split(" - ", 1)[1]
        assert (
            purpose
            == "Register a sample tool; keep its full purpose across multiple lines."
        )
        assert "More details" not in output

        server.local_provider.remove_tool("sample_added")
        output = (await client.call_tool("tools_catalog")).data
        assert not _inventory_names(output)
        assert "sample_added" not in next(
            tool.description
            for tool in await client.list_tools()
            if tool.name == "tools_catalog"
        )


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "Read a value: preserve its units; return the result. Details follow.",
            "Read a value: preserve its units; return the result.",
        ),
        (
            "**WARNING:** Read the **device** and `group_address`.",
            "Read the device and group_address.",
        ),
        ("__Read the device.__ More details follow.", "Read the device."),
        ("Read a value! More details follow.", "Read a value!"),
        (None, ""),
    ],
)
def test_catalog_purpose_keeps_clean_first_sentence(
    description: str | None, expected: str
) -> None:
    tool = Tool(name="sample", description=description, parameters={})
    assert _tool_inventory([tool]).split(" - ", 1)[1] == expected


def test_catalog_shortens_only_long_first_sentences() -> None:
    tool = Tool(
        name="sample",
        description="Read "
        + "the device configuration " * 8
        + ". More details follow.",
        parameters={},
    )
    purpose = _tool_inventory([tool]).split(" - ", 1)[1]
    assert len(purpose) <= 120
    assert purpose.startswith("Read the device configuration")
    assert purpose.endswith("...")
    assert "More details" not in purpose

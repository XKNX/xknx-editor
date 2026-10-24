"""Every MCP tool tells the client what it does (MCP tool annotations): it only reads, writes
what is easily set back, or is destructive; and whether it reaches the bus or the network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from editor_gui.plugins.mcp.context import make_tool, tool_annotations

from .test_server import _build, _build_ctx

# A few tools of each kind, as the client sees them: (read only, destructive, open world).
_EXPECTED = {
    "status": (True, False, False),
    "project_list_parameters": (True, False, False),
    "connection_evaluate_device": (True, False, True),
    "monitor_read_value": (True, False, True),
    "catalog_online_items": (True, False, True),
    "project_set_parameter": (False, False, False),
    "project_link_com_object": (False, False, False),
    "catalog_import_knxprod": (False, False, False),
    "connection_connect": (False, False, True),
    "project_remove_device": (False, True, False),
    "project_unlink_com_object": (False, True, False),
    "project_copy_device_configuration": (False, True, False),
    "project_export_knxproj": (False, True, False),
    "project_batch": (False, True, False),
    "connection_program_device": (False, True, True),
    "monitor_send_write": (False, True, True),
}


async def _tools(tmp_path: Path) -> dict[str, Any]:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        return {t.name: t for t in await client.list_tools()}


async def test_every_tool_states_what_it_does(tmp_path: Path) -> None:
    tools = await _tools(tmp_path)
    assert len(tools) > 100
    for name, tool in tools.items():
        hints = tool.annotations
        assert hints is not None, name
        # All three hints are given; the protocol's defaults (destructive, open world) never
        # apply by omission.
        assert isinstance(hints.read_only_hint, bool), name
        assert isinstance(hints.destructive_hint, bool), name
        assert isinstance(hints.open_world_hint, bool), name
        assert not (hints.read_only_hint and hints.destructive_hint), name


def _hints(annotations: Any) -> tuple[bool, bool, bool]:
    return (
        annotations.read_only_hint,
        annotations.destructive_hint,
        annotations.open_world_hint,
    )


async def test_tools_are_classified_by_what_they_do(tmp_path: Path) -> None:
    tools = await _tools(tmp_path)
    for name, expected in _EXPECTED.items():
        assert _hints(tools[name].annotations) == expected, name


def test_the_annotations_of_each_kind() -> None:
    assert _hints(tool_annotations("read")) == (True, False, False)
    assert _hints(tool_annotations("write", open_world=True)) == (False, False, True)
    assert _hints(tool_annotations("destructive")) == (False, True, False)


def test_a_tool_without_a_kind_is_refused(tmp_path: Path) -> None:
    tool = make_tool(FastMCP("test"), _build_ctx(tmp_path))

    def unclassified() -> None:
        """A tool that forgot its kind."""

    with pytest.raises(TypeError, match="tool kind"):
        tool(unclassified)  # type: ignore[arg-type]

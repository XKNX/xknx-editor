"""Embedded MCP server plugin: exposes the editor's live services as MCP tools over HTTP.

The context and the server import FastMCP, so they load on first use only (see ``plugin``)."""

from typing import TYPE_CHECKING, Any

from editor_gui.plugins.mcp.plugin import McpServerPlugin

if TYPE_CHECKING:
    from editor_gui.plugins.mcp.context import McpContext
    from editor_gui.plugins.mcp.server import build_server

__all__ = ["McpContext", "McpServerPlugin", "build_server"]


def __getattr__(name: str) -> Any:
    if name == "McpContext":
        from editor_gui.plugins.mcp.context import McpContext

        return McpContext
    if name == "build_server":
        from editor_gui.plugins.mcp.server import build_server

        return build_server
    raise AttributeError(name)

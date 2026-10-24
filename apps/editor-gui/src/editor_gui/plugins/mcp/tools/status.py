# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""A single aggregate status/capability tool — a reliable first call for an LLM.

Combines project/connection/keyring/capture state and the enums (download scopes, com-object flags,
group-address style) an agent needs to form valid calls, instead of scattering them across many
tools and runtime validation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from editor_gui.plugins.mcp.context import McpContext, make_tool
from editor_gui.plugins.mcp.tools.network import capture_state

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    @tool("read")
    def status() -> dict[str, Any]:
        """One-call overview: project, connection, keyring, capture state, plus valid enums.

        Call this first. ``capabilities`` lists the download scopes, com-object flags, and (when a
        project is open) the group-address style that other tools accept."""
        from xknxeditor.download.scope import DownloadScope

        def _project() -> dict[str, Any]:
            info = ctx.api.project.get_project_metadata()
            return {
                "open": ctx.api.project.is_open,
                "path": str(ctx.api.project.path) if ctx.api.project.path else None,
                "name": info.name if info else None,
                "group_address_style": info.group_address_style if info else None,
                "device_count": len(ctx.api.project.devices),
            }

        project = ctx.run_locked(_project)
        return {
            "project": project,
            "connection": {
                "state": ctx.connection_plugin.state.value,
                "connected": ctx.connection_plugin.connected,
                "controller_ip": ctx.connection_plugin.controller_ip,
                "routing": ctx.connection_plugin.is_routing,
            },
            "keyring": {"loaded": ctx.keyring.keyring is not None},
            "capture": capture_state(ctx.monitor),
            "online_catalog_language": ctx.api.catalog.online_language,
            "capabilities": {
                "download_scopes": [s.name for s in DownloadScope],
                "com_object_flags": list(
                    (
                        "communication",
                        "read",
                        "write",
                        "transmit",
                        "update",
                        "read_on_init",
                    )
                ),
                "group_address_style": project["group_address_style"],
            },
        }

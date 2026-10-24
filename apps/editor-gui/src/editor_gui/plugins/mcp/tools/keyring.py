# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Keyring tools: load a password-protected KNX .knxkeys keyring, clear it, report status."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    keyring = ctx.keyring

    @tool
    def keyring_load(path: str, password: str) -> dict[str, Any]:
        """Decrypt a .knxkeys file at path on the editor host and replace the keyring held in memory.
        Raises on a wrong password or invalid file. Returns {loaded: true, path}. This is runtime
        state, outside project_undo history; it is not stored in the project or written to the bus."""
        keyring.load(Path(path), password)
        return {"loaded": True, "path": str(keyring.path) if keyring.path else None}

    @tool(destructive=True)
    def keyring_export(path: str, password: str) -> dict[str, Any]:
        """Export the loaded keyring to path on the editor host, re-encrypted and signed under
        password with the same key material. Requires a loaded keyring. An existing destination
        file is overwritten; this cannot be undone with project_undo. Returns {exported: true, path}.
        This writes a file without changing keys on bus devices."""
        keyring.export(Path(path), password)
        return {"exported": True, "path": path}

    @tool(destructive=True)
    def keyring_clear() -> dict[str, str]:
        """Discard the keyring held in memory. Returns {status: "cleared"}. The source file remains
        available for keyring_load; project_undo cannot restore runtime keyring state."""
        keyring.clear()
        return {"status": "cleared"}

    @tool(read_only=True)
    def keyring_status() -> dict[str, Any]:
        """Return {loaded, path, project} for the in-memory keyring. path is its editor-host source
        path or null; project is the project name stored in the keyring, or null."""
        return {
            "loaded": keyring.keyring is not None,
            "path": str(keyring.path) if keyring.path else None,
            "project": keyring.project_name or None,
        }

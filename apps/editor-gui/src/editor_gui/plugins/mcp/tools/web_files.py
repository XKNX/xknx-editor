# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""The browser UI's file folder: the files its file dialogs list (uploads, saved projects,
exports), listed and deleted as the dialogs' delete buttons do. Only in the browser UI
(``--web``): the desktop app has no such folder."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.web.server import WebHub


def _hub() -> WebHub:
    from editor_gui import web

    if web.active_hub is None:
        raise ToolError("the file folder exists only in the browser UI (--web)")
    return web.active_hub


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    @tool("read")
    def web_files_list() -> dict[str, Any]:
        """The files of the browser UI's file folder on the server, newest first, as its file
        dialogs list them: uploads, saved projects and exports.

        Returns ``{"folder": path, "files": [{name, size, modified, in_use}]}``: ``size`` in
        bytes, ``modified`` as ISO 8601 (UTC), ``in_use`` true for a file of the open project
        (it cannot be deleted). Only in the browser UI (``--web``)."""
        hub = _hub()
        in_use = hub.file_in_use
        files = [
            {
                "name": entry.name,
                "size": entry.size,
                "modified": datetime.fromtimestamp(entry.modified, UTC).isoformat(),
                "in_use": bool(in_use and in_use(hub.files.path(entry.name))),
            }
            for entry in hub.files.files()
        ]
        return {"folder": str(hub.files.root), "files": files}

    @tool("destructive")
    def web_file_delete(name: str) -> dict[str, Any]:
        """Delete a file of the browser UI's file folder, as the delete button of a file dialog
        does; it cannot be undone.

        ``name`` is the file's name as ``web_files_list`` gives it (a plain name, no folder).
        Refused for a file not in the folder and for a file of the open project. Returns
        ``{"deleted": name}``. Only in the browser UI (``--web``)."""
        from editor_gui.web.files import FileInUseError

        hub = _hub()
        try:
            hub.delete_file(name)
        except FileInUseError:
            raise ToolError(
                f"{name!r} belongs to the open project and is not deleted"
            ) from None
        except FileNotFoundError:
            raise ToolError(
                f"no file {name!r} in the file folder; see web_files_list"
            ) from None
        except OSError as exc:
            raise ToolError(f"{name!r} could not be deleted: {exc}") from None
        return {"deleted": name}

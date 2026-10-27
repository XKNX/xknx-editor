# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Backup tools: the full backups of the open project (File > Versions and Backups) and their
settings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.backups.settings import BackupSettings, default_folder
from editor_gui.plugins.backups.store import Backup, BackupError, list_backups
from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.backups.service import BackupService

# Restoring replaces and reopens the project, which takes a while on a large one.
_SLOW = 600.0


def _backup(backup: Backup) -> dict[str, Any]:
    info = backup.info
    return {
        "path": str(backup.path),
        "created": info.created.isoformat(),
        "kind": info.kind,
        "comment": info.comment,
        "user": info.user,
        "machine": info.machine,
        "source": info.source,
        "size": backup.size,
        "pinned": backup.pinned,
    }


def _settings(settings: BackupSettings) -> dict[str, Any]:
    return {
        **settings.to_dict(),
        "effective_folder": str(settings.root),
        "default_folder": str(default_folder()),
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    def service() -> BackupService:
        if ctx.backups is None:
            raise ToolError("backups are not available")
        return ctx.backups

    def find(path: str) -> Backup:
        require_project(ctx)
        try:
            return ctx.run_locked(lambda: service().find(path))
        except BackupError as e:
            raise ToolError(str(e)) from e

    @tool("read")
    def backup_settings() -> dict[str, Any]:
        """The backup settings (File > Versions and Backups > Settings…), for every project:
        {folder ("" = the default folder), effective_folder, default_folder, on_close (a backup
        when a project is closed), interval and interval_hours (a backup every that many hours
        while a project is open and changed), expire, expire_days and keep_newest (automatic
        backups older than expire_days days are deleted, all but the newest keep_newest)}.
        Backups made by hand and pinned ones are never deleted automatically."""
        return _settings(service().settings)

    @tool("write")
    def backup_set_settings(
        folder: str | None = None,
        on_close: bool | None = None,
        interval: bool | None = None,
        interval_hours: int | None = None,
        expire: bool | None = None,
        expire_days: int | None = None,
        keep_newest: int | None = None,
    ) -> dict[str, Any]:
        """Change the backup settings (see backup_settings); a parameter left out keeps its
        value, ``folder`` "" means the default folder. Numbers are kept within their bounds
        (interval_hours 1-8760, expire_days 1-36500, keep_newest 0-1000). Not part of the
        project's undo history. Returns the settings as kept."""
        changes = {
            name: value
            for name, value in {
                "folder": folder,
                "on_close": on_close,
                "interval": interval,
                "interval_hours": interval_hours,
                "expire": expire,
                "expire_days": expire_days,
                "keep_newest": keep_newest,
            }.items()
            if value is not None
        }
        current = service().settings
        return _settings(service().set_settings(replace(current, **changes)))

    @tool("read")
    def backup_list() -> dict[str, Any]:
        """The full backups of the open project, the newest first: {folder, backups: [{path,
        created (ISO time, UTC), kind (manual, close, interval, or restore: the project as it
        was before a restore), comment, user, machine, source (the project file it was made
        from), size (bytes), pinned}]}. A backup's path also works as the other project of
        project_compare_projects."""
        require_project(ctx)
        meta = ctx.run_locked(lambda: service().meta())
        if meta is None:
            raise ToolError("the project is busy; retry shortly")
        found = list_backups(service().root, meta.guid)
        folder = found[0].path.parent if found else service().root
        return {"folder": str(folder), "backups": [_backup(b) for b in found]}

    @tool("write")
    def backup_create(comment: str = "", timeout: float = 600.0) -> dict[str, Any]:
        """Make a full backup of the open project as it is now, with an optional ``comment``,
        like File > Versions and Backups > Create Backup…. The project is checked first; a
        damaged one gets no backup (an error). Waits until the backup is written (``timeout``
        seconds). Returns the backup (see backup_list)."""
        require_project(ctx)
        try:
            job = ctx.run_locked(lambda: service().create(comment.strip()))
            backup = job.future.result(timeout=timeout)
        except BackupError as e:
            raise ToolError(str(e)) from e
        if backup is None:
            raise ToolError("no backup was made")
        return _backup(backup)

    @tool("destructive")
    def backup_restore(path: str, password: str | None = None) -> dict[str, Any]:
        """Restore the open project to the state of its backup at ``path`` (see backup_list):
        the project file is replaced and opened again. Its state before is saved as a backup
        first (kind "restore"), so a restore can be taken back by restoring that one; the undo
        history is the backup's. ``password``: the project's password when the backup was made,
        when it differs from the current one. Returns {restored: true, path, created}."""
        from xknxeditor.proj import InvalidProjectPassword, ProjectPasswordRequired

        backup = find(path)
        try:
            ctx.run_locked(lambda: service().restore(backup, password), timeout=_SLOW)
        except (ProjectPasswordRequired, InvalidProjectPassword) as e:
            if password is None:
                raise ToolError(
                    "the backup has another password than the project; pass password"
                ) from e
            raise ToolError("wrong password for the backup") from e
        except BackupError as e:
            raise ToolError(str(e)) from e
        return {
            "restored": True,
            "path": str(backup.path),
            "created": backup.info.created.isoformat(),
        }

    @tool("write")
    def backup_pin(path: str, pinned: bool = True) -> dict[str, Any]:
        """Pin (or with ``pinned`` false unpin) the backup at ``path``: a pinned backup is never
        deleted automatically. Returns the backup."""
        backup = find(path)
        try:
            return _backup(service().set_pinned(backup, pinned))
        except BackupError as e:
            raise ToolError(str(e)) from e

    @tool("destructive")
    def backup_delete(path: str) -> dict[str, Any]:
        """Delete the backup at ``path``; this cannot be undone. Returns {deleted: path}."""
        backup = find(path)
        try:
            service().delete(backup)
        except BackupError as e:
            raise ToolError(str(e)) from e
        return {"deleted": str(backup.path)}

    @tool("write")
    def backup_export(path: str, dest: str) -> dict[str, Any]:
        """Write the project of the backup at ``path`` as a project file ``dest`` (.xknx) on the
        editor host, e.g. to open it beside the project; an existing file there is replaced (not
        the open project's file). Returns {exported: dest}."""
        backup = find(path)
        try:
            service().export(backup, Path(dest))
        except BackupError as e:
            raise ToolError(str(e)) from e
        return {"exported": dest}

    @tool("read")
    def backup_check(path: str) -> dict[str, Any]:
        """Check that the backup at ``path`` can be restored: its file is complete and the project
        in it intact. Returns {ok, problem} (problem: why not, else "")."""
        backup = find(path)
        problem = service().check(backup)
        return {"ok": problem is None, "problem": problem or ""}

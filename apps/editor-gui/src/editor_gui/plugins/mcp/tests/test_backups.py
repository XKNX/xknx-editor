"""MCP tools for the backups of the open project: settings, create, list, pin, check, export,
compare, restore and delete, as in File > Backups."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui import settings as app_settings
from editor_gui.plugins.backups import settings as backup_settings
from editor_gui.plugins.backups.service import BackupService
from editor_gui.plugins.base import Logger
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin

from .test_server import _build_ctx, _data


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "config"
    folder.mkdir()
    monkeypatch.setattr(app_settings, "config_dir", lambda: folder)
    monkeypatch.setattr(backup_settings, "config_dir", lambda: folder)


def _server(tmp_path: Path, *, with_backups: bool = True) -> tuple[Any, Any]:
    ctx = _build_ctx(tmp_path)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    if with_backups:
        ctx.backups = BackupService(
            ctx.api.project, ctx.api.catalog.io_lock, Logger(ctx.api.log, "backups")
        )
    return build_server(ctx), ctx


async def _error(client: Client, tool: str, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool(tool, args)


async def test_settings_are_read_and_changed(tmp_path: Path) -> None:
    mcp, _ctx = _server(tmp_path)
    async with Client(mcp) as client:
        settings = await _data(client, "backup_settings")
        assert settings["folder"] == ""
        assert settings["on_close"] is False
        assert settings["interval"] is False
        assert settings["expire"] is True
        assert settings["effective_folder"] == settings["default_folder"]

        folder = str(tmp_path / "backups")
        changed = await _data(
            client,
            "backup_set_settings",
            folder=folder,
            on_close=True,
            interval_hours=0,
            keep_newest=5,
        )
        assert changed["folder"] == folder
        assert changed["effective_folder"] == folder
        assert changed["on_close"] is True
        assert changed["interval_hours"] == 1
        assert changed["keep_newest"] == 5
        assert changed["expire_days"] == 90
        assert (await _data(client, "backup_settings"))["on_close"] is True


async def test_backups_are_made_used_and_deleted(tmp_path: Path) -> None:
    mcp, _ctx = _server(tmp_path)
    async with Client(mcp) as client:
        await _error(client, "backup_list", "no project is open")
        await _data(client, "backup_set_settings", folder=str(tmp_path / "backups"))
        await _data(client, "project_new", path=str(tmp_path / "home.xknx"))
        assert (await _data(client, "backup_list"))["backups"] == []

        made = await _data(client, "backup_create", comment="  First  ")
        assert made["kind"] == "manual"
        assert made["comment"] == "First"
        assert made["source"] == str(tmp_path / "home.xknx")
        listed = await _data(client, "backup_list")
        assert [b["path"] for b in listed["backups"]] == [made["path"]]
        assert listed["folder"] == str(Path(made["path"]).parent)

        pinned = await _data(client, "backup_pin", path=made["path"])
        assert pinned["pinned"] is True
        assert (await _data(client, "backup_pin", path=made["path"], pinned=False))[
            "pinned"
        ] is False
        assert await _data(client, "backup_check", path=made["path"]) == {
            "ok": True,
            "problem": "",
        }

        dest = tmp_path / "exported.xknx"
        assert (
            await _data(client, "backup_export", path=made["path"], dest=str(dest))
        )["exported"] == str(dest)
        assert dest.exists()
        await _error(
            client,
            "backup_export",
            "open project",
            path=made["path"],
            dest=str(tmp_path / "home.xknx"),
        )

        # A backup is another project for Project Diff.
        diff = await _data(client, "project_compare_projects", path=made["path"])
        assert diff["devices"] == []

        await _data(client, "backup_delete", path=made["path"])
        assert (await _data(client, "backup_list"))["backups"] == []
        await _error(client, "backup_pin", "no backup", path=made["path"])


async def test_restore_brings_a_state_back(tmp_path: Path) -> None:
    mcp, ctx = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "backup_set_settings", folder=str(tmp_path / "backups"))
        await _data(client, "project_new", path=str(tmp_path / "home.xknx"))
        ctx.api.project.set_project_password("old")
        made = await _data(client, "backup_create")
        ctx.api.project.set_project_password("new")
        await _data(client, "project_set_name", name="Changed")

        await _error(client, "backup_restore", "another password", path=made["path"])
        await _error(
            client, "backup_restore", "wrong password", path=made["path"], password="x"
        )
        restored = await _data(
            client, "backup_restore", path=made["path"], password="old"
        )
        assert restored["restored"] is True
        info = ctx.api.project.get_project_metadata()
        assert info is not None
        assert info.name != "Changed"
        kinds = [b["kind"] for b in (await _data(client, "backup_list"))["backups"]]
        assert sorted(kinds) == ["manual", "restore"]


async def test_without_the_backups_service(tmp_path: Path) -> None:
    mcp, _ctx = _server(tmp_path, with_backups=False)
    async with Client(mcp) as client:
        await _error(client, "backup_settings", "not available")

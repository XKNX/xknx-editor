"""MCP tools for the program's settings: read and changed with the functions the Settings window
uses, found by the window's search, secrets never told, and the cache cleared."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui import cache
from editor_gui.plugins.mcp import context as mcp_context
from editor_gui.plugins.mcp.server import build_server
from editor_gui.settings_window import Setting, SettingsPage, SettingsWindow

from .test_server import _build_ctx, _data


class _Values:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {
            "flag": False,
            "choice": "a",
            "number": 5,
            "text": "x",
            "token": "",
            "locked": False,
            "hidden": False,
        }

    def setting(self, key: str, kind: Any, **extra: Any) -> Setting:
        def change(value: Any) -> None:
            self.values[key] = value

        return Setting(
            key,
            lambda: key.title(),
            kind,
            get=lambda: self.values[key],
            set=change,
            **extra,
        )


def _server(tmp_path: Path) -> tuple[Any, _Values]:
    values = _Values()
    pages = [
        SettingsPage(
            "general",
            lambda: "General",
            [
                values.setting("flag", "bool"),
                values.setting(
                    "choice",
                    "choice",
                    choices=lambda: [("a", "Alpha"), ("b", "Beta")],
                    hint=lambda: "Pick a letter",
                ),
                values.setting("number", "int", bounds=(1, 10)),
            ],
        ),
        SettingsPage(
            "other",
            lambda: "Other",
            [
                values.setting("text", "text"),
                values.setting("token", "password", secret=True),
                Setting("fixed", lambda: "Fixed", "info", get=lambda: "/some/folder"),
                values.setting("locked", "bool", read_only=lambda: True),
                values.setting("hidden", "bool", shown=lambda: False),
            ],
        ),
    ]
    ctx = _build_ctx(tmp_path)
    ctx.settings = SettingsWindow(lambda: pages)
    return build_server(ctx), values


async def _error(client: Client, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool("settings_set", args)


async def test_the_settings_are_read_by_page(tmp_path: Path) -> None:
    mcp, _values = _server(tmp_path)
    async with Client(mcp) as client:
        pages = (await _data(client, "settings_get"))["pages"]
    assert [p["key"] for p in pages] == ["general", "other"]
    general = {s["key"]: s for s in pages[0]["settings"]}
    assert general["flag"] == {
        "key": "flag",
        "page": "general",
        "label": "Flag",
        "kind": "bool",
        "writable": True,
        "value": False,
    }
    assert general["choice"]["choices"] == [
        {"value": "a", "label": "Alpha"},
        {"value": "b", "label": "Beta"},
    ]
    assert general["choice"]["hint"] == "Pick a letter"
    assert (general["number"]["min"], general["number"]["max"]) == (1, 10)
    other = {s["key"]: s for s in pages[1]["settings"]}
    # A secret tells only whether it is set; a setting not shown here is left out.
    assert "value" not in other["token"] and other["token"]["is_set"] is False
    assert other["fixed"]["writable"] is False
    assert other["locked"]["writable"] is False
    assert "hidden" not in other


async def test_a_query_finds_settings_like_the_window_search(tmp_path: Path) -> None:
    mcp, _values = _server(tmp_path)
    async with Client(mcp) as client:
        found = (await _data(client, "settings_get", query="letter"))["pages"]
        by_page = (await _data(client, "settings_get", query="other fix"))["pages"]
    assert [[s["key"] for s in p["settings"]] for p in found] == [["choice"]]
    assert [[s["key"] for s in p["settings"]] for p in by_page] == [["fixed"]]


async def test_a_setting_is_changed_with_its_own_function(tmp_path: Path) -> None:
    mcp, values = _server(tmp_path)
    async with Client(mcp) as client:
        changed = await _data(client, "settings_set", key="flag", value=True)
        await _data(client, "settings_set", key="choice", value="b")
        # A number is kept within its bounds.
        number = await _data(client, "settings_set", key="number", value=99)
        await _data(client, "settings_set", key="text", value="y")
        token = await _data(client, "settings_set", key="token", value="s3cret")
    assert changed["value"] is True
    assert number["value"] == 10
    assert token == {**token, "is_set": True} and "value" not in token
    assert values.values == {
        "flag": True,
        "choice": "b",
        "number": 10,
        "text": "y",
        "token": "s3cret",
        "locked": False,
        "hidden": False,
    }


async def test_a_wrong_value_or_setting_is_refused(tmp_path: Path) -> None:
    mcp, values = _server(tmp_path)
    async with Client(mcp) as client:
        await _error(client, "unknown setting", key="nope", value=1)
        await _error(client, "cannot be changed", key="fixed", value="x")
        await _error(client, "cannot be changed", key="locked", value=True)
        await _error(client, "unknown setting", key="hidden", value=True)
        await _error(client, "true or false", key="flag", value="yes")
        await _error(client, "one of: a, b", key="choice", value="c")
        await _error(client, "a number", key="number", value="5")
        await _error(client, "a number", key="number", value=True)
        await _error(client, "a text", key="text", value=5)
    assert values.values["flag"] is False


def test_a_secret_setting_is_not_logged() -> None:
    redact = mcp_context._redact_args  # pyright: ignore[reportPrivateUsage]
    assert redact("settings_set", {"key": "mcp_token", "value": "s3cret"}) == {
        "key": "mcp_token",
        "value": "***",
    }
    assert (
        redact("settings_set", {"key": "mcp_port", "value": "8765"})["value"] == "8765"
    )


async def test_without_settings_the_tools_say_so(tmp_path: Path) -> None:
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="not available"):
            await client.call_tool("settings_get", {})


async def test_the_cache_is_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "cache"
    (folder / "catalog").mkdir(parents=True)
    (folder / "catalog" / "index.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(cache, "cache_dir", lambda: folder)
    mcp, _values = _server(tmp_path)
    async with Client(mcp) as client:
        cleared = await _data(client, "cache_clear")
    assert cleared["freed"] == 100
    assert cache.cache_size() == 0


async def test_a_setting_mcp_may_only_tighten_is_refused_the_other_way(
    tmp_path: Path,
) -> None:
    values = _Values()
    pages = [
        SettingsPage(
            "general",
            lambda: "General",
            [values.setting("flag", "bool", mcp_may_set=bool)],
        )
    ]
    ctx = _build_ctx(tmp_path)
    ctx.settings = SettingsWindow(lambda: pages)
    async with Client(build_server(ctx)) as client:
        await client.call_tool("settings_set", {"key": "flag", "value": True})
        assert values.values["flag"] is True
        await _error(client, "only the user can set 'flag'", key="flag", value=False)
    assert values.values["flag"] is True

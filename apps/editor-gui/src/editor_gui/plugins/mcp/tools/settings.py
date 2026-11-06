# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Settings tools: the program's settings, as the Settings window (File > Settings…) shows and
changes them, and clearing the cache (Settings > Folders)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.cache import cache_size, clear_cache
from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.settings_window import Setting, SettingsPage, SettingsWindow


def _entry(page: SettingsPage, setting: Setting) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "key": setting.key,
        "page": page.key,
        "label": setting.label(),
        "kind": setting.kind,
        "writable": setting.set is not None and not setting.read_only(),
    }
    if setting.get is not None:
        value = setting.get()
        if setting.secret:
            entry["is_set"] = bool(value)
        else:
            entry["value"] = value
    if setting.choices is not None:
        entry["choices"] = [
            {"value": key, "label": label} for key, label in setting.choices()
        ]
    if setting.bounds is not None:
        entry["min"], entry["max"] = setting.bounds
    if setting.hint is not None and setting.hint():
        entry["hint"] = setting.hint()
    return entry


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    def window() -> SettingsWindow:
        if ctx.settings is None:
            raise ToolError("the settings are not available")
        return ctx.settings

    @tool("read")
    def settings_get(query: str = "") -> dict[str, Any]:
        """The program's settings, as the Settings window (File > Settings…) shows them, by
        page: {pages: [{key, title, settings: [{key, page, label, kind (bool, choice, text,
        password, int, info, custom), writable, value, choices: [{value, label}], min, max,
        hint}]}]}. A secret (the MCP bearer token) gives ``is_set`` instead of its value.
        ``query`` finds settings by their names, hints and page titles, in the interface
        language or in English, like the window's search. They apply to the program, not to a
        project, and are not part of the undo history. Change one with settings_set."""

        def read() -> dict[str, Any]:
            return {
                "pages": [
                    {
                        "key": page.key,
                        "title": page.title(),
                        "settings": [_entry(page, s) for s in settings],
                    }
                    for page, settings in window().search(query)
                ]
            }

        return ctx.run_on_ui(read)

    @tool("write")
    def settings_set(key: str, value: Any) -> dict[str, Any]:
        """Change the setting ``key`` (see settings_get) to ``value``: true/false for a bool,
        one of the choices' values for a choice, a number for an int (kept within min and max),
        a text for text and password. It takes effect as when changed in the Settings window:
        a new interface language rebuilds the device views, a new MCP host, port or bearer
        token applies when the server is started next. Returns the setting as it is now."""

        def change() -> dict[str, Any]:
            found = window().setting(key)
            if found is None:
                raise ToolError(f"unknown setting {key!r}; see settings_get")
            page, setting = found
            if setting.set is None or setting.read_only():
                raise ToolError(f"the setting {key!r} cannot be changed now")
            new = _coerce(setting, value)
            setting.set(new)
            return _entry(page, setting)

        return ctx.run_on_ui(change)

    @tool("destructive")
    def cache_clear() -> dict[str, Any]:
        """Delete the cache (downloaded catalog data and indexes, Settings > Folders > Clear
        Cache…): it is downloaded or built again when needed. Projects, settings and the local
        catalog stay. Returns {freed} (bytes)."""
        before = cache_size()
        freed = clear_cache()
        ctx.log.info("cache cleared", freed=freed, before=before)
        return {"freed": freed}


def _coerce(setting: Setting, value: Any) -> Any:
    """``value`` as ``setting`` takes it, or a ToolError saying what it takes."""
    if setting.kind == "bool":
        if not isinstance(value, bool):
            raise ToolError(f"{setting.key} takes true or false")
        return value
    if setting.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ToolError(f"{setting.key} takes a number")
        number = int(value)
        if setting.bounds is not None:
            number = min(max(number, setting.bounds[0]), setting.bounds[1])
        return number
    if setting.choices is not None:
        allowed = [key for key, _ in setting.choices()]
        if str(value) not in allowed:
            raise ToolError(f"{setting.key} takes one of: {', '.join(allowed)}")
        return str(value)
    if not isinstance(value, str):
        raise ToolError(f"{setting.key} takes a text")
    return value

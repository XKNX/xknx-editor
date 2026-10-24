"""Shared context and helpers for the embedded MCP server's tool modules.

The MCP server runs on its own thread (uvicorn), but the GUI's ``project``/``catalog`` services wrap
non-thread-safe SQLAlchemy sessions and per-frame caches. Every tool that touches them therefore
runs its body on the imgui main thread via :attr:`McpContext.run_on_ui` (a
:class:`~editor_gui.concurrency.MainThreadExecutor`), which serialises MCP access with the GUI's own
per-frame reads and writes. Bus operations instead go through the connection service, which already
schedules onto the KNX event loop thread-safely and returns a ``concurrent.futures.Future``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from fastmcp.exceptions import ToolError

from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError
from xknxeditor.prod.errors import ArchiveError

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.base import Logger, PluginAPI
    from editor_gui.plugins.connection.plugin import ConnectionPlugin
    from editor_gui.plugins.keyring.service import KeyringService
    from editor_gui.plugins.monitor.service import MonitorService

T = TypeVar("T")

# Substrings that mark an argument as secret; its value is never logged. A substring denylist (not an
# exact name) so future args like ``auth_token`` or ``api_key`` are redacted without revisiting this.
_SECRET_ARG_MARKERS = (
    "password",
    "token",
    "secret",
    "credential",
    "passphrase",
    "authentication",
)

# Arguments that are secret only in a specific tool: the same name is harmless elsewhere, so a
# substring marker would over-redact (e.g. ``code`` is a device FDSK here but a language code in
# catalog_set_online_language). Keyed by tool name -> set of secret argument names.
_SECRET_ARGS_BY_TOOL: dict[str, frozenset[str]] = {
    "project_add_device_certificate": frozenset({"code"}),
}

# Large upload arguments: logged as a length summary, never their full content.
_BULK_ARGS = frozenset({"data_base64"})


def _is_secret_arg(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _SECRET_ARG_MARKERS)


def _redact_args(tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Tool-call arguments with secrets masked and bulk uploads summarized by length."""
    secret_here = _SECRET_ARGS_BY_TOOL.get(tool, frozenset())
    redacted: dict[str, Any] = {}
    for name, value in kwargs.items():
        if _is_secret_arg(name) or name in secret_here:
            redacted[name] = "***"
        elif name in _BULK_ARGS and isinstance(value, str):
            redacted[name] = f"<{len(value)} chars>"
        else:
            redacted[name] = value
    return redacted


# Exceptions the services raise for ordinary bad input / expected failures: surfaced to the MCP
# client as a clean ToolError rather than an opaque internal error. Deliberately does NOT include
# broad types like RuntimeError, so genuine defects surface as internal errors instead of being
# reported to the client as ordinary bad input.
_EXPECTED_ERRORS = (
    KeyError,
    ValueError,
    FileNotFoundError,
    TimeoutError,
    OnlineCatalogError,
    ArchiveError,
)


@dataclass
class McpContext:
    """The live GUI services the MCP tools drive, plus the main-thread marshaller."""

    api: PluginAPI
    run_on_ui: Callable[
        ..., Any
    ]  # run_on_ui(fn: Callable[[], T], *, timeout: float = ...) -> T
    connection_plugin: ConnectionPlugin
    monitor: MonitorService
    keyring: KeyringService
    log: Logger

    def run_locked(self, fn: Callable[[], T], *, timeout: float = 30.0) -> T:
        """Run ``fn`` on the UI thread while holding the shared project/catalog lock non-blocking.

        The GUI runs a ``.knxproj`` import / project open on a background worker that holds this
        re-entrant ``io_lock`` for the whole write. A concurrent MCP call touching the same
        (non-thread-safe) SQLAlchemy session would race it, so we acquire the lock without blocking
        and, if a background writer holds it, reject with a clear ``project busy`` error rather than
        return partial data or corrupt state. Use this for every project/catalog access; use plain
        ``run_on_ui`` only for state that does not touch those services."""
        lock = self.api.catalog.io_lock

        def _guarded() -> T:
            if not lock.acquire(blocking=False):
                raise ToolError(
                    "project busy: an import or open is in progress; retry shortly"
                )
            try:
                return fn()
            finally:
                lock.release()

        return self.run_on_ui(_guarded, timeout=timeout)


def make_tool(mcp: FastMCP, ctx: McpContext) -> Any:
    """Return a decorator that registers a function as an MCP tool with uniform error translation.

    Supports ``@tool``, ``@tool(read_only=True)``, ``@tool(destructive=True)``, ``@tool(open_world=True)``,
    and ``@tool(annotations=...)``.

    CRITERIA FOR TOOL ANNOTATIONS (MCP Specification 2025-03-26):
    - readOnlyHint=True: Verändert nichts, schreibt weder Dateien noch Cache noch Bus-Telegramme.
    - destructiveHint=True: Löscht Objekte oder Strukturen, überschreibt externe Dateien,
      schreibt auf reale Geräte oder den Bus, löscht Schlüssel oder Katalogdaten.
    - destructiveHint=False (explizit): Alle übrigen Schreib-Tools (anlegen, umbenennen, Werte setzen).
      (Explizit False ist notwendig, da der Spec-Default bei readOnlyHint=False sonst True ist).
    - openWorldHint: Standardmäßig False (geschlossene lokale Domäne), True nur für Tools mit
      Web- oder Bus-Bezug (z. B. Hersteller-Docs, Online-Katalog, Bus-Telegramme).

    SECURITY NOTE:
    Annotations are advisory hints to AI clients/harnesses, not a hard security boundary or
    authorization mechanism. Safety and validation are enforced independently by the server.
    """

    def decorator_or_factory(
        fn_or_none: Callable[..., Any] | None = None,
        *,
        read_only: bool = False,
        destructive: bool = False,
        open_world: bool = False,
        annotations: Any | None = None,
    ) -> Any:
        if annotations is not None and hasattr(annotations, "model_dump"):
            tool_ann: dict[str, Any] = annotations.model_dump(by_alias=True, exclude_none=True)
        else:
            tool_ann = dict(annotations or {})
        if read_only:
            tool_ann["readOnlyHint"] = True
            tool_ann["destructiveHint"] = False
        else:
            tool_ann["readOnlyHint"] = False
            tool_ann["destructiveHint"] = destructive
        tool_ann["openWorldHint"] = open_world

        def register(fn: Callable[..., T]) -> Callable[..., T]:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> T:
                # Log every tool call so the GUI log shows what the LLM is doing (secrets redacted).
                logged = _redact_args(fn.__name__, kwargs)
                ctx.log.info("tool call", tool=fn.__name__, args=logged)
                try:
                    return fn(*args, **kwargs)
                except ToolError as exc:
                    ctx.log.warning("tool error", tool=fn.__name__, error=str(exc))
                    raise
                except _EXPECTED_ERRORS as exc:
                    ctx.log.warning(
                        "tool error", tool=fn.__name__, error=f"{type(exc).__name__}: {exc}"
                    )
                    raise ToolError(f"{type(exc).__name__}: {exc}") from exc

            mcp.tool(annotations=tool_ann)(wrapper)
            return wrapper

        if fn_or_none is not None and callable(fn_or_none):
            return register(fn_or_none)
        return register

    return decorator_or_factory


def require_project(ctx: McpContext) -> None:
    """Raise a ToolError when no project is open (most project tools need one)."""
    if not ctx.api.project.is_open:
        raise ToolError("no project is open; call project_open or project_new first")

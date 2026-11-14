"""Shared context and helpers for the embedded MCP server's tool modules.

The MCP server runs on its own thread (uvicorn), but the GUI's ``project``/``catalog`` services wrap
non-thread-safe SQLAlchemy sessions and per-frame caches. Every tool that touches them therefore
runs its body on the imgui main thread via :attr:`McpContext.run_on_ui` (a
:class:`~editor_gui.concurrency.MainThreadExecutor`), which serialises MCP access with the GUI's own
per-frame reads and writes. Bus operations instead go through the connection service, which already
schedules onto the KNX event loop thread-safely and returns a ``concurrent.futures.Future``.
"""

from __future__ import annotations

import contextlib
import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar

import anyio
from fastmcp import Context
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError
from xknxeditor.prod.errors import ArchiveError

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.backups.service import BackupService
    from editor_gui.plugins.base import Logger, PluginAPI
    from editor_gui.plugins.connection.plugin import ConnectionPlugin
    from editor_gui.plugins.home_assistant.service import HomeAssistantService
    from editor_gui.plugins.keyring.service import KeyringService
    from editor_gui.plugins.monitor.service import MonitorService
    from editor_gui.plugins.project.plugin import ProjectPlugin
    from editor_gui.plugins.recover.service import RecoverService
    from editor_gui.settings_window import SettingsWindow

T = TypeVar("T")

# What a tool does to its environment (the project, files, the catalog, the editor's settings and
# connection, the devices on the bus), told to the client as MCP tool annotations:
# - "read": changes nothing.
# - "write": changes only what is easily set back: project edits project_undo reverts that add or
#   change elements, additions to the catalog, the editor's settings, recording, the connection.
# - "destructive": removes or replaces data (removing, unlinking, deleting, clearing, compacting,
#   copying onto other devices, replacing the open project), writes files outside the project, or
#   acts on devices on the bus (programming, addresses, restarts, resets, group writes, DALI).
ToolKind = Literal["read", "write", "destructive"]

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
    "catalog_alternative_verify_registration": frozenset({"verification_code"}),
}

# Settings whose value settings_set never logs.
_SECRET_SETTINGS = frozenset({"mcp_token"})

# Large upload arguments: logged as a length summary, never their full content.
_BULK_ARGS = frozenset({"data_base64"})


def _is_secret_arg(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _SECRET_ARG_MARKERS)


def _redact_args(tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Tool-call arguments with secrets masked and bulk uploads summarized by length."""
    secret_here = _SECRET_ARGS_BY_TOOL.get(tool, frozenset())
    if tool == "settings_set" and kwargs.get("key") in _SECRET_SETTINGS:
        secret_here = secret_here | {"value"}
    redacted: dict[str, Any] = {}
    for name, value in kwargs.items():
        if isinstance(value, Context):
            continue  # the server context injected by fastmcp, not a client argument
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


def _all_tools() -> bool:
    """Tool discovery without search: every tool is listed."""
    return False


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
    # The project plugin, for the actions it composes (the Tools window, bulk group addresses);
    # None where no GUI runs them (tests).
    project_plugin: ProjectPlugin | None = None
    # The backups of the open project; None where no GUI runs them (tests).
    backups: BackupService | None = None
    # The program's settings (those of the Settings window); None where no GUI runs (tests).
    settings: SettingsWindow | None = None
    # The recovery of devices from the bus (the Recover window); None where no GUI runs it.
    recover: RecoverService | None = None
    # The open project in Home Assistant's KNX integration; None where no GUI runs it.
    home_assistant: HomeAssistantService | None = None
    # Whether clients search for the tools instead of listing all (the setting Tool Discovery).
    tool_search: Callable[[], bool] = _all_tools

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


class ToolDecorator(Protocol):
    """``@tool(kind, open_world=...)``: see :func:`make_tool`."""

    def __call__(
        self, kind: ToolKind, *, open_world: bool = False
    ) -> Callable[[Callable[..., T]], Callable[..., T]]: ...


def tool_annotations(kind: ToolKind, *, open_world: bool = False) -> ToolAnnotations:
    """The MCP annotations of a tool of ``kind`` (see :data:`ToolKind`); ``open_world`` for a tool
    that reaches the bus, the network or the internet."""
    return ToolAnnotations(
        read_only_hint=kind == "read",
        destructive_hint=kind == "destructive",
        open_world_hint=open_world,
    )


def make_tool(mcp: FastMCP, ctx: McpContext) -> ToolDecorator:
    """Return ``tool``: ``@tool(kind)`` registers a function as an MCP tool with uniform error
    translation, its annotations telling the client what it does (see :data:`ToolKind`).

    Expected service errors become :class:`ToolError` (a clean client-facing message); anything else
    propagates as an internal error.
    """

    def tool(
        kind: ToolKind, *, open_world: bool = False
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        # A bare ``@tool`` would pass the function as the kind and register nothing.
        if kind not in ("read", "write", "destructive"):
            raise TypeError(
                f'tool kind must be "read", "write" or "destructive", not {kind!r}'
            )
        annotations = tool_annotations(kind, open_world=open_world)

        def decorator(fn: Callable[..., T]) -> Callable[..., T]:
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
                        "tool error",
                        tool=fn.__name__,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    raise ToolError(f"{type(exc).__name__}: {exc}") from exc

            mcp.tool(wrapper, annotations=annotations)
            return wrapper

        return decorator

    return tool


def require_project(ctx: McpContext) -> None:
    """Raise a ToolError when no project is open (most project tools need one)."""
    if not ctx.api.project.is_open:
        raise ToolError("no project is open; call project_open or project_new first")


def send_progress(
    mcp_ctx: Context | None,
    progress: float,
    total: float | None = None,
    message: str | None = None,
    *,
    timeout: float = 2.0,
) -> None:
    """Send an MCP progress notification to the client from a synchronous tool body.

    A tool that declares a ``Context | None`` parameter gets the server context injected; pass it
    here while a long bus or import operation runs so a capable client can show a progress bar.
    ``report_progress`` is a coroutine bound to the server's event loop, and sync tool bodies run in
    the worker threadpool, so the update is marshalled back onto the loop through anyio's blocking
    portal. Delivery is bounded by ``timeout`` so a stalled or slow client cannot hold up the calling
    thread (which is driving a long operation's wait loop and must stay responsive to its deadline).
    A no-op when no context was injected (headless server, direct test call), when the client did not
    ask for progress, or when there is no portal (not run in the threadpool)."""
    if mcp_ctx is None:
        return

    async def _send() -> None:
        with anyio.move_on_after(timeout):
            await mcp_ctx.report_progress(progress, total, message)

    with contextlib.suppress(RuntimeError):
        anyio.from_thread.run(_send)

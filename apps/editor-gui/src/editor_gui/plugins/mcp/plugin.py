"""Embedded MCP server lifecycle: run FastMCP over Streamable HTTP on a daemon thread.

The editor's main thread is blocked by the imgui run loop, so the server (uvicorn + its own asyncio
loop) runs on a dedicated thread — mirroring the connection plugin's "KNX-Async" thread. Start/stop is
driven from the Settings > MCP tab; ``stop`` asks uvicorn to exit and joins the thread.

FastMCP and the tools are imported, and the server is built, on that thread too: a server restored at
start-up then does not delay the editor's first frame.
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from editor_gui.plugins.base import Logger
from editor_gui.ports import PortInUseError, bind_port

if TYPE_CHECKING:
    import uvicorn

    from editor_gui.plugins.backups.service import BackupService
    from editor_gui.plugins.base import PluginAPI
    from editor_gui.plugins.connection.plugin import ConnectionPlugin
    from editor_gui.plugins.keyring.service import KeyringService
    from editor_gui.plugins.mcp.context import McpContext
    from editor_gui.plugins.monitor.service import MonitorService
    from editor_gui.plugins.project.plugin import ProjectPlugin


def _primary_lan_address() -> str | None:
    """A LAN address this host is reachable at from other machines, best-effort.

    Opens a UDP socket to a public IP WITHOUT sending anything: the kernel picks the outbound
    interface and ``getsockname()`` returns its address. Returns None when no routable interface
    is up (offline, loopback-only), so the caller can fall back."""
    for family, probe in (
        (socket.AF_INET, ("8.8.8.8", 80)),
        (socket.AF_INET6, ("2001:4860:4860::8888", 80)),
    ):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect(probe)
                addr = sock.getsockname()[0]
                if addr and not addr.startswith(("0.", "127.", "::")):
                    return addr
        except OSError:
            continue
    return None


_WILDCARD_BIND_HOSTS = frozenset({"0.0.0.0", "::", "::0", ""})


def advertise_url(host: str, port: str | int, path: str = "/mcp") -> str:
    """Build the URL to SHOW the user as the server address.

    A wildcard bind (0.0.0.0, ::) is correct for the server but useless as a target from another
    machine; substitute the primary LAN address when one is up. Loopback hosts (127.0.0.1, ::1,
    localhost) are deliberate "local only" — kept as-is. Falls back to the raw host if no LAN
    address is reachable, so nothing is invented."""
    stripped = (host or "").strip()
    advertise = stripped
    if stripped in _WILDCARD_BIND_HOSTS:
        lan = _primary_lan_address()
        if lan is not None:
            advertise = f"[{lan}]" if ":" in lan else lan
        else:
            advertise = (
                stripped or "0.0.0.0"
            )  # offline: honest, agent/user sees the bind value
    return f"http://{advertise}:{port}{path}"


def _build_auth(token: str = "") -> Any:
    """Optional bearer-token auth for the HTTP endpoint.

    The token comes from the Settings > MCP field (``token`` arg); if empty it falls back to the
    ``XKNX_MCP_AUTH_TOKEN`` env var. When neither is set, the endpoint is unauthenticated."""
    token = (token or os.environ.get("XKNX_MCP_AUTH_TOKEN", "")).strip()
    if not token:
        return None
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    return StaticTokenVerifier(
        tokens={token: {"client_id": "xknx-editor", "scopes": []}}
    )


def build_http_app(ctx: McpContext, auth: Any) -> Any:
    """Build the ASGI app for the embedded server, with the Host/Origin guard enabled.

    Without that guard, any web page the user happens to visit while the server is running can POST
    to ``http://127.0.0.1:<port>/mcp`` from their browser (DNS rebinding) and drive the tools — which
    here means reading and editing the project, exporting the loaded keyring, and programming devices
    on a live KNX bus. FastMCP leaves the guard off by default for compatibility.

    ``"auto"`` applies it exactly where it is needed: it validates Host and Origin whenever the
    server is bound to loopback (the default, and the only case a browser can reach), and stays out
    of the way of a deliberate non-loopback bind, which the bearer token is there to cover instead.
    """
    from editor_gui.plugins.mcp.server import build_server

    return build_server(ctx, auth=auth).http_app(host_origin_protection="auto")


class McpServerPlugin:
    """Owns the embedded MCP server thread. Wired directly in ``main.py`` (not via the registry)."""

    name = "mcp"

    def __init__(
        self,
        api: PluginAPI,
        run_on_ui: Callable[..., Any],
        connection_plugin: ConnectionPlugin,
        monitor: MonitorService,
        keyring: KeyringService,
        project_plugin: ProjectPlugin | None = None,
        backups: BackupService | None = None,
    ) -> None:
        self._log = Logger(api.log, "mcp")
        self._context_args: dict[str, Any] = {
            "api": api,
            "run_on_ui": run_on_ui,
            "connection_plugin": connection_plugin,
            "monitor": monitor,
            "keyring": keyring,
            "log": self._log,
            "project_plugin": project_plugin,
            "backups": backups,
        }
        self._ctx: McpContext | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        # Guards the hand-over of the server built on its thread against a stop meanwhile.
        self._server_lock = threading.Lock()
        self._stop_requested = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _context(self) -> McpContext:
        if self._ctx is None:
            from editor_gui.plugins.mcp.context import McpContext

            self._ctx = McpContext(**self._context_args)
        return self._ctx

    def start(self, host: str, port: int, token: str = "") -> None:
        """Start the server on ``host``:``port``. The port is taken here, before the server thread
        starts, so a port another program uses raises :class:`PortInUseError` to the caller."""
        if self.is_running:
            return
        sock = bind_port(host, port)
        if sock is None:
            self._log.error("MCP server port in use", host=host, port=port)
            raise PortInUseError(port)
        token = (token or os.environ.get("XKNX_MCP_AUTH_TOKEN", "")).strip()
        if not token and host not in ("127.0.0.1", "::1", "localhost"):
            # No token on a non-loopback bind means anyone who can reach this host can read/edit the
            # project and drive the KNX bus. Surface it loudly; do not silently expose it.
            self._log.warning(
                "MCP server exposed on a non-loopback address without a bearer token",
                host=host,
            )
        with self._server_lock:
            self._stop_requested = False
        port = sock.getsockname()[1]
        self._thread = threading.Thread(
            target=self._serve,
            args=(sock, host, port, token),
            daemon=True,
            name="xknxeditor-mcp",
        )
        self._thread.start()
        self._log.info("MCP server started", host=host, port=port, auth=bool(token))

    def _serve(self, sock: socket.socket, host: str, port: int, token: str) -> None:
        """Build the server and run it on the bound ``sock`` until it is asked to exit (on the
        server thread)."""
        with sock:
            try:
                import uvicorn

                config = uvicorn.Config(
                    build_http_app(self._context(), _build_auth(token)),
                    host=host,
                    port=port,
                    log_level="warning",
                )
                server = uvicorn.Server(config)
            except Exception as e:
                self._log.error(
                    "MCP server start failed", error=f"{type(e).__name__}: {e}"
                )
                return
            with self._server_lock:
                if self._stop_requested:
                    return
                self._server = server
            server.run(sockets=[sock])

    def stop(self) -> None:
        with self._server_lock:
            self._stop_requested = True
            if self._server is not None:
                self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                # The server did not exit in time. Keep the references so is_running stays true and a
                # second server cannot be started on the same port while this one is still finishing.
                self._log.warning(
                    "MCP server did not stop within timeout; still shutting down"
                )
                return
        self._server = None
        self._thread = None
        self._log.info("MCP server stopped")

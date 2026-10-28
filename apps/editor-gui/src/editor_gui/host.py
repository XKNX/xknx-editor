"""Where the editor runs: on its own, or as part of a program that starts it (the command line).

- ``--home-assistant=integration`` or ``--home-assistant=app``: the editor runs inside Home
  Assistant, started by its integration (the panel in the sidebar) or as a Home Assistant app
  (the add-on). Home Assistant updates and stops it, so the editor leaves out its own update check
  and File > Exit; other differences check :func:`home_assistant` too.
- ``--mcp=[host:]port``: the MCP server runs on this address from the start, with the token in
  ``XKNX_MCP_AUTH_TOKEN``. The program that started the editor set it up, so the MCP menu and the
  AI tab show it without the fields to change it.
- ``--mcp-url=URL``: the address MCP clients use, when they reach the server through a proxy (the
  integration's address in Home Assistant).
"""

from __future__ import annotations

from dataclasses import dataclass

HOME_ASSISTANT_INTEGRATION = "integration"
HOME_ASSISTANT_APP = "app"
# Stands for the user's Home Assistant access token in the example commands of the AI tab.
HOME_ASSISTANT_TOKEN_PLACEHOLDER = "HOME_ASSISTANT_TOKEN"
_HOME_ASSISTANT_KINDS = (HOME_ASSISTANT_INTEGRATION, HOME_ASSISTANT_APP)
_MCP_DEFAULT_HOST = "127.0.0.1"


@dataclass(frozen=True)
class HostOptions:
    # "integration" or "app" inside Home Assistant, else None.
    home_assistant: str | None = None
    # The MCP server's address set by the program that started the editor (--mcp).
    mcp_host: str | None = None
    mcp_port: int | None = None
    # The address MCP clients use (--mcp-url).
    mcp_url: str | None = None

    @property
    def mcp_managed(self) -> bool:
        """Whether the program that started the editor set up its MCP server."""
        return self.mcp_port is not None


def host_options(argv: list[str]) -> HostOptions:
    """The options of this module from the command line (unknown values are ignored)."""
    home_assistant: str | None = None
    mcp_host: str | None = None
    mcp_port: int | None = None
    mcp_url: str | None = None
    for arg in argv[1:]:
        name, _, value = arg.partition("=")
        if name == "--home-assistant" and value in _HOME_ASSISTANT_KINDS:
            home_assistant = value
        elif name == "--mcp" and value:
            host, _, port = value.rpartition(":")
            if port.isdigit():
                mcp_host, mcp_port = host or _MCP_DEFAULT_HOST, int(port)
        elif name == "--mcp-url" and value:
            mcp_url = value
    return HostOptions(home_assistant, mcp_host, mcp_port, mcp_url)


_current = HostOptions()


def current() -> HostOptions:
    """The options this editor was started with."""
    return _current


def set_current(options: HostOptions) -> None:
    global _current
    _current = options


def home_assistant() -> str | None:
    """``"integration"`` or ``"app"`` when the editor runs inside Home Assistant, else None."""
    return _current.home_assistant

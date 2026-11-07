"""Where the editor runs: on its own, or as part of a program that starts it (the command line).

- ``--home-assistant``: the editor runs inside Home Assistant, started by its integration (the
  panel in the sidebar). Home Assistant updates it, so a new version found by the update check is
  not installed or downloaded: the editor asks to update the integration instead. Other
  differences check :func:`home_assistant` too.
- ``--mcp=[host:]port``: the MCP server runs on this address from the start, with the token in
  ``XKNX_MCP_AUTH_TOKEN``. The program that started the editor set it up, so the MCP Server menu
  and the AI/MCP settings show it without the fields to change it.
- ``--mcp-url=URL``: the address MCP clients use, when they reach the server through a proxy (the
  integration's address in Home Assistant).
- ``--language=CODE``: the language of the program that started the editor (Home Assistant's):
  the editor's language, and so its product language, until one is chosen in its settings, in
  place of the system's (which is none in Home Assistant's container).
"""

from __future__ import annotations

from dataclasses import dataclass

# Stands for the user's Home Assistant access token in the example commands of the AI/MCP
# settings.
HOME_ASSISTANT_TOKEN_PLACEHOLDER = "HOME_ASSISTANT_TOKEN"
_MCP_DEFAULT_HOST = "127.0.0.1"


@dataclass(frozen=True)
class HostOptions:
    # Inside Home Assistant (--home-assistant).
    home_assistant: bool = False
    # The MCP server's address set by the program that started the editor (--mcp).
    mcp_host: str | None = None
    mcp_port: int | None = None
    # The address MCP clients use (--mcp-url).
    mcp_url: str | None = None
    # The language of the program that started the editor (--language), e.g. "pt-BR".
    language: str | None = None

    @property
    def mcp_managed(self) -> bool:
        """Whether the program that started the editor set up its MCP server."""
        return self.mcp_port is not None


def host_options(argv: list[str]) -> HostOptions:
    """The options of this module from the command line (unknown values are ignored)."""
    home_assistant = False
    mcp_host: str | None = None
    mcp_port: int | None = None
    mcp_url: str | None = None
    language: str | None = None
    for arg in argv[1:]:
        name, _, value = arg.partition("=")
        if arg == "--home-assistant":
            home_assistant = True
        elif name == "--mcp" and value:
            host, _, port = value.rpartition(":")
            if port.isdigit():
                mcp_host, mcp_port = host or _MCP_DEFAULT_HOST, int(port)
        elif name == "--mcp-url" and value:
            mcp_url = value
        elif name == "--language" and value:
            language = value
    return HostOptions(home_assistant, mcp_host, mcp_port, mcp_url, language)


_current = HostOptions()


def current() -> HostOptions:
    """The options this editor was started with."""
    return _current


def set_current(options: HostOptions) -> None:
    global _current
    _current = options


def home_assistant() -> bool:
    """Whether the editor runs inside Home Assistant."""
    return _current.home_assistant

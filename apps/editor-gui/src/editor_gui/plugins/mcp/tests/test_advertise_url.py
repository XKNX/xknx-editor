"""Unit tests for mcp.plugin.advertise_url: wildcard-bind host substituted with a usable address,
loopback hosts kept, port + path preserved. Primary-LAN probe is patched for determinism."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from editor_gui.plugins.mcp.plugin import advertise_url


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "::0", ""])
def test_wildcard_bind_uses_lan_address(host: str) -> None:
    with patch(
        "editor_gui.plugins.mcp.plugin._primary_lan_address", return_value="192.168.1.5"
    ):
        assert advertise_url(host, "8765") == "http://192.168.1.5:8765/mcp"


def test_wildcard_bind_uses_bracketed_ipv6_literal() -> None:
    with patch(
        "editor_gui.plugins.mcp.plugin._primary_lan_address", return_value="fe80::1"
    ):
        assert advertise_url("::", 8765) == "http://[fe80::1]:8765/mcp"


def test_wildcard_bind_without_lan_keeps_bind_host() -> None:
    # Offline / loopback-only: honest fallback, never invent an address.
    with patch("editor_gui.plugins.mcp.plugin._primary_lan_address", return_value=None):
        assert advertise_url("0.0.0.0", 8765) == "http://0.0.0.0:8765/mcp"
        # Empty host becomes 0.0.0.0 so the URL is well-formed.
        assert advertise_url("", 8765) == "http://0.0.0.0:8765/mcp"


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "::1", "localhost", "192.168.1.5", "my.host"]
)
def test_non_wildcard_host_is_kept_verbatim(host: str) -> None:
    # The user chose a specific interface or name; respect it.
    with patch(
        "editor_gui.plugins.mcp.plugin._primary_lan_address", return_value="10.0.0.1"
    ):
        assert advertise_url(host, 8765) == f"http://{host}:8765/mcp"


def test_custom_path_is_preserved() -> None:
    assert advertise_url("127.0.0.1", 8765, path="/foo").endswith("/foo")

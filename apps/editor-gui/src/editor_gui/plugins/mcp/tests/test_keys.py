"""MCP tools for the keys read from the KNX authoring tool's assemblies: status, extraction (stored
like the GUI does, never returned) and resetting the signing key."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.mcp.tools import keys
from xknxeditor.proj import KeyExtractionError

from .test_server import _build_ctx, _data


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The keys the tools store, in place of the user's settings."""
    store: dict[str, Any] = {}
    monkeypatch.setattr(
        keys.signing_key, "save_key", lambda *k: store.__setitem__("signing", k)
    )
    monkeypatch.setattr(
        keys.signing_key, "clear_key", lambda: store.pop("signing", None)
    )
    monkeypatch.setattr(
        keys.signing_key, "load_cached_key", lambda: store.get("signing")
    )
    monkeypatch.setattr(
        keys.trace_key, "save_key", lambda *k: store.__setitem__("trace", k)
    )
    monkeypatch.setattr(keys, "trace_key_available", lambda: "trace" in store)
    monkeypatch.setattr(
        keys.myknx_oauth, "save_credentials", lambda *c: store.__setitem__("myknx", c)
    )
    monkeypatch.setattr(
        keys.myknx_oauth, "load_cached_credentials", lambda: store.get("myknx")
    )
    monkeypatch.setattr(keys, "extraction_backend", lambda: "dotnet")
    monkeypatch.setattr(keys, "extract_converter_key", lambda path: (11, 22, 65537))
    monkeypatch.setattr(keys, "extract_trace_key", lambda path: (b"k", b"iv", "m"))
    monkeypatch.setattr(
        keys, "extract_oauth_credentials", lambda path: ("id", "secret")
    )
    return store


async def test_keys_are_extracted_stored_and_never_returned(
    tmp_path: Path, stored: dict[str, Any]
) -> None:
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        assert await _data(client, "keys_status") == {
            "signing_key": "placeholder",
            "trace_key": False,
            "myknx_client": False,
            "extraction": "dotnet",
        }
        for name, dll in (
            ("signing_key_extract", "Knx.Ets.XmlSigning.dll"),
            ("trace_key_extract", "Knx.Ets.Common.dll"),
            ("myknx_client_extract", "Knx.Ets.MyKnx.dll"),
        ):
            result = await client.call_tool(name, {"path": f"/files/{dll}"})
            assert result.data == {"stored": True}
            # Nothing of the key in the answer.
            assert "secret" not in str(result.content) and "65537" not in str(
                result.content
            )
        assert stored == {
            "signing": (11, 22, 65537),
            "trace": (b"k", b"iv", "m"),
            "myknx": ("id", "secret"),
        }
        status = await _data(client, "keys_status")
        assert status["signing_key"] == "extracted"
        assert status["trace_key"] and status["myknx_client"]
        assert await _data(client, "signing_key_reset") == {"stored": False}
        assert (await _data(client, "keys_status"))["signing_key"] == "placeholder"


async def test_extraction_errors(
    tmp_path: Path, stored: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_log4net(path: str) -> tuple[bytes, bytes, str]:
        raise KeyExtractionError(
            "Could not load file or assembly 'log4net, Version=3.0.3.0'"
        )

    def no_runtime(path: str) -> tuple[int, int, int]:
        raise KeyExtractionError("no .NET runtime available to read the assembly")

    monkeypatch.setattr(keys, "extract_trace_key", no_log4net)
    monkeypatch.setattr(keys, "extract_converter_key", no_runtime)
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        with pytest.raises(Exception, match=r"needs log4net\.dll from its folder"):
            await client.call_tool(
                "trace_key_extract", {"path": "/files/Knx.Ets.Common.dll"}
            )
        with pytest.raises(Exception, match=r"no \.NET runtime"):
            await client.call_tool(
                "signing_key_extract", {"path": "/files/Knx.Ets.XmlSigning.dll"}
            )
    assert stored == {}

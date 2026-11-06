"""Alternative catalog tools expose the same .knxprod import and registration as the GUI."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from editor_gui.plugins.catalog.alternative import (
    AlternativeCatalogClient,
    AlternativeSettings,
)
from editor_gui.plugins.catalog.tests.test_alternative import index
from editor_gui.plugins.mcp.context import _redact_args
from editor_gui.plugins.mcp.server import build_server

from .test_server import _FIXTURE, _build_ctx, _data


async def test_search_import_settings_and_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("editor_gui.settings.config_dir", lambda: tmp_path / "settings")
    monkeypatch.setattr(
        "editor_gui.plugins.catalog.alternative.time.sleep", lambda _seconds: None
    )
    data = _FIXTURE.read_bytes()
    requests: list[str] = []

    def serve(r: httpx.Request) -> httpx.Response:
        requests.append(r.url.path)
        if r.url.path.endswith("index.json"):
            return httpx.Response(200, json=index(data))
        if r.url.path.endswith("token"):
            assert json.loads(r.content) == {
                "user": "testuser",
                "password": "test-password",
            }
            return httpx.Response(
                200,
                json={"token": "test-token", "tokenType": "Bearer", "expiresIn": 43200},
            )
        assert (
            r.url.path == "/api/download"
            and r.headers["authorization"] == "Bearer test-token"
        )
        return httpx.Response(200, content=data)

    ctx = _build_ctx(tmp_path)
    ctx.api.catalog.alternative.close()
    ctx.api.catalog.alternative = AlternativeCatalogClient(
        tmp_path,
        settings=AlternativeSettings("https://catalog.example"),
        transport=httpx.MockTransport(serve),
    )
    mcp = build_server(ctx)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        for name in (
            "status",
            "search",
            "configure",
            "refresh",
            "download",
            "register",
            "resume_registration",
            "verify_registration",
            "resend_registration_code",
        ):
            hints = tools["catalog_alternative_" + name].annotations
            assert hints.read_only_hint == (name in ("status", "search", "refresh"))
            assert hints.open_world_hint == (
                name not in ("status", "search", "configure")
            )
            assert not hints.destructive_hint
        status = await _data(
            client,
            "catalog_alternative_configure",
            base_url="https://catalog.example",
            user="testuser",
            password="test-password",
        )
        assert status["has_password"] and "test-password" not in json.dumps(status)
        assert (await _data(client, "catalog_alternative_refresh"))["count"] == 1
        before = len(requests)
        result = await _data(
            client, "catalog_alternative_search", query="MDT AKD040102"
        )
        assert len(requests) == before and result["total"] == 1
        item = result["items"][0]
        assert item["programs"][0]["version"] == "03" and item["file_type"] == "knxprod"
        assert (
            await _data(client, "catalog_alternative_download", entry_id=item["id"])
        )["added_refs"]
        assert (await _data(client, "catalog_list_products"))["count"] > 0
        assert (
            await _data(client, "catalog_alternative_download", entry_id=item["id"])
        )["added_refs"] == []
        with pytest.raises(Exception, match="unknown catalog entry"):
            await _data(client, "catalog_alternative_download", entry_id="legacy")
        assert "test-token" not in json.dumps(
            await _data(client, "catalog_alternative_status")
        )
    ctx.api.catalog.alternative.close()


def test_registration_secrets_are_redacted() -> None:
    assert _redact_args(
        "catalog_alternative_register", {"password": "test-password"}
    ) == {"password": "***"}
    assert _redact_args(
        "catalog_alternative_verify_registration", {"verification_code": "123456"}
    ) == {"verification_code": "***"}

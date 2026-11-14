"""Online catalog metadata exposes the application identity used by recovery."""

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.catalog.online_catalog import parse_catalog_items
from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


async def test_online_items_report_full_application_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _build_ctx(tmp_path)
    items = parse_catalog_items(b"""{
        "ManufacturerId": 2,
        "Entries": [
            {"Id": "actuator", "ApplicationIdentifier": [0, 2, 160, 30, 32]},
            {"Id": "unknown"}
        ]
    }""")
    monkeypatch.setattr(ctx.api.catalog, "online_catalog_items", lambda mid: items)
    async with Client(build_server(ctx)) as client:
        result = await _data(client, "catalog_online_items", manufacturer_id=2)
    actuator, unknown = result["items"]
    assert (
        actuator["application_manufacturer_id"],
        actuator["application_number"],
        actuator["application_version"],
    ) == (2, 0xA01E, 0x20)
    assert unknown["application_manufacturer_id"] is None
    assert unknown["application_number"] is None
    assert unknown["application_version"] is None


async def test_recovery_offers_and_loads_one_other_version(
    tmp_path, monkeypatch
) -> None:
    from editor_gui.plugins.recover.matching import OnlineVersion
    from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
    from xknxeditor.recover import AppId

    ctx = _build_ctx(tmp_path)
    service = RecoverService(ctx.api)
    ctx.recover = service
    entry = RecoverEntry("1.1.5", 0x0013, app_id=AppId("M-0002", 0xA01E, 0x10))
    entry.online_versions = (OnlineVersion("article-v2", "Actuator", "SA/S", 0x20),)
    service.entries = [entry]
    downloads = []

    def download(ids):
        downloads.append(ids)
        return []

    monkeypatch.setattr(ctx.api.catalog, "download_online_products", download)
    async with Client(build_server(ctx)) as client:
        status = await _data(client, "recover_status")
        assert status["entries"][0]["online_versions"] == [
            {
                "catalog_item_id": "article-v2",
                "product_name": "Actuator",
                "order_number": "SA/S",
                "application_version": 32,
            }
        ]
        with pytest.raises(Exception, match="not an offered"):
            await client.call_tool(
                "recover_load_other_version",
                {"device_address": "1.1.5", "catalog_item_id": "unrelated"},
            )
        status = await _data(
            client,
            "recover_load_other_version",
            device_address="1.1.5",
            catalog_item_id="article-v2",
        )
        tool = next(
            t
            for t in await client.list_tools()
            if t.name == "recover_load_other_version"
        )
        assert tool.annotations.open_world_hint and not tool.annotations.read_only_hint
    assert downloads == [["article-v2"]]
    assert not status["busy"]
    assert not status["entries"][0]["selected"]
    assert status["entries"][0]["recovered"] is None

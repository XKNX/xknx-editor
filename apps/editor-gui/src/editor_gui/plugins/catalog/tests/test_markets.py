"""The markets of the online catalog: which markets offer each product, their names, and both kept
in the product index cache."""

from __future__ import annotations

import json
from pathlib import Path

from editor_gui.plugins.catalog.online_catalog import (
    OnlineCatalogClient,
    parse_catalog_items,
    parse_markets,
)

_INDEX = json.dumps(
    {
        "ManufacturerId": 7,
        "Entries": [
            {"Id": "CI-1", "CatalogItemName": "Actuator"},
            {"Id": "CI-2", "CatalogItemName": "Dimmer"},
            {"Id": "CI-3", "CatalogItemName": "Sensor"},
        ],
        "Markets": [
            {
                "Id": "M-DE",
                "DefaultName": "Germany",
                "CatalogIds": ["CI-1", "CI-2"],
                "Translations": [{"Language": "de-DE", "Text": "Deutschland"}],
            },
            {"Id": "M-AT", "DefaultName": "Austria", "CatalogIds": ["CI-1"]},
        ],
    }
).encode()


def test_each_product_knows_the_markets_offering_it() -> None:
    items = {i.id: i for i in parse_catalog_items(_INDEX)}
    assert items["CI-1"].markets == ("M-DE", "M-AT")
    assert items["CI-2"].markets == ("M-DE",)
    # Listed in no market: offered nowhere (and not downloadable).
    assert items["CI-3"].markets == ()
    assert not items["CI-3"].downloadable


def test_an_index_without_markets_leaves_them_unknown() -> None:
    index = json.dumps({"Entries": [{"Id": "CI-1", "CatalogItemName": "A"}]}).encode()
    (item,) = parse_catalog_items(index)
    assert item.markets is None
    assert parse_markets(index) == []


def test_market_names_in_the_wanted_language() -> None:
    germany = next(m for m in parse_markets(_INDEX) if m.id == "M-DE")
    assert germany.name_in("de") == "Deutschland"
    assert germany.name_in("fr-FR") == "Germany"


def test_the_index_cache_keeps_the_markets(tmp_path: Path, monkeypatch) -> None:
    client = OnlineCatalogClient(tmp_path)
    monkeypatch.setattr(
        "editor_gui.plugins.catalog.online_catalog._http_get",
        lambda url: (
            b'<ArrayOfunsignedShort xmlns="http://schemas.microsoft.com/2003/10/'
            b'Serialization/Arrays"><unsignedShort>7</unsignedShort></ArrayOfunsignedShort>'
        ),
    )
    monkeypatch.setattr(client, "download_index", lambda mid: _INDEX)
    client.refresh_index()
    reloaded = OnlineCatalogClient(tmp_path)
    markets = reloaded.cached_markets()
    assert markets is not None
    assert [m.name for m in markets] == ["Austria", "Germany"]
    assert markets[1].name_in("de-DE") == "Deutschland"
    items = {
        i.id: i for items in (reloaded.cached_index() or {}).values() for i in items
    }
    assert items["CI-1"].markets == ("M-DE", "M-AT")


def test_an_index_cached_without_markets_reports_them_unknown(tmp_path: Path) -> None:
    (tmp_path / "online_catalog_index.json").write_text(
        json.dumps(
            {
                "format": 5,
                "fetched_at": "2026-10-22T12:00:00+00:00",
                "index": {"7": [{"id": "CI-1", "name": "A", "dl": True}]},
            }
        ),
        "utf-8",
    )
    client = OnlineCatalogClient(tmp_path)
    assert client.cached_markets() is None
    (item,) = (client.cached_index() or {})[7]
    assert item.markets is None


def test_a_refresh_fetches_manufacturers_cached_without_markets(
    tmp_path: Path, monkeypatch
) -> None:
    test_an_index_cached_without_markets_reports_them_unknown(tmp_path)
    client = OnlineCatalogClient(tmp_path)
    monkeypatch.setattr(
        "editor_gui.plugins.catalog.online_catalog._http_get",
        lambda url: (
            b'<ArrayOfunsignedShort xmlns="http://schemas.microsoft.com/2003/10/'
            b'Serialization/Arrays"><unsignedShort>7</unsignedShort></ArrayOfunsignedShort>'
        ),
    )
    fetched: list[int] = []
    monkeypatch.setattr(
        client, "download_index", lambda mid: fetched.append(mid) or _INDEX
    )
    client.refresh_index()
    assert fetched == [7]
    items = {i.id: i for i in (client.cached_index() or {})[7]}
    assert items["CI-1"].markets == ("M-DE", "M-AT")
    # Cached with their markets, they are skipped again.
    client.refresh_index()
    assert fetched == [7]

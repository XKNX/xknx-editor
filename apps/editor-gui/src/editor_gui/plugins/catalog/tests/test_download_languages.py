"""Products download in the editor's languages including the variants a manufacturer labels its
texts with ("de", "es-MX"), as the online index lists them per product."""

from __future__ import annotations

import json
from typing import Any

from editor_gui.plugins.catalog.online_catalog import (
    OnlineCatalogItem,
    _item_from_dict,
    _item_to_dict,
    parse_catalog_items,
    with_variants,
)
from editor_gui.plugins.catalog.service import CatalogService


def test_variants_of_the_wanted_languages_follow_each_of_them() -> None:
    offered = ["en-US", "es-MX", "de", "fr-FR", "de-AT"]
    assert with_variants(["de-DE", "en-US", "es-ES"], offered) == [
        "de-DE",
        "de",
        "de-AT",
        "en-US",
        "es-ES",
        "es-MX",
    ]
    assert with_variants(["nl-NL"], offered) == ["nl-NL"]


def test_the_index_gives_each_product_its_text_languages() -> None:
    index = json.dumps(
        {
            "ManufacturerId": 7,
            "Entries": [
                {
                    "Id": "CI-1",
                    "CatalogItemName": "Actuator",
                    "ProductDefaultLanguage": "de",
                    "Translations": [{"Language": "en-GB"}, {"Language": "de"}],
                }
            ],
        }
    ).encode()
    (item,) = parse_catalog_items(index)
    assert item.languages == ("de", "en-GB")
    assert _item_from_dict(_item_to_dict(item)).languages == ("de", "en-GB")


class _Client:
    def __init__(self, items: list[OnlineCatalogItem]) -> None:
        self.items = items
        self.requested: list[str] = []

    def cached_index(self) -> dict[int, list[OnlineCatalogItem]] | None:
        return {7: self.items} if self.items else None

    def catalog_items(self, manufacturer_id: int) -> list[OnlineCatalogItem]:
        return [
            OnlineCatalogItem("CI-2", "Dimmer", "D1", True, languages=("de", "en-US"))
        ]

    def download_product(self, ids: list[str], languages: list[str]) -> bytes:
        self.requested = languages
        return b""


def test_a_download_asks_for_the_variants_the_product_has() -> None:
    item = OnlineCatalogItem(
        "CI-1", "Actuator", "A1", True, languages=("es-MX", "en-GB", "fr-FR")
    )
    client = _Client([item])
    service: Any = CatalogService.__new__(CatalogService)
    service._online_client = client
    service._language = "es-ES"
    service._browsed = {}
    service._import_knxprod_bytes = lambda data: []
    service.download_online_products(["CI-1"])
    assert client.requested == ["es-ES", "es-MX"]
    # Languages chosen explicitly are requested as they are.
    service.download_online_products(["CI-1"], ["fr-FR"])
    assert client.requested == ["fr-FR"]


def test_a_product_listed_while_browsing_is_known_without_the_index() -> None:
    client = _Client([])
    service: Any = CatalogService.__new__(CatalogService)
    service._online_client = client
    service._language = "de-DE"
    service._browsed = {}
    service._import_knxprod_bytes = lambda data: []
    service.online_catalog_items(9)
    service.download_online_products(["CI-2"])
    assert client.requested == ["de-DE", "de"]


def test_the_index_status_shows_the_local_fetch_date() -> None:
    from datetime import datetime

    from editor_gui.plugins.catalog.ui.panel import fetched_date
    from editor_gui.regional import regional_format
    from editor_gui.widgets.value_pickers import format_date

    stamp = "2026-10-22T12:00:00+00:00"
    local = datetime.fromisoformat(stamp).astimezone().date().isoformat()
    assert fetched_date(stamp) == format_date(local, True, regional_format())
    assert fetched_date(None) == "-"
    assert fetched_date("not a date") == "-"

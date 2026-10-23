"""The online catalog's market and product language filters."""

from __future__ import annotations

from typing import Any

import pytest

from editor_gui.plugins.catalog import filters as filters_module
from editor_gui.plugins.catalog.filters import OnlineFilters
from editor_gui.plugins.catalog.online_catalog import (
    MARKET_REGIONS,
    OnlineCatalogItem,
    OnlineMarket,
    market_for_region,
    offered,
    search_index,
)

_DE = OnlineMarket("M-DE", "Germany")
_AT = OnlineMarket("M-AT", "Austria")


def _item(cid: str, markets: tuple[str, ...] | None, languages: tuple[str, ...]) -> Any:
    return OnlineCatalogItem(
        cid, f"Product {cid}", cid, True, markets=markets, languages=languages
    )


def test_a_product_is_offered_in_its_markets_and_languages() -> None:
    item = _item("1", ("M-DE",), ("de-DE", "en-US"))
    assert offered(item, None, None)
    assert offered(item, "M-DE", "de-AT")
    assert not offered(item, "M-AT", None)
    assert not offered(item, None, "fr-FR")
    # Unknown markets or languages (an older index) hide nothing.
    assert offered(_item("2", None, ()), "M-AT", "fr-FR")


def test_the_market_of_the_region() -> None:
    assert market_for_region([_AT, _DE], "de") == "M-DE"
    assert market_for_region([_AT], "DE") is None
    assert market_for_region([_DE], "XX") is None
    assert len(MARKET_REGIONS) == len(set(MARKET_REGIONS.values())) == 69


class _Client:
    def __init__(self, markets: list[OnlineMarket] | None) -> None:
        self.markets = markets
        self.index = {
            7: [
                _item("1", ("M-DE",), ("de-DE",)),
                _item("2", ("M-AT",), ("de-DE", "fr-FR")),
                _item("3", ("M-DE", "M-AT"), ("fr-FR",)),
            ]
        }

    def cached_markets(self) -> list[OnlineMarket] | None:
        return self.markets

    def cached_index(self) -> dict[int, list[Any]]:
        return self.index


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    stored: dict[str, Any] = {}
    monkeypatch.setattr(filters_module, "load_settings", lambda name: dict(stored))
    monkeypatch.setattr(
        filters_module, "save_settings", lambda name, data: stored.update(data)
    )
    monkeypatch.setattr(filters_module, "os_region", lambda: "DE")
    monkeypatch.setattr(filters_module, "product_language", lambda: "fr-FR")
    return stored


def test_the_market_starts_from_the_region_and_is_kept(
    settings: dict[str, Any],
) -> None:
    filters = OnlineFilters(_Client([_AT, _DE]))  # type: ignore[arg-type]
    assert filters.market == "M-DE"
    assert filters.counts() == (2, 3)
    filters.set_market(None)
    assert filters.market is None and settings["market"] == ""
    assert filters.counts() == (3, 3)
    # Chosen once, it stays chosen.
    assert OnlineFilters(_Client([_AT, _DE])).market is None  # type: ignore[arg-type]


def test_without_markets_in_the_index_nothing_is_filtered(
    settings: dict[str, Any],
) -> None:
    filters = OnlineFilters(_Client(None))  # type: ignore[arg-type]
    assert filters.market is None
    assert filters.counts() == (3, 3)


def test_only_products_in_the_preferred_product_language(
    settings: dict[str, Any],
) -> None:
    filters = OnlineFilters(_Client([_AT, _DE]))  # type: ignore[arg-type]
    filters.set_market(None)
    filters.set_only_product_language(True)
    assert settings["only_product_language"] is True
    assert filters.counts() == (2, 3)
    index = _Client(None).index
    results, total = search_index(index, "product", keep=filters.predicate())
    assert total == 2 and {i.id for i in results} == {"2", "3"}

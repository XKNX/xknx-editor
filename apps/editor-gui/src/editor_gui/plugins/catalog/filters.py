"""The online catalog's market and product language filters, kept in the catalog settings."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from editor_gui.languages import PRODUCT_LANGUAGES, product_language
from editor_gui.plugins.catalog.online_catalog import market_for_region, offered
from editor_gui.regional import os_region
from editor_gui.settings import load_settings, save_settings

if TYPE_CHECKING:
    from editor_gui.plugins.catalog.online_catalog import (
        OnlineCatalogClient,
        OnlineCatalogItem,
        OnlineMarket,
    )

_SETTINGS = "catalog"


class OnlineFilters:
    def __init__(self, client: OnlineCatalogClient) -> None:
        self._client = client
        data = load_settings(_SETTINGS)
        market = data.get("market")
        # The chosen market's id, "" for all markets, None until one is chosen.
        self._market: str | None = market if isinstance(market, str) else None
        self._only_language = data.get("only_product_language") is True
        self._counts: tuple[object, tuple[int, int]] | None = None

    def markets(self) -> list[OnlineMarket] | None:
        """The markets of the product index; None when it does not list them."""
        return self._client.cached_markets()

    @property
    def market(self) -> str | None:
        """The id of the market shown, None for all. Until one is chosen, the market of the
        operating system's region when the catalog has one."""
        if self._market is not None:
            return self._market or None
        markets = self.markets()
        return market_for_region(markets, os_region()) if markets else None

    def set_market(self, market: str | None) -> None:
        self._market = market or ""
        self._save()

    @property
    def only_product_language(self) -> bool:
        """Whether only products with texts in the preferred product language are shown."""
        return self._only_language

    def set_only_product_language(self, only: bool) -> None:
        self._only_language = only
        self._save()

    def product_language_name(self) -> str:
        """The preferred product language's name in the current language."""
        code = product_language()
        return next(
            (lang.name() for lang in PRODUCT_LANGUAGES if lang.culture == code), code
        )

    def key(self) -> tuple[str | None, str | None]:
        """What the shown products depend on: the market and the language they need."""
        return self.market, product_language() if self._only_language else None

    def predicate(self) -> Callable[[OnlineCatalogItem], bool]:
        """Whether a product passes the filters as they are set now."""
        market, language = self.key()
        return lambda item: offered(item, market, language)

    def counts(self) -> tuple[int, int] | None:
        """``(shown, total)`` products of the index with the filters on; None without an index."""
        index = self._client.cached_index()
        if not index:
            return None
        cache_key = (self.key(), id(index))
        if self._counts is None or self._counts[0] != cache_key:
            items = [item for items in index.values() for item in items]
            keep = self.predicate()
            shown = sum(1 for item in items if keep(item))
            self._counts = (cache_key, (shown, len(items)))
        return self._counts[1]

    def _save(self) -> None:
        data = load_settings(_SETTINGS)
        data["market"] = self._market
        data["only_product_language"] = self._only_language
        save_settings(_SETTINGS, data)

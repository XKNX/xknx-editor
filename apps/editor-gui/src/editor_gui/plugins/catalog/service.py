"""GUI adapter: caching, path-based import, online catalog, and new-product reporting."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from editor_gui.concurrency import io_guarded

_CATALOG_SETTINGS = "catalog"

if TYPE_CHECKING:
    from collections.abc import Callable

    from editor_gui.plugins.catalog.online_catalog import (
        OnlineCatalogItem,
        OnlineIndexStatus,
        OnlineManufacturer,
    )
    from xknxeditor.catalog import ProductSummary
    from xknxeditor.prod import Application


class CatalogService:
    def __init__(
        self, catalog_path: Path, io_lock: threading.RLock | None = None
    ) -> None:
        from editor_gui.plugins.catalog.online_catalog import (
            DEFAULT_LANGUAGE,
            OnlineCatalogClient,
        )
        from editor_gui.settings import load_settings
        from xknxeditor.catalog import CatalogService as _CatalogService

        self._service = _CatalogService(catalog_path)
        self._products: list[ProductSummary] | None = None
        # Shared with the project service so a background import can hold both while it writes.
        self._io_lock = io_lock or threading.RLock()
        # Manufacturer list from the KNX online catalog service (cached next to the db).
        self._online_client = OnlineCatalogClient(catalog_path.parent)
        # Selected download country/language, persisted in the app config.
        lang = load_settings(_CATALOG_SETTINGS).get("language")
        self._language: str = (
            lang if isinstance(lang, str) and lang else DEFAULT_LANGUAGE
        )

    @property
    def online_language(self) -> str:
        """The selected download country/language (KNX language id, e.g. ``de-DE``)."""
        return self._language

    def set_online_language(self, code: str) -> None:
        """Select and persist the download country/language."""
        from editor_gui.settings import load_settings, save_settings

        self._language = code
        data = load_settings(_CATALOG_SETTINGS)
        data["language"] = code
        save_settings(_CATALOG_SETTINGS, data)

    def online_manufacturers(self) -> list[OnlineManufacturer] | None:
        """The cached online manufacturer list, or None when the cache is empty.

        Never touches the network: the panel reads this every frame."""
        return self._online_client.cached_manufacturers()

    def refresh_online_manufacturers(self) -> list[OnlineManufacturer]:
        """Download the manufacturer list now; raises OnlineCatalogError on failure."""
        return self._online_client.refresh_manufacturers()

    @property
    def io_lock(self) -> threading.RLock:
        """The re-entrant lock a background import holds while writing catalog + project data."""
        return self._io_lock

    @io_guarded(list)
    def get_products(self) -> list[ProductSummary]:
        """Cached product list for the browse panel."""
        if self._products is None:
            self._products = self._service.list_products()
        return self._products

    def import_knxprod(self, path: Path) -> list[str]:
        """Import a .knxprod file; returns newly added product refs."""
        return self._import_knxprod_bytes(path.read_bytes())

    def import_project_products(self, path: Path) -> list[str]:
        """Ingest the product data embedded in a ``.knxproj`` (which omits ``Catalog.xml``), so the
        imported project's devices resolve their applications. Returns newly added product refs."""
        return self._import_knxprod_bytes(path.read_bytes(), require_catalog=False)

    def import_product_source(self, data: bytes, master_xml: bytes) -> list[str]:
        """Ingest product data from any supported source: a ``.knxprod``, an OpenKNX release ZIP, or
        a raw monolithic KNX product XML (OpenKNXproducer output). ``master_xml`` supplies the
        ``knx_master.xml`` the latter two do not embed. Returns the product refs newly added."""
        from xknxeditor.prod import knxprod_from_source

        return self._import_knxprod_bytes(knxprod_from_source(data, master_xml))

    def _import_knxprod_bytes(
        self, data: bytes, *, require_catalog: bool = True
    ) -> list[str]:
        before = {p.product_ref_id for p in self.get_products()}
        self._service.import_knxprod(data, require_catalog=require_catalog)
        self._products = None
        after = {p.product_ref_id for p in self.get_products()}
        return sorted(after - before)

    def online_catalog_items(self, manufacturer_id: int) -> list[OnlineCatalogItem]:
        """Downloadable products a manufacturer offers in the online catalog (hits the network)."""
        return self._online_client.catalog_items(manufacturer_id)

    def online_index(self) -> dict[int, list[OnlineCatalogItem]] | None:
        """The full cached product index (all manufacturers), or None if never built. No network."""
        return self._online_client.cached_index()

    def online_index_status(self) -> OnlineIndexStatus:
        """How many manufacturers/products are in the cached index, and when it was built."""
        return self._online_client.index_status()

    def build_online_index(
        self,
        progress_cb: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        *,
        force: bool = False,
    ) -> OnlineIndexStatus:
        """Build/refresh the full product index (hits the network, ~hundreds of MB). Resumable:
        skips manufacturers already cached unless ``force``. Meant to run on a worker thread."""
        return self._online_client.refresh_index(
            progress_cb, should_cancel, force=force
        )

    def search_online_products(
        self, query: str, limit: int = 300
    ) -> tuple[list[OnlineCatalogItem], int]:
        """Flat cross-manufacturer product search over the cached index; (results, total)."""
        from editor_gui.plugins.catalog.online_catalog import search_index

        index = self._online_client.cached_index()
        if not index:
            return [], 0
        return search_index(index, query, limit)

    def online_products_for_order(self, order_number: str) -> list[OnlineCatalogItem]:
        """Cached online catalog entries whose order number matches ``order_number`` (case/space
        insensitive). Used by the editor to show which products/versions exist online. No network."""
        index = self._online_client.cached_index()
        if not index or not order_number.strip():
            return []
        key = order_number.strip().lower()
        return [
            item
            for items in index.values()
            for item in items
            if item.order_number.strip().lower() == key
        ]

    def download_online_products(
        self, catalog_item_ids: list[str], language_ids: list[str] | None = None
    ) -> list[str]:
        """Download a ``.knxprod`` for the given catalog items and import it; returns new refs.

        Defaults to the selected country/language (:attr:`online_language`)."""
        data = self._online_client.download_product(
            catalog_item_ids, language_ids or [self._language]
        )
        return self._import_knxprod_bytes(data)

    @io_guarded(lambda: None)
    def get_program_application_id(self, program_id: str) -> str | None:
        """The application id a hardware program loads, whether or not a catalog item lists it."""
        return self._service.get_program_application_id(program_id)

    @io_guarded(lambda: "")
    def program_resolution_hint(self, program_ref: str) -> str:
        """A one-line diagnostic (for logs) on why a device program did not resolve: whether its
        manufacturer's product data is in the catalog and how many programs it holds - telling
        'not ingested' apart from an id mismatch."""
        mid = program_ref.split("_", 1)[0]
        present, count = self._service.manufacturer_catalog_status(mid)
        if not present:
            return f"manufacturer {mid} not in catalog (product data not ingested)"
        return (
            f"manufacturer {mid} present with {count} hardware program(s), "
            "none matching this ref (id mismatch)"
        )

    @io_guarded(lambda: None)
    def get_application(self, application_id: str) -> Application | None:
        # Localize device labels (parameter/tab/block names) to the current UI language when the
        # .knxprod ships that language; get_locale() returns e.g. "de", matched to "de-DE".
        from editor_gui.strings import get_locale

        return self._service.get_application(application_id, get_locale())

    @io_guarded(list)
    def find_products_for_application(
        self,
        *,
        manufacturer_id: str,
        application_number: int,
        application_version: int | None = None,
        mask_version: str | None = None,
    ) -> list[ProductSummary]:
        """Catalog products matching an application id read off a device (for recover)."""
        return self._service.find_products_for_application(
            manufacturer_id=manufacturer_id,
            application_number=application_number,
            application_version=application_version,
            mask_version=mask_version,
        )

    @io_guarded(lambda: False)
    def requires_download_plugin(self, hardware2program_id: str) -> bool:
        """Whether a product's hardware may only be programmed with the manufacturer's plugin."""
        hardware_id = hardware2program_id.split("_HP-", 1)[0]
        hardware = self._service.get_hardware(hardware_id)
        return bool(hardware is not None and hardware.no_download_without_plugin)

    @io_guarded(lambda: None)
    def get_program_source(self, program_id: str) -> tuple[str, str] | None:
        """Return ``(knxprod_path, manufacturer_id)`` for a hardware program id, or ``None``."""
        return self._service.get_program_source(program_id)

    def refresh(self) -> None:
        self._products = None

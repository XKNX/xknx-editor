"""GUI adapter: caching, path-based import, online catalog, and new-product reporting."""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from editor_gui.concurrency import io_guarded
from editor_gui.languages import product_language, product_text_languages
from editor_gui.plugins.catalog.filters import OnlineFilters
from xknxeditor.prod import Application

# The settings file the download language was stored in (see _forget_stored_language).
_CATALOG_SETTINGS = "catalog"

if TYPE_CHECKING:
    from collections.abc import Callable

    from editor_gui.plugins.catalog.online_catalog import (
        OnlineCatalogItem,
        OnlineIndexStatus,
        OnlineManufacturer,
    )
    from xknxeditor.catalog import (
        ProductDeletion,
        ProductFacts,
        ProductSummary,
        ProgramSummary,
    )
    from xknxeditor.catalog.core.hardware import FactoryData
    from xknxeditor.namespaces.intermediate.application_program_t import (
        ApplicationProgram,
    )


def _forget_stored_language() -> None:
    """Remove the download language earlier versions let the user choose and stored: the
    editor's language decides it now. Its file goes when nothing else is in it."""
    from editor_gui.settings import config_dir, load_settings, save_settings

    data = load_settings(_CATALOG_SETTINGS)
    if "language" not in data:
        return
    del data["language"]
    if data:
        save_settings(_CATALOG_SETTINGS, data)
        return
    with contextlib.suppress(OSError):
        (config_dir() / f"{_CATALOG_SETTINGS}.json").unlink()


class CatalogService:
    def __init__(
        self,
        catalog_path: Path,
        io_lock: threading.RLock | None = None,
        parse_cache_dir: Path | None = None,
    ) -> None:
        from editor_gui.plugins.catalog.online_catalog import OnlineCatalogClient
        from xknxeditor.catalog import CatalogService as _CatalogService

        self._service = _CatalogService(catalog_path, parse_cache_dir)
        self._products: list[ProductSummary] | None = None
        self._product_index: dict[str, ProductSummary] | None = None
        self._programs: dict[str, list[ProgramSummary]] = {}
        self._replaces: dict[str, list[int]] = {}
        # Factory images (``LoadedImage`` and ``CheckSums``) by hardware program id, read once.
        self._factory_images: dict[str, FactoryData | None] = {}
        # Shared with the project service so a background import can hold both while it writes.
        self._io_lock = io_lock or threading.RLock()
        # Manufacturer list from the KNX online catalog service (cached next to the db), named from
        # the app's own master data.
        from editor_gui.master_data import master_xml_bytes

        self._online_client = OnlineCatalogClient(
            catalog_path.parent, master_xml=master_xml_bytes
        )
        # A download language chosen for this session (over MCP), else the editor's languages.
        self._language: str | None = None
        # The online products last listed per manufacturer, for their text languages on download.
        self._browsed: dict[int, list[OnlineCatalogItem]] = {}
        _forget_stored_language()
        # The market and product language the online catalog shows products for.
        self.online_filters = OnlineFilters(self._online_client)

    @property
    def online_languages(self) -> list[str]:
        """The languages products are downloaded in (KNX language ids such as ``de-DE``): the
        preferred product language first, then the editor's languages."""
        from editor_gui.plugins.catalog.online_catalog import download_languages
        from editor_gui.strings import get_locale

        if self._language:
            return [self._language]
        # The preferred product language first, then the editor's languages.
        first = product_language()
        return [first, *(c for c in download_languages(get_locale()) if c != first)]

    @property
    def online_language(self) -> str:
        """The first download language (KNX language id, e.g. ``de-DE``)."""
        return self.online_languages[0]

    def set_online_language(self, code: str) -> None:
        """Download in ``code`` only, for the rest of the session."""
        self._language = code

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

    def find_product(self, product_ref_id: str) -> ProductSummary | None:
        """The catalog product ``product_ref_id`` (any of its versions), or ``None``."""
        if self._product_index is None:
            products = self.get_products()
            if self._products is None:
                return None  # the catalog is busy; try again next time
            index: dict[str, ProductSummary] = {}
            for product in products:
                index.setdefault(product.product_ref_id, product)
            self._product_index = index
        return self._product_index.get(product_ref_id)

    def import_knxprod(self, path: Path) -> list[ProductSummary]:
        """Import a .knxprod file; returns the newly added product versions."""
        return self._import_knxprod_bytes(path.read_bytes())

    def import_project_products(self, path: Path) -> list[ProductSummary]:
        """Ingest the product data embedded in a ``.knxproj`` (which omits ``Catalog.xml``), so the
        imported project's devices resolve their applications. Returns the newly added product
        versions."""
        return self._import_knxprod_bytes(path.read_bytes(), require_catalog=False)

    def import_product_source(
        self, data: bytes, master_xml: bytes
    ) -> list[ProductSummary]:
        """Ingest product data from any supported source: a ``.knxprod``, an OpenKNX release ZIP, or
        a raw monolithic KNX product XML (OpenKNXproducer output). ``master_xml`` supplies the
        ``knx_master.xml`` the latter two do not embed. Returns the newly added product versions."""
        from xknxeditor.prod import knxprod_from_source

        return self._import_knxprod_bytes(knxprod_from_source(data, master_xml))

    def import_knxprod_contents(self, path: Path) -> list[ProductSummary]:
        """Import a ``.knxprod`` and return every product version it provides, new or already in the
        catalog. Unlike :meth:`import_knxprod` (which returns only the newly added versions), this
        lets a re-import still list the file's products instead of dead-ending at "nothing new"."""
        with self._io_lock:
            stored = self._service.import_knxprod(path.read_bytes())
            self._forget_products()
            return self._service.products_for_path(str(stored))

    def import_product_source_contents(
        self, data: bytes, master_xml: bytes
    ) -> list[ProductSummary]:
        """Like :meth:`import_product_source`, but returns every product version the source provides
        (new or already present), so a re-import still lists the file's products."""
        from xknxeditor.prod import knxprod_from_source

        with self._io_lock:
            stored = self._service.import_knxprod(knxprod_from_source(data, master_xml))
            self._forget_products()
            return self._service.products_for_path(str(stored))

    def _import_knxprod_bytes(
        self, data: bytes, *, require_catalog: bool = True
    ) -> list[ProductSummary]:
        """Each product version (product and hardware program) new in the catalog."""
        before = {
            (p.product_ref_id, p.hardware2program_ref_id) for p in self.get_products()
        }
        self._service.import_knxprod(data, require_catalog=require_catalog)
        self._forget_products()
        added: dict[tuple[str, str], ProductSummary] = {}
        for product in self.get_products():
            key = (product.product_ref_id, product.hardware2program_ref_id)
            if key not in before:
                added.setdefault(key, product)
        return [added[key] for key in sorted(added)]

    @io_guarded(lambda: None)
    def plan_delete_product(
        self, product_ref_id: str, hardware_program_id: str | None = None
    ) -> ProductDeletion | None:
        """What deleting ``product_ref_id`` would remove, without changing anything."""
        return self._service.plan_product_deletion(product_ref_id, hardware_program_id)

    def delete_product(
        self, product_ref_id: str, hardware_program_id: str | None = None
    ) -> ProductDeletion | None:
        """Delete a product from the local catalog, holding :attr:`io_lock` while it writes."""
        with self._io_lock:
            deletion = self._service.delete_product(product_ref_id, hardware_program_id)
            self._forget_products()
        return deletion

    def online_catalog_items(self, manufacturer_id: int) -> list[OnlineCatalogItem]:
        """Downloadable products a manufacturer offers in the online catalog (hits the network)."""
        items = self._online_client.catalog_items(manufacturer_id)
        self._browsed[manufacturer_id] = items
        return items

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
        """Flat cross-manufacturer product search over the cached index, of the products the
        market and language filters show; (results, total)."""
        from editor_gui.plugins.catalog.online_catalog import search_index

        index = self._online_client.cached_index()
        if not index:
            return [], 0
        return search_index(index, query, limit, self.online_filters.predicate())

    def download_online_products(
        self, catalog_item_ids: list[str], language_ids: list[str] | None = None
    ) -> list[ProductSummary]:
        """Download a ``.knxprod`` for the given catalog items and import it; returns the new
        product versions.

        Defaults to the editor's languages (:attr:`online_languages`), with the variants of them
        the products offer (a manufacturer may label its texts "de" or "es-MX")."""
        from editor_gui.plugins.catalog.online_catalog import with_variants

        if language_ids is None:
            language_ids = with_variants(
                self.online_languages, self._offered_languages(catalog_item_ids)
            )
        data = self._online_client.download_product(catalog_item_ids, language_ids)
        return self._import_knxprod_bytes(data)

    def _offered_languages(self, catalog_item_ids: list[str]) -> list[str]:
        """The text languages of the given catalog items, as the cached index or the products
        listed while browsing give them."""
        wanted = set(catalog_item_ids)
        offered: list[str] = []
        listed = [
            *(self._online_client.cached_index() or {}).values(),
            *self._browsed.values(),
        ]
        for items in listed:
            for item in items:
                if item.id in wanted:
                    offered += [c for c in item.languages if c not in offered]
        return offered

    @io_guarded(list)
    def hardware_programs(self, hardware_id: str) -> list[ProgramSummary]:
        """Every program of a hardware with the application it loads (cached)."""
        programs = self._programs.get(hardware_id)
        if programs is None:
            programs = self._service.list_hardware_programs(hardware_id)
            self._programs[hardware_id] = programs
        return programs

    @io_guarded(list)
    def replaces_versions(self, program_id: str) -> list[int]:
        """The application versions the program's application can update (cached)."""
        versions = self._replaces.get(program_id)
        if versions is None:
            versions = self._service.get_replaces_versions(program_id)
            self._replaces[program_id] = versions
        return versions

    @io_guarded(lambda: None)
    def get_program_application_id(self, program_id: str) -> str | None:
        """The application id a hardware program loads, whether or not a catalog item lists it."""
        return self._service.get_program_application_id(program_id)

    @io_guarded(lambda: False)
    def is_program_without_application(self, program_id: str) -> bool:
        """Whether a known hardware program loads no application (e.g. a power supply's)."""
        return self._service.is_program_without_application(program_id)

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
    def product_facts(self, hardware2program_ref_id: str) -> ProductFacts | None:
        """Mask version, KNX Secure flag, registration number/date and media of a product version
        (by its ``hardware2program_ref_id``); ``None`` while a background import holds the lock."""
        return self._service.product_facts(hardware2program_ref_id)

    @io_guarded(lambda: None)
    def get_application(self, application_id: str) -> Application | None:
        # Localize device labels (parameter/tab/block names) to the preferred product language,
        # else the UI language, when the .knxprod ships it.
        return self._service.get_application(application_id, product_text_languages())

    @io_guarded(lambda: None)
    def application_source(self, application_id: str) -> str | None:
        """What ``application_id`` is read from, in the current language; ``None`` when it is not
        in the catalog."""
        source = self._service.application_source(application_id)
        return None if source is None else f"{source}|{product_text_languages()}"

    def unloaded_application(
        self, application_id: str, name: str, version: str, manufacturer_id: str
    ) -> Application:
        """``application_id`` known by its name and versions, parsed in the current language when
        first used (on any thread)."""
        language = product_text_languages()

        def load() -> ApplicationProgram:
            return self._service.load_application_program(application_id, language)

        return Application.unloaded(
            application_id, name, version, manufacturer_id, load
        )

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

    def factory_image(self, hardware2program_id: str) -> FactoryData | None:
        """The image devices of a hardware program leave the factory with (its
        ``LoadedImage``) and its stamp, or ``None`` when the product has no image. Not skipped while an import
        runs: a download must not go without it."""
        if hardware2program_id not in self._factory_images:
            self._factory_images[hardware2program_id] = self._service.get_factory_image(
                hardware2program_id
            )
        return self._factory_images[hardware2program_id]

    @io_guarded(lambda: False)
    def hardware_is_ip_enabled(self, hardware2program_id: str) -> bool:
        """Whether a product's hardware is an IP device (``IsIPEnabled``)."""
        hardware_id = hardware2program_id.split("_HP-", 1)[0]
        hardware = self._service.get_hardware(hardware_id)
        return bool(hardware is not None and hardware.is_ip_enabled)

    @io_guarded(lambda: None)
    def ip_config(self, hardware2program_id: str) -> str | None:
        """Who sets the IP configuration of a product's application: "Tool" or "Custom"
        (``IPConfig``); ``None`` when the catalog lacks it."""
        return self._service.get_ip_config(hardware2program_id)

    @io_guarded(lambda: set[str]())
    def coupler_capabilities(self, hardware2program_id: str) -> set[str]:
        """The coupler capabilities of a hardware program (e.g. ``SecurityProxy``)."""
        return self._service.coupler_capabilities(hardware2program_id)

    @io_guarded(lambda: None)
    def program_media(self, hardware2program_id: str) -> tuple[set[str], bool] | None:
        """The media a hardware program supports and whether it is a coupler; None when the
        catalog lacks the program."""
        return self._service.program_media(hardware2program_id)

    @io_guarded(dict)
    def program_roles(
        self, hardware2program_ids: list[str]
    ) -> dict[str, tuple[bool, str]]:
        """Per hardware program the catalog has: whether its hardware is a coupler, and the
        mask version of its application."""
        return self._service.program_roles(hardware2program_ids)

    @io_guarded(dict)
    def program_power(
        self, hardware2program_ids: list[str]
    ) -> dict[str, tuple[float | None, bool, bool]]:
        """Per hardware program the catalog has: its hardware's bus current (mA, None for
        none), whether it is a power supply, and whether it is a coupler."""
        return self._service.program_power(hardware2program_ids)

    @io_guarded(lambda: False)
    def has_application(self, application_id: str) -> bool:
        """Whether a hardware program in the catalog loads ``application_id``."""
        return self._service.has_application(application_id)

    @io_guarded(lambda: None)
    def get_program_source(self, program_id: str) -> tuple[str, str] | None:
        """Return ``(knxprod_path, manufacturer_id)`` for a hardware program id, or ``None``."""
        return self._service.get_program_source(program_id)

    def refresh(self) -> None:
        self._forget_products()

    def _forget_products(self) -> None:
        self._products = None
        self._product_index = None
        self._programs = {}
        self._replaces = {}

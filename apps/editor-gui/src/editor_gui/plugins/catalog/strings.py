from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("catalog", _locale_dir)


class CatalogStrings:
    @property
    def PANEL_CATALOG(self) -> str:
        return _("Catalog")

    @property
    def ARCHIVE_FAILED_TO_LOAD(self) -> str:
        return _("Failed to load archive")

    @property
    def ARCHIVE_LOADED(self) -> str:
        return _("Loaded: {path}")

    @property
    def ARCHIVE_FOUND_APPS(self) -> str:
        return _("Found {count} application(s)")

    @property
    def ARCHIVE_COM_OBJECTS(self) -> str:
        return _("({count} com objects)")

    @property
    def BTN_ONLINE_CATALOG(self) -> str:
        return _("Fetch Online Catalog")

    @property
    def ONLINE_LOADING(self) -> str:
        return _("Loading manufacturer list...")

    @property
    def ONLINE_FAILED(self) -> str:
        return _("Online catalog not reachable")

    @property
    def ONLINE_COUNT(self) -> str:
        return _("{count} manufacturers")

    @property
    def ONLINE_MATCH_COUNT(self) -> str:
        return _("{count} matches")

    @property
    def ONLINE_LOADING_PRODUCTS(self) -> str:
        return _("Loading products...")

    @property
    def ONLINE_NO_PRODUCTS(self) -> str:
        return _("No downloadable products")

    @property
    def ONLINE_DOWNLOADING(self) -> str:
        return _("Downloading {name}...")

    @property
    def ONLINE_NOT_DOWNLOADABLE(self) -> str:
        return _("(plugin required)")

    @property
    def ONLINE_ALREADY_IMPORTED(self) -> str:
        return _("(in local catalog)")

    @property
    def ONLINE_SEARCH_HINT(self) -> str:
        return _("Search products, order number...")

    @property
    def ONLINE_COUNTRY_LABEL(self) -> str:
        return _("Country")

    @property
    def ONLINE_INDEX_STATUS(self) -> str:
        return _("Product index: {mfrs} manufacturers, {products} products")

    @property
    def ONLINE_INDEX_NONE(self) -> str:
        return _("No product index yet - fetch the online catalog to build it")

    @property
    def ONLINE_INDEX_BUILDING(self) -> str:
        return _("Building product index... {done}/{total} manufacturers")

    @property
    def ONLINE_INDEX_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def ONLINE_SEARCH_BUILD_HINT(self) -> str:
        return _("Fetch the online catalog to search across all manufacturers.")

    @property
    def ONLINE_FLAT_CAPPED(self) -> str:
        return _("Showing {shown} of {total} matches - refine the search")

    @property
    def CATALOG_DETAIL_TITLE(self) -> str:
        return _("Product Details")

    @property
    def CATALOG_DETAIL_NAME(self) -> str:
        return _("Name")

    @property
    def CATALOG_DETAIL_ORDER(self) -> str:
        return _("Order Number")

    @property
    def CATALOG_DETAIL_MANUFACTURER(self) -> str:
        return _("Manufacturer")

    @property
    def CATALOG_DETAIL_APPLICATION(self) -> str:
        return _("Application")

    @property
    def CATALOG_DETAIL_VERSION(self) -> str:
        return _("Program Version")

    @property
    def CATALOG_DETAIL_PRODUCT_REF(self) -> str:
        return _("Product Ref")

    @property
    def CATALOG_DETAIL_ADD(self) -> str:
        return _("Add Device")

    @property
    def CATALOG_ADD_SPACE_LABEL(self) -> str:
        return _("Room")

    @property
    def CATALOG_ADD_SPACE_NONE(self) -> str:
        return _("(no room)")

    @property
    def CATALOG_ADD_NEEDS_PROJECT(self) -> str:
        return _("Open or create a project first to add a device.")

    @property
    def CATALOG_ADD_NO_APP(self) -> str:
        return _("This product has no usable application program.")

    @property
    def CATALOG_ADD_OK(self) -> str:
        return _("Added device: {name}")

    @property
    def TAB_LOCAL(self) -> str:
        return _("Local")

    @property
    def TAB_ONLINE(self) -> str:
        return _("Online")

    @property
    def LOCAL_EMPTY(self) -> str:
        return _("No products imported yet — import a .knxprod or use the Online tab.")

    @property
    def MISSING_APPS_FETCH(self) -> str:
        return _("Fetch {count} Missing Application(s) Online")

    @property
    def MISSING_APPS_FETCHING(self) -> str:
        return _("Fetching missing applications…")


S = CatalogStrings()

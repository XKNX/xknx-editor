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
        return _("({count} group objects)")

    @property
    def BTN_ONLINE_CATALOG(self) -> str:
        return _("Fetch Online Catalog")

    @property
    def ONLINE_LOADING(self) -> str:
        return _("Loading manufacturer list…")

    @property
    def ONLINE_FAILED(self) -> str:
        return _("Online catalog not reachable")

    @property
    def ONLINE_MATCH_COUNT(self) -> str:
        return _("{count} matches")

    @property
    def ONLINE_LOADING_PRODUCTS(self) -> str:
        return _("Loading products…")

    @property
    def ONLINE_NO_PRODUCTS(self) -> str:
        return _("No downloadable products")

    @property
    def ONLINE_DOWNLOADING(self) -> str:
        return _("Downloading {name}…")

    @property
    def ONLINE_NOT_DOWNLOADABLE(self) -> str:
        return _("(plugin required)")

    @property
    def ONLINE_ALREADY_IMPORTED(self) -> str:
        return _("(in local catalog)")

    @property
    def ONLINE_SEARCH_HINT(self) -> str:
        return _("Search products, order number…")

    @property
    def ONLINE_INDEX_STATUS(self) -> str:
        return _("Product Index ({date}): {mfrs} manufacturers, {products} products")

    @property
    def ONLINE_INDEX_NONE(self) -> str:
        return _("No Product Index yet - fetch the online catalog to build it")

    @property
    def ONLINE_INDEX_BUILDING(self) -> str:
        return _("Building Product Index… {done}/{total} manufacturers")

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
    def CATALOG_DETAIL_APPLICATION_ID(self) -> str:
        return _("Application ID")

    @property
    def CATALOG_DETAIL_RAIL(self) -> str:
        return _("DIN Rail Mounting")

    @property
    def CATALOG_DETAIL_BUS_CURRENT(self) -> str:
        return _("Bus Current")

    @property
    def CATALOG_YES(self) -> str:
        return _("Yes")

    @property
    def CATALOG_NO(self) -> str:
        return _("No")

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
    def CATALOG_ADD_SPACE_TITLE(self) -> str:
        return _("Add Device to Project")

    @property
    def CATALOG_ADD_SPACE_PROMPT(self) -> str:
        return _("Which room should {name} go into?")

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
    def CATALOG_ADD_ONLINE_TITLE(self) -> str:
        return _("Add to project?")

    @property
    def CATALOG_ADD_ONLINE_PROMPT(self) -> str:
        return _("“{name}” was added to the local catalog. Add it to the project now?")

    @property
    def CATALOG_ADD_ONLINE_YES(self) -> str:
        return _("Add to Project")

    @property
    def CATALOG_ADD_ONLINE_NO(self) -> str:
        return _("Not Now")

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

    @property
    def CONTEXT_DELETE(self) -> str:
        return _("Delete")

    @property
    def DELETE_TITLE(self) -> str:
        return _("Delete from Local Catalog")

    @property
    def DELETE_CONFIRM(self) -> str:
        return _(
            "Are you sure you want to delete the selected device? This cannot be undone."
        )

    @property
    def DELETE_AFFECTED(self) -> str:
        return _(
            "{count} device(s) in the open project use this product. They can't be "
            "loaded until the product is imported again."
        )

    @property
    def BTN_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def DELETING(self) -> str:
        return _("Deleting {name}…")

    @property
    def DELETE_OK(self) -> str:
        return _("Deleted from the local catalog: {name}")

    @property
    def CONTEXT_EXPORT_KNXPROD(self) -> str:
        return _("Export .knxprod…")

    @property
    def EXPORT_KNXPROD_TITLE(self) -> str:
        return _("Export .knxprod")

    @property
    def EXPORT_KNXPROD_FILTER(self) -> str:
        return _("KNX product (*.knxprod)")

    @property
    def EXPORT_KNXPROD_OK(self) -> str:
        return _("Exported {name}")

    @property
    def EXPORT_KNXPROD_FAILED(self) -> str:
        return _("Could not export {name}: {error}")

    @property
    def EXPORT_KNXPROD_UNAVAILABLE(self) -> str:
        return _("No stored archive for {name}")

    @property
    def CATALOG_ADD_SECURE_PASSWORD(self) -> str:
        return _(
            "This device supports KNX Data Secure. Set a project password to commission it securely; without one it is added and commissioned in the clear."
        )

    @property
    def LOCAL_SEARCH_HINT(self) -> str:
        return _("Search…")


S = CatalogStrings()

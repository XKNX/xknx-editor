from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.catalog.online_catalog import (
    OnlineCatalogError,
)
from editor_gui.plugins.catalog.strings import S
from editor_gui.plugins.catalog.ui import CatalogPanel
from editor_gui.plugins.project.service import DeviceProduct

if TYPE_CHECKING:
    from editor_gui.plugins.catalog.online_catalog import OnlineCatalogItem
    from xknxeditor.catalog import ProductSummary
    from xknxeditor.prod import Application


class CatalogPlugin:
    name = "catalog"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._log = Logger(api.log, "catalog")
        self._panel = CatalogPanel(
            get_products=api.catalog.get_products,
            on_select=self._on_select,
            get_spaces=self._space_options,
            get_online_manufacturers=api.catalog.online_manufacturers,
            on_online_refresh=self._refresh_online,
            fetch_online_items=self._fetch_online_items,
            download_online_item=self._download_online_item,
            get_missing_app_count=self._missing_app_count,
            fetch_missing_apps=self._fetch_missing_apps,
            build_online_index=self._build_online_index,
            get_online_index_status=api.catalog.online_index_status,
            search_online_products=api.catalog.search_online_products,
            online_filters=api.catalog.online_filters,
            add_online_item=self._add_online_item_to_project,
            can_add_to_project=lambda: api.project.is_open,
            plan_delete_product=self._plan_delete_product,
            delete_product=self._delete_product,
            export_product=self._export_product,
        )
        self._panels = [
            PanelDefinition(
                name="catalog",
                label=S.PANEL_CATALOG,
                dock="LeftSpace",
                render=self._panel.render,
            ),
        ]

    def _notify(self, text: str) -> None:
        if self._api.notify is not None:
            self._api.notify(text)

    def _on_select(self, product: ProductSummary, space_id: int | None = None) -> None:
        # "Add device" needs a project to add into; without one add_device is a silent no-op, so give
        # explicit feedback instead of the button appearing to do nothing.
        if not self._api.project.is_open:
            self._log.warning(
                "cannot add device: no project open", product=product.product_ref_id
            )
            self._notify(S.CATALOG_ADD_NEEDS_PROJECT)
            return
        if product.application_id is None:
            self._log.warning(
                "product has no application", product=product.product_ref_id
            )
            self._notify(S.CATALOG_ADD_NO_APP)
            return
        app = self._api.catalog.get_application(product.application_id)
        if app is None:
            self._log.warning(
                "application not found", application_id=product.application_id
            )
            self._notify(S.CATALOG_ADD_NO_APP)
            return

        # A device that supports KNX Data Secure is commissioned securely by default, which needs
        # a project password: ask for one first; without it the device is added in the clear.
        request = self._api.request_project_password
        if (
            app.program.is_secure_enabled
            and request is not None
            and self._api.project.project_password is None
        ):
            request(
                S.CATALOG_ADD_SECURE_PASSWORD,
                lambda: self._add(product, app, space_id, secure_commissioning=True),
                lambda: self._add(product, app, space_id, secure_commissioning=False),
            )
            return
        self._add(product, app, space_id)

    def _add(
        self,
        product: ProductSummary,
        app: Application,
        space_id: int | None,
        *,
        secure_commissioning: bool | None = None,
    ) -> None:
        # A new device has no name of its own; it shows its product's name until renamed.
        device_id = self._api.project.add_device(
            product_ref_id=product.product_ref_id,
            hardware2program_ref_id=product.hardware2program_ref_id,
            name="",
            app=app,
            product=DeviceProduct.of(product),
            secure_commissioning=secure_commissioning,
        )
        if device_id:
            if space_id is not None:
                self._api.project.set_device_space(device_id, space_id)
            ask = self._api.ask_device_certificate
            if ask is not None and self._api.project.secure_commissioning_of(device_id):
                ask(device_id)
            label = product.name or app.name
            self._log.info("device added", name=label, id=device_id)
            self._notify(S.CATALOG_ADD_OK.format(name=label))

    def _plan_delete_product(self, product: ProductSummary) -> int | None:
        """Number of open-project devices that deleting ``product`` leaves unloadable, or ``None``
        when the product is not in the local catalog."""
        deletion = self._api.catalog.plan_delete_product(
            product.product_ref_id, product.hardware2program_ref_id
        )
        if deletion is None:
            return None
        return self._api.project.count_devices_losing_product(deletion)

    def _delete_product(self, product: ProductSummary) -> None:
        """Delete a product from the local catalog (called on a worker thread)."""
        ref = product.product_ref_id
        try:
            # Hold the shared lock across the catalog delete and the device rebuild, so per-frame
            # reads bail instead of seeing the catalog and the project out of step.
            with self._api.catalog.io_lock:
                deletion = self._api.catalog.delete_product(
                    ref, product.hardware2program_ref_id
                )
                if deletion is not None:
                    self._api.project.forget_deleted_product(deletion, rebuild=True)
        except Exception as exc:
            self._log.error(
                "catalog delete failed",
                product=ref,
                error=f"{type(exc).__name__}: {exc}",
            )
            return
        if deletion is None:
            self._log.warning("product not in local catalog", product=ref)
            return
        self._log.info(
            "product deleted from local catalog",
            product=ref,
            programs=len(deletion.removed_programs),
            applications=len(deletion.removed_applications),
            files=len(deletion.removed_files),
        )
        self._notify(S.DELETE_OK.format(name=product.name or ref))

    def _export_product(self, product: ProductSummary, dest: str) -> None:
        """Copy a local product's original ``.knxprod`` archive to ``dest`` (it is kept verbatim on
        disk). The archive is the one that was imported, so a manufacturer bundle exports whole."""
        import shutil

        name = product.name or product.product_ref_id
        source = self._api.catalog.get_program_source(product.hardware2program_ref_id)
        if source is None:
            self._log.warning("no stored archive for product", product=name)
            self._notify(S.EXPORT_KNXPROD_UNAVAILABLE.format(name=name))
            return
        knxprod_path, _manufacturer = source
        try:
            shutil.copyfile(knxprod_path, dest)
        except OSError as exc:
            self._log.error(
                "knxprod export failed",
                product=name,
                error=f"{type(exc).__name__}: {exc}",
            )
            self._notify(S.EXPORT_KNXPROD_FAILED.format(name=name, error=str(exc)))
            return
        self._log.info("exported knxprod", product=name, dest=dest)
        self._notify(S.EXPORT_KNXPROD_OK.format(name=name))

    def _space_options(self) -> list[tuple[int, str]]:
        """Flattened (space_id, "Building / Floor / Room") pairs for the Add-device room picker,
        in tree order. Empty when no project is open or the project has no spaces."""
        if not self._api.project.is_open:
            return []
        out: list[tuple[int, str]] = []

        def walk(spaces: list[Any], trail: list[str]) -> None:
            for space in spaces:
                here = [*trail, space.name or space.space_type or "?"]
                out.append((space.id, " / ".join(here)))
                walk(space.children, here)

        walk(self._api.project.get_space_tree(), [])
        return out

    def _refresh_online(self) -> None:
        """Fetch the online manufacturer list (called on a worker thread)."""
        try:
            manufacturers = self._api.catalog.refresh_online_manufacturers()
        except OnlineCatalogError as exc:
            self._log.error("online catalog refresh failed", error=str(exc))
            raise
        self._log.info("online catalog manufacturers loaded", count=len(manufacturers))

    def _fetch_online_items(self, manufacturer_id: int) -> list[OnlineCatalogItem]:
        """Fetch a manufacturer's downloadable products (called on a worker thread)."""
        try:
            items = self._api.catalog.online_catalog_items(manufacturer_id)
        except OnlineCatalogError as exc:
            self._log.error(
                "online catalog index failed",
                manufacturer=manufacturer_id,
                error=str(exc),
            )
            raise
        self._log.info(
            "online catalog products loaded",
            manufacturer=manufacturer_id,
            count=len(items),
        )
        return items

    def _download_online_item(self, item_id: str) -> None:
        """Download a single product's ``.knxprod`` and import it (called on a worker thread)."""
        try:
            added = self._api.catalog.download_online_products([item_id])
        except OnlineCatalogError as exc:
            self._log.error(
                "online product download failed", item=item_id, error=str(exc)
            )
            raise
        self._log.info("online product imported", item=item_id, added=len(added))

    def _add_online_item_to_project(self, item_id: str) -> None:
        """Add a just-imported online product to the open project (confirmed via the panel alert).

        The online item id carries the product's ``hardware2program_ref_id`` as a prefix, so the
        matching freshly-imported local product is the longest such prefix match. Reuses the normal
        local add path (``_on_select``) so catalog resolution/export behave identically."""
        if not self._api.project.is_open:
            self._notify(S.CATALOG_ADD_NEEDS_PROJECT)
            return
        matches = [
            p
            for p in self._api.catalog.get_products()
            if p.hardware2program_ref_id
            and item_id.startswith(p.hardware2program_ref_id)
        ]
        if not matches:
            self._log.warning("imported product not found for online add", item=item_id)
            self._notify(S.CATALOG_ADD_NO_APP)
            return
        product = max(matches, key=lambda p: len(p.hardware2program_ref_id))
        self._on_select(product)

    def _build_online_index(
        self,
        progress_cb: Callable[[int, int], None],
        should_cancel: Callable[[], bool],
    ) -> None:
        """Build/refresh the full online product index, logging start and result so the operation
        is visible in the Log. Called on a worker thread after the manufacturer list is fetched."""
        self._log.info("online product index build started")
        try:
            status = self._api.catalog.build_online_index(progress_cb, should_cancel)
        except OnlineCatalogError as exc:
            self._log.error("online product index build failed", error=str(exc))
            raise
        self._log.info(
            "online product index built",
            manufacturers=status.manufacturers,
            products=status.products,
        )

    def _missing_app_count(self) -> int:
        return len(self._api.project.missing_program_refs())

    def _fetch_missing_apps(self) -> None:
        """Download+import online-catalog products for project devices whose application is missing.

        A device's ``hardware2program_ref_id`` (``M-0002_H-…_HP-…``) is a prefix of the matching
        online catalog item id, so each missing ref maps to exactly one product. Called on a worker
        thread (network + import)."""
        refs = self._api.project.missing_program_refs()
        by_mfr: dict[int, list[str]] = {}
        for ref in refs:
            try:
                mid = int(
                    ref[2:6], 16
                )  # "M-0083_…" -> 0x0083 -> 131 (hex, not decimal)
            except ValueError:
                continue
            by_mfr.setdefault(mid, []).append(ref)
        self._log.debug(
            "fetch missing apps: resolving refs online",
            refs=len(refs),
            manufacturers=len(by_mfr),
        )
        item_ids: list[str] = []
        for mid, mrefs in by_mfr.items():
            try:
                items = self._api.catalog.online_catalog_items(mid)
            except OnlineCatalogError as exc:
                self._log.error("online index failed", manufacturer=mid, error=str(exc))
                continue
            for ref in mrefs:
                matches = [it for it in items if it.id.startswith(ref)]
                downloadable = next((it for it in matches if it.downloadable), None)
                if downloadable is not None:
                    item_ids.append(downloadable.id)
                elif matches:
                    # Found online but download-gated (plugin / external software / incomplete /
                    # in no market). Attempting the download anyway returns HTTP 400 (issue #51), so
                    # skip it and report the specific reason the index gave.
                    reason = next(
                        (
                            it.download_block_reason
                            for it in matches
                            if it.download_block_reason
                        ),
                        "server policy",
                    )
                    self._log.warning(
                        "online product is not downloadable",
                        ref=ref,
                        reason=reason,
                    )
                else:
                    self._log.warning("no online product for application", ref=ref)
        if item_ids:
            with self._api.catalog.io_lock:
                added = self._api.catalog.download_online_products(item_ids)
            self._log.info(
                "fetched missing applications", products=len(item_ids), added=len(added)
            )
        # rebuild=True: rebuild the device views here on this worker thread (behind the shared lock),
        # not lazily on the next UI frame — otherwise the full DynamicUI re-parse freezes the UI.
        self._api.project.refresh_catalog_resolution(rebuild=True)

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.catalog.strings import S
from editor_gui.widgets.dpi import label_column_width, px
from editor_gui.widgets.filter_box import filter_box

if TYPE_CHECKING:
    from editor_gui.plugins.catalog.online_catalog import (
        OnlineCatalogItem,
        OnlineIndexStatus,
        OnlineManufacturer,
    )
    from xknxeditor.catalog import ProductSummary


def _label(product: ProductSummary) -> str:
    name = product.name or product.product_ref_id
    if product.order_number and product.name:
        return f"{name}  ({product.order_number})"
    return name


class CatalogPanel:
    def __init__(
        self,
        get_products: Callable[[], list[ProductSummary]],
        on_select: Callable[[ProductSummary, int | None], None],
        get_spaces: Callable[[], list[tuple[int, str]]] | None = None,
        get_online_manufacturers: Callable[[], list[OnlineManufacturer] | None]
        | None = None,
        on_online_refresh: Callable[[], None] | None = None,
        fetch_online_items: Callable[[int], list[OnlineCatalogItem]] | None = None,
        download_online_item: Callable[[str], None] | None = None,
        language_options: list[tuple[str, str]] | None = None,
        get_language: Callable[[], str] | None = None,
        set_language: Callable[[str], None] | None = None,
        get_missing_app_count: Callable[[], int] | None = None,
        fetch_missing_apps: Callable[[], None] | None = None,
        build_online_index: Callable[
            [Callable[[int, int], None], Callable[[], bool]], object
        ]
        | None = None,
        get_online_index_status: Callable[[], OnlineIndexStatus] | None = None,
        search_online_products: Callable[
            [str, int], tuple[list[OnlineCatalogItem], int]
        ]
        | None = None,
    ) -> None:
        self._get_products = get_products
        self._on_select = on_select
        # Optional room picker for "Add device": lists (space_id, path) so a device can be placed
        # into a space on creation instead of always landing in "Without space". ``_add_space_id``
        # is the panel-level chosen target (None = no room), applied by both the button and the
        # double-click quick-add.
        self._get_spaces = get_spaces
        self._add_space_id: int | None = None
        self._search: str = ""
        # Online catalog (manufacturer list from the KNX service).
        self._get_online_manufacturers = get_online_manufacturers
        self._on_online_refresh = on_online_refresh
        self._online_loading = False
        self._online_error = False
        # Show the browse tree immediately after a restart when a manufacturer list is already
        # cached on disk (no re-fetch needed); only truly-never-fetched starts hidden.
        self._online_shown = (
            get_online_manufacturers is not None
            and get_online_manufacturers() is not None
        )
        self._online_search: str = ""
        self._language_options = language_options or []
        self._get_language = get_language
        self._set_language = set_language
        # Online product browse/download (per-manufacturer index + .knxprod download).
        self._fetch_online_items = fetch_online_items
        self._download_online_item = download_online_item
        self._online_lock = threading.Lock()
        self._online_items: dict[int, list[OnlineCatalogItem]] = {}
        self._online_items_loading: set[int] = set()
        self._online_downloading: set[str] = set()
        self._selected_product: ProductSummary | None = None
        # Fetch missing project applications from the online catalog.
        self._get_missing_app_count = get_missing_app_count
        self._fetch_missing_apps = fetch_missing_apps
        self._fetching_missing = False
        # Full product index (all manufacturers) for cross-manufacturer order-number search.
        self._build_online_index = build_online_index
        self._get_online_index_status = get_online_index_status
        self._search_online_products = search_online_products
        self._index_building = False
        self._index_progress: tuple[int, int] = (0, 0)
        self._index_cancel = False
        # Flat search result cache: recomputed only when the query changes or a build finishes
        # (_flat_query = None forces a recompute), never per frame over the whole index.
        self._flat_query: str | None = None
        self._flat_results: list[OnlineCatalogItem] = []
        self._flat_total = 0

    def render(self) -> None:
        self._render_missing_apps_banner()
        # Local vs Online as tabs instead of stacked sections, so each gets the full panel height.
        if imgui.begin_tab_bar("##catalog_tabs"):
            if imgui.begin_tab_item(S.TAB_LOCAL)[0]:
                self._render_local_catalog()
                imgui.end_tab_item()
            if (
                self._get_online_manufacturers is not None
                and imgui.begin_tab_item(S.TAB_ONLINE)[0]
            ):
                self._render_online_catalog()
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def _render_local_catalog(self) -> None:
        self._search = filter_box("##catalog_search", "Search...", self._search)

        search = self._search.lower().strip()
        leaf_flags = (
            imgui.TreeNodeFlags_.leaf
            | imgui.TreeNodeFlags_.no_tree_push_on_open
            | imgui.TreeNodeFlags_.span_avail_width
        )

        products = self._get_products()
        if not products:
            imgui.text_disabled(S.LOCAL_EMPTY)
            return

        by_manufacturer: dict[str, list[ProductSummary]] = {}
        manufacturer_labels: dict[str, str] = {}
        seen_refs: set[str] = set()
        for product in products:
            if product.product_ref_id in seen_refs:
                continue  # a product may be present from several imports — show it once
            seen_refs.add(product.product_ref_id)
            if product.manufacturer_id not in by_manufacturer:
                by_manufacturer[product.manufacturer_id] = []
                manufacturer_labels[product.manufacturer_id] = (
                    product.manufacturer_name or product.manufacturer_id
                )
            by_manufacturer[product.manufacturer_id].append(product)

        sorted_mfrs = sorted(
            by_manufacturer.keys(), key=lambda m: manufacturer_labels[m]
        )

        # Every tree node carries a unique ##id (product ref / manufacturer id) so duplicate display
        # labels never collide into the same imgui ID (which triggers a "conflicting ID" error).
        if search:
            for mfr_id in sorted_mfrs:
                mfr_label = manufacturer_labels[mfr_id]
                for product in by_manufacturer[mfr_id]:
                    name = _label(product)
                    if search in name.lower() or search in mfr_label.lower():
                        imgui.tree_node_ex(
                            f"{mfr_label} - {name}##{product.product_ref_id}",
                            leaf_flags,
                        )
                        self._handle_product_click(product)
        else:
            for mfr_id in sorted_mfrs:
                mfr_label = manufacturer_labels[mfr_id]
                if imgui.tree_node(f"{mfr_label}##{mfr_id}"):
                    for product in by_manufacturer[mfr_id]:
                        imgui.tree_node_ex(
                            f"{_label(product)}##{product.product_ref_id}", leaf_flags
                        )
                        self._handle_product_click(product)
                    imgui.tree_pop()

        self._render_product_detail()

    def _render_missing_apps_banner(self) -> None:
        """Offer to download applications missing from the local catalog for the open project."""
        if self._get_missing_app_count is None or self._fetch_missing_apps is None:
            return
        if self._fetching_missing:
            imgui.text_disabled(S.MISSING_APPS_FETCHING)
            imgui.separator()
            return
        count = self._get_missing_app_count()
        if count <= 0:
            return
        if imgui.button(S.MISSING_APPS_FETCH.format(count=count)):
            self._fetching_missing = True
            threading.Thread(target=self._run_fetch_missing, daemon=True).start()
        imgui.separator()

    def _run_fetch_missing(self) -> None:
        try:
            assert self._fetch_missing_apps is not None
            self._fetch_missing_apps()
        finally:
            self._fetching_missing = False

    def _handle_product_click(self, product: ProductSummary) -> None:
        if imgui.is_item_clicked():
            self._selected_product = product  # single click selects (shows detail)
            if imgui.is_mouse_double_clicked(0):
                self._on_select(
                    product, self._add_space_id
                )  # double click adds to the project

    def _render_product_detail(self) -> None:
        product = self._selected_product
        if product is None:
            return
        imgui.separator()
        imgui.text_disabled(S.CATALOG_DETAIL_TITLE)
        rows = (
            (S.CATALOG_DETAIL_NAME, product.name or product.product_ref_id),
            (S.CATALOG_DETAIL_ORDER, product.order_number or "-"),
            (
                S.CATALOG_DETAIL_MANUFACTURER,
                product.manufacturer_name or product.manufacturer_id,
            ),
            (S.CATALOG_DETAIL_APPLICATION, product.application_id or "-"),
            (
                S.CATALOG_DETAIL_VERSION,
                f"V{product.application_version}"
                if product.application_version is not None
                else "-",
            ),
            (S.CATALOG_DETAIL_PRODUCT_REF, product.product_ref_id),
        )
        label_w = label_column_width(*(label for label, _ in rows), minimum=150.0)
        for label, value in rows:
            imgui.text_disabled(label)
            imgui.same_line(label_w)
            imgui.text_wrapped(value)
        self._render_space_picker()
        if imgui.button(S.CATALOG_DETAIL_ADD):
            self._on_select(product, self._add_space_id)

    def _render_space_picker(self) -> None:
        """Room combo above the Add button: pick a space to place the new device in (or none).
        Hidden when no space getter is wired or the project has no spaces yet."""
        if self._get_spaces is None:
            return
        spaces = self._get_spaces()
        if not spaces:
            self._add_space_id = None
            return
        options = [(None, S.CATALOG_ADD_SPACE_NONE), *spaces]
        # Drop a stale selection (e.g. the chosen space was deleted) so Add never targets a gone id.
        if self._add_space_id is not None and all(
            sid != self._add_space_id for sid, _ in spaces
        ):
            self._add_space_id = None
        current = next(
            (i for i, (sid, _) in enumerate(options) if sid == self._add_space_id), 0
        )
        imgui.set_next_item_width(px(240.0))
        changed, picked = imgui.combo(
            S.CATALOG_ADD_SPACE_LABEL, current, [label for _, label in options]
        )
        if changed:
            self._add_space_id = options[picked][0]

    def _render_online_catalog(self) -> None:
        """Online manufacturer list from the KNX catalog service (button + tree).

        ``get_online_manufacturers`` is called every frame; it must return the cached list
        (or None) without touching the network. The fetch itself runs on a worker thread."""
        online = (
            self._get_online_manufacturers()
            if (self._get_online_manufacturers is not None)
            else None
        )
        imgui.set_next_item_width(px(160.0))
        if imgui.button(S.BTN_ONLINE_CATALOG):
            self._start_online_refresh()
        if self._online_loading:
            imgui.same_line()
            imgui.text_disabled(S.ONLINE_LOADING)
        elif self._online_error:
            imgui.same_line()
            imgui.text_colored(imgui.ImVec4(1.0, 0.4, 0.4, 1.0), S.ONLINE_FAILED)

        self._render_language_combo()
        leaf_flags = (
            imgui.TreeNodeFlags_.leaf
            | imgui.TreeNodeFlags_.no_tree_push_on_open
            | imgui.TreeNodeFlags_.span_avail_width
        )
        self._render_index_controls()
        imgui.separator()

        # The search runs against the full product index (all manufacturers), so an order number
        # like "AKD-0424" finds devices even before the manufacturer-name list is fetched or a
        # node is expanded. An empty query falls back to the manufacturer browse tree below.
        self._online_search = filter_box(
            "##online_search", S.ONLINE_SEARCH_HINT, self._online_search
        )
        query = self._online_search.lower().strip()
        if (
            query != self._flat_query
        ):  # None sentinel (after a build) always forces a recompute
            self._recompute_flat(query)
        if query:
            self._render_flat_results(leaf_flags)
            return

        if online is None or not self._online_shown:
            return
        shown = sorted(
            (m for m in online if self._manufacturer_matches(m, query)),
            key=lambda m: m.name.lower(),
        )
        imgui.text_disabled(S.ONLINE_COUNT.format(count=len(shown)))
        for mfr in shown:
            name_hit = (
                not query or query in mfr.name.lower() or query in f"m-{mfr.id:04d}"
            )
            if imgui.tree_node(f"{mfr.name}  (M-{mfr.id:04d})##online_mfr_{mfr.id}"):
                self._render_online_products(
                    mfr.id, leaf_flags, "" if name_hit else query
                )
                imgui.tree_pop()

    def _manufacturer_matches(self, mfr: OnlineManufacturer, query: str) -> bool:
        if not query:
            return True
        if query in mfr.name.lower() or query in f"m-{mfr.id:04d}":
            return True
        with (
            self._online_lock
        ):  # keep manufacturers whose already-loaded products match
            items = self._online_items.get(mfr.id)
        return bool(items) and any(
            query in i.name.lower() or query in i.order_number.lower() for i in items
        )

    def _render_language_combo(self) -> None:
        """Country/language selector for online product downloads (persisted by the service)."""
        if not (self._language_options and self._get_language and self._set_language):
            return
        current = self._get_language()
        cur_label = next(
            (label for label, code in self._language_options if code == current),
            current,
        )
        imgui.set_next_item_width(px(200.0))
        if imgui.begin_combo(S.ONLINE_COUNTRY_LABEL, cur_label):
            for label, code in self._language_options:
                selected = code == current
                if imgui.selectable(label, selected)[0]:
                    self._set_language(code)
                if selected:
                    imgui.set_item_default_focus()
            imgui.end_combo()

    def _render_online_products(
        self, manufacturer_id: int, leaf_flags: int, query: str = ""
    ) -> None:
        """Products of one online manufacturer: fetched on first expand, downloaded on double-click.

        ``query`` (the online search text) filters the products by name / order number."""
        with self._online_lock:
            items = self._online_items.get(manufacturer_id)
            loading = manufacturer_id in self._online_items_loading
            downloading = set(self._online_downloading)
        if items is None:
            if not loading:
                self._start_fetch_items(manufacturer_id)
            imgui.text_disabled(S.ONLINE_LOADING_PRODUCTS)
            return
        if query:
            items = [
                i
                for i in items
                if query in i.name.lower() or query in i.order_number.lower()
            ]
        if not items:
            imgui.text_disabled(S.ONLINE_NO_PRODUCTS)
            return
        items = sorted(items, key=lambda i: i.name.lower())
        for item in items:
            label = (
                f"{item.name}  ({item.order_number})"
                if item.order_number
                else item.name
            )
            if item.id in downloading:
                imgui.text_disabled(S.ONLINE_DOWNLOADING.format(name=item.name))
                continue
            if not item.downloadable:
                imgui.text_disabled(f"{label}  {S.ONLINE_NOT_DOWNLOADABLE}")
                continue
            imgui.tree_node_ex(f"{label}##online_item_{item.id}", leaf_flags)
            if imgui.is_item_clicked() and imgui.is_mouse_double_clicked(0):
                self._start_download(item.id)

    def _start_fetch_items(self, manufacturer_id: int) -> None:
        if self._fetch_online_items is None:
            return
        with self._online_lock:
            if manufacturer_id in self._online_items_loading:
                return
            self._online_items_loading.add(manufacturer_id)
        threading.Thread(
            target=self._fetch_items, args=(manufacturer_id,), daemon=True
        ).start()

    def _fetch_items(self, manufacturer_id: int) -> None:
        assert self._fetch_online_items is not None
        try:
            items = self._fetch_online_items(manufacturer_id)
        except Exception:
            items = []  # error already logged by the plugin; show "no products"
        with self._online_lock:
            self._online_items[manufacturer_id] = items
            self._online_items_loading.discard(manufacturer_id)

    def _start_download(self, item_id: str) -> None:
        if self._download_online_item is None:
            return
        with self._online_lock:
            if item_id in self._online_downloading:
                return
            self._online_downloading.add(item_id)
        threading.Thread(
            target=self._download_item, args=(item_id,), daemon=True
        ).start()

    def _download_item(self, item_id: str) -> None:
        assert self._download_online_item is not None
        try:
            self._download_online_item(item_id)
        except Exception:
            pass  # error already logged by the plugin
        finally:
            with self._online_lock:
                self._online_downloading.discard(item_id)

    # --- full product index: build + flat cross-manufacturer search ------

    def _render_index_controls(self) -> None:
        """Status line + (while building) progress + cancel. The index is built automatically after
        'fetch online catalog' — there is no separate build button."""
        if self._get_online_index_status is None or self._build_online_index is None:
            return
        if self._index_building:
            done, total = self._index_progress
            imgui.text_disabled(
                S.ONLINE_INDEX_BUILDING.format(done=done, total=total or "?")
            )
            imgui.same_line()
            if imgui.button(S.ONLINE_INDEX_CANCEL):
                self._index_cancel = True
            return
        status = self._get_online_index_status()
        if status.products:
            imgui.text_disabled(
                S.ONLINE_INDEX_STATUS.format(
                    mfrs=status.manufacturers, products=status.products
                )
            )
        else:
            imgui.text_disabled(S.ONLINE_INDEX_NONE)

    def _recompute_flat(self, query: str) -> None:
        """Refresh the cached flat search results for ``query`` (only called on query change)."""
        self._flat_query = query
        if not query or self._search_online_products is None:
            self._flat_results, self._flat_total = [], 0
            return
        self._flat_results, self._flat_total = self._search_online_products(query, 300)

    def _render_flat_results(self, leaf_flags: int) -> None:
        """Flat cross-manufacturer product hits for the current query; double-click downloads."""
        if self._get_online_index_status is not None:
            status = self._get_online_index_status()
            if not status.products:
                imgui.text_disabled(S.ONLINE_SEARCH_BUILD_HINT)
                return
        if not self._flat_results:
            imgui.text_disabled(S.ONLINE_NO_PRODUCTS)
            return
        if self._flat_total > len(self._flat_results):
            imgui.text_disabled(
                S.ONLINE_FLAT_CAPPED.format(
                    shown=len(self._flat_results), total=self._flat_total
                )
            )
        else:
            imgui.text_disabled(S.ONLINE_MATCH_COUNT.format(count=self._flat_total))
        with self._online_lock:
            downloading = set(self._online_downloading)
        local = self._local_versions_by_order()
        for item in self._flat_results:
            order = f"  ({item.order_number})" if item.order_number else ""
            ver = (
                f"  V{item.application_version}"
                if item.application_version is not None
                else ""
            )
            mfr = f"  - {item.manufacturer_name}" if item.manufacturer_name else ""
            have = local.get(item.order_number.lower())
            imported = have is not None and (
                item.application_version is None
                or None in have
                or item.application_version in have
            )
            mark = f"  {S.ONLINE_ALREADY_IMPORTED}" if imported else ""
            label = f"{item.name}{order}{ver}{mfr}{mark}"
            if item.id in downloading:
                imgui.text_disabled(S.ONLINE_DOWNLOADING.format(name=item.name))
                continue
            if not item.downloadable:
                imgui.text_disabled(f"{label}  {S.ONLINE_NOT_DOWNLOADABLE}")
                continue
            imgui.tree_node_ex(f"{label}##flat_{item.id}", leaf_flags)
            if imgui.is_item_clicked() and imgui.is_mouse_double_clicked(0):
                self._start_download(item.id)

    def _local_versions_by_order(self) -> dict[str, set[int | None]]:
        """Map order number (lower-case) -> set of application versions already in the local
        catalog, so online search hits can be marked as already imported."""
        out: dict[str, set[int | None]] = {}
        for product in self._get_products():
            if product.order_number:
                out.setdefault(product.order_number.lower(), set()).add(
                    product.application_version
                )
        return out

    def _run_build_index(self) -> None:
        """Build the full product index (resumable). Runs inline on the online-fetch worker thread
        after the manufacturer list is fetched, so no separate button/thread is needed."""
        if self._build_online_index is None or self._index_building:
            return
        self._index_building = True
        self._index_cancel = False
        self._index_progress = (0, 0)
        try:
            self._build_online_index(
                self._on_build_progress, lambda: self._index_cancel
            )
        except Exception:
            pass  # network error already logged by the plugin
        finally:
            self._index_building = False
            self._flat_query = (
                None  # force the flat search to recompute with the new data
            )

    def _on_build_progress(self, done: int, total: int) -> None:
        self._index_progress = (done, total)

    def _start_online_refresh(self) -> None:
        """Fetch the manufacturer list off the UI thread (urllib blocks)."""
        if self._online_loading or self._on_online_refresh is None:
            return
        self._online_loading = True
        self._online_error = False
        threading.Thread(target=self._fetch_online, daemon=True).start()

    def _fetch_online(self) -> None:
        if self._on_online_refresh is None:
            return
        try:
            self._on_online_refresh()
            self._online_shown = True
        except Exception:
            self._online_error = True
            self._online_loading = False
            return
        self._online_loading = False
        # Build the full product index right after fetching, so order-number search works across
        # all manufacturers without a separate button. Resumable, so re-fetching is cheap.
        self._run_build_index()

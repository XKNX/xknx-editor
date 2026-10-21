from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.device_info import application_version_text
from editor_gui.hardware_text import bus_current_text, rail_text
from editor_gui.plugins.catalog.strings import S
from editor_gui.strings import get_locale
from editor_gui.widgets.backgrounds import fill_content_background
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import label_column_width, px, px_vec2
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.tree import leaf_rows, tree_node

if TYPE_CHECKING:
    from editor_gui.plugins.catalog.online_catalog import (
        OnlineCatalogItem,
        OnlineCatalogSection,
        OnlineIndexStatus,
        OnlineManufacturer,
    )
    from xknxeditor.catalog import ProductSummary


@dataclass
class _SectionNode:
    """A catalog section in the online tree: its sections below and the products it files."""

    section: OnlineCatalogSection | None
    children: dict[str, _SectionNode] = field(default_factory=dict[str, "_SectionNode"])
    items: list[OnlineCatalogItem] = field(default_factory=list["OnlineCatalogItem"])


def section_tree(items: list[OnlineCatalogItem], locale: str) -> _SectionNode:
    """``items`` filed into their catalog sections; sections and products sorted by their names
    (the sections' in ``locale``), products without a section at the top level."""
    root = _SectionNode(None)
    for item in items:
        node = root
        for section in item.sections:
            node = node.children.setdefault(section.id, _SectionNode(section))
        node.items.append(item)

    def order(node: _SectionNode) -> None:
        node.children = dict(
            sorted(
                node.children.items(),
                key=lambda kv: _section_name(kv[1], locale).lower(),
            )
        )
        node.items.sort(key=lambda i: i.name.lower())
        for child in node.children.values():
            order(child)

    order(root)
    return root


def _online_label(
    item: OnlineCatalogItem,
    local: dict[str, set[int | None]],
    *,
    with_manufacturer: bool = False,
) -> str:
    """An online product as listed: its name, order number and application version, its
    manufacturer when asked for, and whether the local catalog has it already (``local``: the
    versions there by lower-case order number)."""
    order = f"  ({item.order_number})" if item.order_number else ""
    ver = (
        f"  V{application_version_text(item.application_version)}"
        if item.application_version is not None
        else ""
    )
    mfr = (
        f"  - {item.manufacturer_name}"
        if with_manufacturer and item.manufacturer_name
        else ""
    )
    have = local.get(item.order_number.lower())
    imported = have is not None and (
        item.application_version is None
        or None in have
        or item.application_version in have
    )
    mark = f"  {S.ONLINE_ALREADY_IMPORTED}" if imported else ""
    return f"{item.name}{order}{ver}{mfr}{mark}"


def _section_name(node: _SectionNode, locale: str) -> str:
    return node.section.name_in(locale) if node.section is not None else ""


def _label(product: ProductSummary) -> str:
    name = product.name or product.product_ref_id
    if product.order_number and product.name:
        name = f"{name}  ({product.order_number})"
    if product.application_version is not None:
        name = f"{name}  V{application_version_text(product.application_version)}"
    return name


def _key(product: ProductSummary) -> str:
    """Identifies one version of a product in the list."""
    return f"{product.product_ref_id}|{product.hardware2program_ref_id}"


def _safe_stem(text: str) -> str:
    """A filesystem-friendly default file name (no path separators or odd characters)."""
    stem = "".join(c if c.isalnum() or c in " -_." else "_" for c in text).strip()
    return stem or "product"


def _same(a: ProductSummary | None, b: ProductSummary) -> bool:
    return a is not None and _key(a) == _key(b)


def _version_text(product: ProductSummary) -> str:
    if product.application_version is None:
        return "-"
    return application_version_text(product.application_version)


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
        add_online_item: Callable[[str], None] | None = None,
        can_add_to_project: Callable[[], bool] | None = None,
        plan_delete_product: Callable[[ProductSummary], int | None] | None = None,
        delete_product: Callable[[ProductSummary], None] | None = None,
        export_product: Callable[[ProductSummary, str], None] | None = None,
    ) -> None:
        self._get_products = get_products
        self._on_select = on_select
        # Optional room picker for "Add device": lists (space_id, path) so a device can be placed
        # into a space on creation instead of always landing in "Without space". ``_add_space_id``
        # is the panel-level chosen target (None = no room), applied by both the button and the
        # double-click quick-add.
        self._get_spaces = get_spaces
        self._add_space_id: int | None = None
        # Double-click a product to add it: first ask which room to place it in (when the project
        # has rooms). ``_add_target`` is the product waiting on that choice.
        self._add_target: ProductSummary | None = None
        self._open_add_space_popup = False
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
        # Online product browse/download (per-manufacturer index + .knxprod download).
        self._fetch_online_items = fetch_online_items
        self._download_online_item = download_online_item
        self._online_lock = threading.Lock()
        self._online_items: dict[int, list[OnlineCatalogItem]] = {}
        # (frame, versions in the local catalog by order number) of the last lookup.
        self._local_versions: tuple[int, dict[str, set[int | None]]] = (-1, {})
        # Manufacturer id -> (products, (filter, language), section tree) of the tree last built.
        self._online_trees: dict[
            int, tuple[list[OnlineCatalogItem], tuple[str, str], _SectionNode]
        ] = {}
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
        # After an online download+import, offer to add the product to the project (confirmed in a
        # modal rendered on the GUI thread; set from the download worker thread under _online_lock).
        self._add_online_item = add_online_item
        self._can_add_to_project = can_add_to_project
        self._pending_add_item: str | None = None
        self._pending_add_name: str = ""
        # Delete a local product: the planner returns the number of project devices that would
        # lose their product data (None when the product is gone); the delete runs on a worker.
        self._plan_delete_product = plan_delete_product
        self._delete_product = delete_product
        self._delete_target: ProductSummary | None = None
        self._delete_affected = 0
        self._open_delete_popup = False
        self._deleting: str | None = None
        # Export a local product's original .knxprod archive. The save dialog is polled each frame
        # (desktop or browser); on a chosen path the callback copies the stored archive there.
        self._export_product_cb = export_product
        self._export_dialog: file_dialogs.SaveFileDialog | None = None
        self._export_target: ProductSummary | None = None

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
        self._render_add_to_project_modal()
        self._render_add_space_modal()
        self._render_delete_modal()
        self._poll_export()

    def _render_local_catalog(self) -> None:
        self._search = filter_box("##catalog_search", S.LOCAL_SEARCH_HINT, self._search)
        fill_content_background()
        deleting = self._deleting
        if deleting is not None:
            imgui.text_disabled(S.DELETING.format(name=deleting))

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
        seen: set[str] = set()
        for product in products:
            if _key(product) in seen:
                continue  # a product version may be listed in several catalog sections
            seen.add(_key(product))
            if product.manufacturer_id not in by_manufacturer:
                by_manufacturer[product.manufacturer_id] = []
                manufacturer_labels[product.manufacturer_id] = (
                    product.manufacturer_name or product.manufacturer_id
                )
            by_manufacturer[product.manufacturer_id].append(product)

        sorted_mfrs = sorted(
            by_manufacturer.keys(), key=lambda m: manufacturer_labels[m].lower()
        )
        for mfr_products in by_manufacturer.values():
            # Newest version first within a product.
            mfr_products.sort(
                key=lambda p: (
                    (p.name or p.product_ref_id).lower(),
                    (p.order_number or "").lower(),
                    -(p.application_version or 0),
                )
            )

        # Every tree node carries a unique ##id (product ref / manufacturer id) so duplicate display
        # labels never collide into the same imgui ID (which triggers a "conflicting ID" error).
        if search:
            for mfr_id in sorted_mfrs:
                mfr_label = manufacturer_labels[mfr_id]
                for product in by_manufacturer[mfr_id]:
                    name = _label(product)
                    if search in name.lower() or search in mfr_label.lower():
                        tree_node(f"{mfr_label} - {name}##{_key(product)}", leaf_flags)
                        self._handle_product_click(product)
                        self._render_product_context_menu(product)
        else:
            for mfr_id in sorted_mfrs:
                mfr_label = manufacturer_labels[mfr_id]
                if tree_node(f"{mfr_label}##{mfr_id}"):
                    with leaf_rows(nodes=True):
                        for product in by_manufacturer[mfr_id]:
                            tree_node(f"{_label(product)}##{_key(product)}", leaf_flags)
                            self._handle_product_click(product)
                            self._render_product_context_menu(product)
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
                self._request_add(product)  # double click adds to the project

    def _request_add(self, product: ProductSummary) -> None:
        """Double-click add: ask which room to place the device in first. Adds straight away only
        when the project has no rooms to choose from."""
        spaces = self._get_spaces() if self._get_spaces is not None else []
        if not spaces:
            self._on_select(product, None)
            return
        self._add_target = product
        self._open_add_space_popup = True

    def _render_add_space_modal(self) -> None:
        title = S.CATALOG_ADD_SPACE_TITLE
        if self._open_add_space_popup:
            imgui.open_popup(title)
            self._open_add_space_popup = False
        product = self._add_target
        if product is None:
            return
        if begin_dialog(title):
            imgui.dummy(px_vec2(360, 0))  # anchor the minimum content width
            imgui.text_wrapped(
                S.CATALOG_ADD_SPACE_PROMPT.format(
                    name=product.name or product.product_ref_id
                )
            )
            imgui.spacing()
            self._render_space_picker()
            clicked = dialog_buttons(S.CATALOG_DETAIL_ADD, S.BTN_CANCEL)
            if clicked == 0:
                self._on_select(product, self._add_space_id)
                self._add_target = None
                imgui.close_current_popup()
            elif clicked == 1:
                self._add_target = None
                imgui.close_current_popup()
            end_dialog()

    def _render_product_context_menu(self, product: ProductSummary) -> None:
        if self._delete_product is None and self._export_product_cb is None:
            return
        if imgui.begin_popup_context_item(f"##catalog_ctx_{_key(product)}"):
            if (
                self._export_product_cb is not None
                and imgui.menu_item(S.CONTEXT_EXPORT_KNXPROD, "", False)[0]
            ):
                self._start_export(product)
            if (
                self._delete_product is not None
                and imgui.menu_item(
                    S.CONTEXT_DELETE, "", False, self._deleting is None
                )[0]
            ):
                self._request_delete(product)
            imgui.end_popup()

    def _start_export(self, product: ProductSummary) -> None:
        """Ask where to save the product's original ``.knxprod`` archive."""
        stem = _safe_stem(
            product.order_number or product.name or product.product_ref_id
        )
        self._export_target = product
        self._export_dialog = file_dialogs.save_file(
            S.EXPORT_KNXPROD_TITLE,
            f"{stem}.knxprod",
            [S.EXPORT_KNXPROD_FILTER, "*.knxprod"],
        )

    def _poll_export(self) -> None:
        if self._export_dialog is None or not self._export_dialog.ready():
            return
        dest = self._export_dialog.result()
        self._export_dialog = None
        product, self._export_target = self._export_target, None
        if dest and product is not None and self._export_product_cb is not None:
            self._export_product_cb(product, dest)

    def _request_delete(self, product: ProductSummary) -> None:
        """Open the delete confirmation, with the number of project devices it affects."""
        affected = (
            self._plan_delete_product(product)
            if self._plan_delete_product is not None
            else 0
        )
        if affected is None:
            return
        self._delete_target = product
        self._delete_affected = affected
        self._open_delete_popup = True

    def _render_delete_modal(self) -> None:
        title = S.DELETE_TITLE
        if self._open_delete_popup:
            imgui.open_popup(title)
            self._open_delete_popup = False
        if not begin_dialog(title):
            return
        product = self._delete_target
        imgui.dummy(px_vec2(440, 0))  # anchor the minimum content width
        imgui.text_wrapped(S.DELETE_CONFIRM)
        if product is not None:
            imgui.spacing()
            rows = (
                (S.CATALOG_DETAIL_NAME, product.name or product.product_ref_id),
                (S.CATALOG_DETAIL_ORDER, product.order_number or "-"),
                (S.CATALOG_DETAIL_VERSION, _version_text(product)),
            )
            label_w = label_column_width(*(label for label, _ in rows), minimum=150.0)
            for label, value in rows:
                imgui.text_disabled(label)
                imgui.same_line(label_w)
                imgui.text_wrapped(value)
        if self._delete_affected > 0:
            imgui.spacing()
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.95, 0.6, 0.25, 1.0))
            imgui.text_wrapped(S.DELETE_AFFECTED.format(count=self._delete_affected))
            imgui.pop_style_color()
        clicked = dialog_buttons(S.CONTEXT_DELETE, S.BTN_CANCEL)
        if clicked == 0:
            if product is not None:
                self._start_delete(product)
            self._delete_target = None
            imgui.close_current_popup()
        elif clicked == 1:
            self._delete_target = None
            imgui.close_current_popup()
        end_dialog()

    def _start_delete(self, product: ProductSummary) -> None:
        if self._delete_product is None or self._deleting is not None:
            return
        self._deleting = product.name or product.product_ref_id
        if _same(self._selected_product, product):
            self._selected_product = None
        threading.Thread(target=self._run_delete, args=(product,), daemon=True).start()

    def _run_delete(self, product: ProductSummary) -> None:
        assert self._delete_product is not None
        try:
            self._delete_product(product)
        except Exception:
            pass  # error already logged by the plugin
        finally:
            if _same(self._selected_product, product):
                self._selected_product = None
            self._deleting = None

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
            (S.CATALOG_DETAIL_RAIL, rail_text(product, S.CATALOG_YES, S.CATALOG_NO)),
            (S.CATALOG_DETAIL_BUS_CURRENT, bus_current_text(product)),
            (
                S.CATALOG_DETAIL_APPLICATION,
                product.application_name or product.application_id or "-",
            ),
            (S.CATALOG_DETAIL_VERSION, _version_text(product)),
            (S.CATALOG_DETAIL_APPLICATION_ID, product.application_id or "-"),
            (S.CATALOG_DETAIL_PRODUCT_REF, product.product_ref_id),
        )
        label_w = label_column_width(*(label for label, _ in rows), minimum=150.0)
        for label, value in rows:
            imgui.text_disabled(label)
            imgui.same_line(label_w)
            imgui.text_wrapped(value)
        if self._render_space_picker():
            imgui.same_line()
        if imgui.button(S.CATALOG_DETAIL_ADD):
            self._on_select(product, self._add_space_id)

    def _render_space_picker(self) -> bool:
        """Room label and combo before the Add button: pick a space to place the new device in
        (or none). Hidden when no space getter is wired or the project has no spaces yet; returns
        whether it was shown."""
        if self._get_spaces is None:
            return False
        spaces = self._get_spaces()
        if not spaces:
            self._add_space_id = None
            return False
        options = [(None, S.CATALOG_ADD_SPACE_NONE), *spaces]
        # Drop a stale selection (e.g. the chosen space was deleted) so Add never targets a gone id.
        if self._add_space_id is not None and all(
            sid != self._add_space_id for sid, _ in spaces
        ):
            self._add_space_id = None
        current = next(
            (i for i, (sid, _) in enumerate(options) if sid == self._add_space_id), 0
        )
        imgui.align_text_to_frame_padding()
        imgui.text(S.CATALOG_ADD_SPACE_LABEL)
        imgui.same_line()
        imgui.set_next_item_width(px(240.0))
        changed, picked = combo(
            "##catalog_add_space", current, [label for _, label in options]
        )
        if changed:
            self._add_space_id = options[picked][0]
        return True

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
        self._render_index_controls()

        leaf_flags = (
            imgui.TreeNodeFlags_.leaf
            | imgui.TreeNodeFlags_.no_tree_push_on_open
            | imgui.TreeNodeFlags_.span_avail_width
        )
        imgui.separator()

        # The search runs against the full product index (all manufacturers), so an order number
        # like "AKD-0424" finds devices even before the manufacturer-name list is fetched or a
        # node is expanded. An empty query falls back to the manufacturer browse tree below.
        self._online_search = filter_box(
            "##online_search", S.ONLINE_SEARCH_HINT, self._online_search
        )
        query = self._online_search.lower().strip()
        fill_content_background()
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
        for mfr in shown:
            name_hit = (
                not query or query in mfr.name.lower() or query in f"m-{mfr.id:04d}"
            )
            if tree_node(f"{mfr.name}  (M-{mfr.id:04d})##online_mfr_{mfr.id}"):
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
        tree = self._section_tree(manufacturer_id, items, query)
        if not tree.children and not tree.items:
            imgui.text_disabled(S.ONLINE_NO_PRODUCTS)
            return
        self._render_section(
            tree, manufacturer_id, leaf_flags, downloading, bool(query)
        )

    def _section_tree(
        self, manufacturer_id: int, items: list[OnlineCatalogItem], query: str
    ) -> _SectionNode:
        """The products matching ``query`` filed into their sections, kept while the products,
        the filter and the language stay the same."""
        locale = get_locale()
        cached = self._online_trees.get(manufacturer_id)
        if cached is not None and cached[0] is items and cached[1] == (query, locale):
            return cached[2]
        shown = [
            i
            for i in items
            if not query or query in i.name.lower() or query in i.order_number.lower()
        ]
        tree = section_tree(shown, locale)
        self._online_trees[manufacturer_id] = (items, (query, locale), tree)
        return tree

    def _render_section(
        self,
        node: _SectionNode,
        manufacturer_id: int,
        leaf_flags: int,
        downloading: set[str],
        open_all: bool,
    ) -> None:
        """A section's sections below it, as tree nodes, then its products."""
        locale = get_locale()
        for child in node.children.values():
            assert child.section is not None
            if open_all:
                imgui.set_next_item_open(True, imgui.Cond_.always)
            label = child.section.name_in(locale)
            if tree_node(f"{label}##online_sec_{manufacturer_id}_{child.section.id}"):
                self._render_section(
                    child, manufacturer_id, leaf_flags, downloading, open_all
                )
                imgui.tree_pop()
        local = self._local_versions_by_order()
        for item in node.items:
            self._render_online_item(item, leaf_flags, downloading, local)

    def _render_online_item(
        self,
        item: OnlineCatalogItem,
        leaf_flags: int,
        downloading: set[str],
        local: dict[str, set[int | None]],
    ) -> None:
        label = _online_label(item, local)
        if item.id in downloading:
            imgui.text_disabled(S.ONLINE_DOWNLOADING.format(name=item.name))
            return
        if not item.downloadable:
            imgui.text_disabled(f"{label}  {S.ONLINE_NOT_DOWNLOADABLE}")
            return
        with leaf_rows(nodes=True):
            tree_node(f"{label}##online_item_{item.id}", leaf_flags)
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
        ok = False
        try:
            self._download_online_item(item_id)
            ok = True
        except Exception:
            pass  # error already logged by the plugin
        finally:
            with self._online_lock:
                self._online_downloading.discard(item_id)
        # Offer to add it to the project, but only on a successful import and with a project open.
        if (
            ok
            and self._add_online_item is not None
            and (self._can_add_to_project is None or self._can_add_to_project())
        ):
            name = self._online_item_name(item_id)
            with self._online_lock:
                self._pending_add_item = item_id
                self._pending_add_name = name

    def _online_item_name(self, item_id: str) -> str:
        with self._online_lock:
            pools = [*self._online_items.values(), self._flat_results]
        for items in pools:
            for it in items:
                if it.id == item_id:
                    return it.name or item_id
        return item_id

    def _render_add_to_project_modal(self) -> None:
        with self._online_lock:
            item_id = self._pending_add_item
            name = self._pending_add_name
        if item_id is None:
            return
        title = S.CATALOG_ADD_ONLINE_TITLE
        imgui.open_popup(title)
        # A comfortable width so the prompt doesn't wrap narrow.
        imgui.set_next_window_size_constraints(
            imgui.ImVec2(px(460.0), px(140.0)), imgui.ImVec2(px(680.0), 1.0e9)
        )
        if begin_dialog(title):
            imgui.dummy(
                imgui.ImVec2(px(440.0), 0.0)
            )  # anchor the minimum content width
            imgui.text_wrapped(S.CATALOG_ADD_ONLINE_PROMPT.format(name=name))
            clicked = dialog_buttons(S.CATALOG_ADD_ONLINE_YES, S.CATALOG_ADD_ONLINE_NO)
            if clicked == 0:
                if self._add_online_item is not None:
                    self._add_online_item(item_id)
                self._clear_pending_add()
                imgui.close_current_popup()
            elif clicked == 1:
                self._clear_pending_add()
                imgui.close_current_popup()
            end_dialog()

    def _clear_pending_add(self) -> None:
        with self._online_lock:
            self._pending_add_item = None
            self._pending_add_name = ""

    # --- full product index: build + flat cross-manufacturer search ------

    def _render_index_controls(self) -> None:
        """Status (while building: progress + cancel) on the line of the fetch button. The index is
        built automatically after 'fetch online catalog' — there is no separate build button."""
        if self._get_online_index_status is None or self._build_online_index is None:
            return
        imgui.same_line()
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
            label = _online_label(item, local, with_manufacturer=True)
            if item.id in downloading:
                imgui.text_disabled(S.ONLINE_DOWNLOADING.format(name=item.name))
                continue
            if not item.downloadable:
                imgui.text_disabled(f"{label}  {S.ONLINE_NOT_DOWNLOADABLE}")
                continue
            tree_node(f"{label}##flat_{item.id}", leaf_flags)
            if imgui.is_item_clicked() and imgui.is_mouse_double_clicked(0):
                self._start_download(item.id)

    def _local_versions_by_order(self) -> dict[str, set[int | None]]:
        """Map order number (lower-case) -> set of application versions already in the local
        catalog, so online search hits can be marked as already imported. Built once per frame."""
        frame = imgui.get_frame_count()
        if self._local_versions[0] == frame:
            return self._local_versions[1]
        out: dict[str, set[int | None]] = {}
        for product in self._get_products():
            if product.order_number:
                out.setdefault(product.order_number.lower(), set()).add(
                    product.application_version
                )
        self._local_versions = (frame, out)
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

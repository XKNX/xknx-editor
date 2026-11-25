"""The Delete key in the local catalog tree asks to delete the selected product, as the "Delete"
of its menu does."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.catalog.ui import panel as panel_module
from editor_gui.plugins.catalog.ui.panel import CatalogPanel
from xknxeditor.catalog import ProductSummary

from .test_delete_product import _fixture


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.fixture
def products(tmp_path: Path) -> list[ProductSummary]:
    imported = CatalogService(tmp_path / "c.xknxcatalog").import_knxprod_contents(
        _fixture()
    )
    return list(imported.products)


def _panel(products: list[ProductSummary], asked: list[ProductSummary]) -> CatalogPanel:
    def plan(product: ProductSummary) -> int:
        asked.append(product)
        return 0

    return CatalogPanel(
        get_products=lambda: products,
        on_select=lambda *_args: None,
        plan_delete_product=plan,
        delete_product=lambda _product: None,
    )


def _press_delete(panel: CatalogPanel, monkeypatch: pytest.MonkeyPatch) -> None:
    """Frames of the Local tab with its tree taking the keyboard (as after a click into it), the
    Delete key down in the last one."""
    real = panel_module.begin_tree_area

    def begin_tree_area(*args: Any) -> bool:
        imgui.set_next_window_focus()
        return real(*args)

    monkeypatch.setattr(panel_module, "begin_tree_area", begin_tree_area)
    io = imgui.get_io()
    for index in range(3):
        if index == 2:
            io.add_key_event(imgui.Key.delete, True)
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        # A click into the tree gives its panel the focus too.
        imgui.set_next_window_focus()
        imgui.begin("Catalog")
        panel._render_local_catalog()  # pyright: ignore[reportPrivateUsage]
        imgui.end()
        imgui.end_frame()
    io.add_key_event(imgui.Key.delete, False)
    imgui.new_frame()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_the_delete_key_asks_to_delete_the_selected_product(
    products: list[ProductSummary], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[ProductSummary] = []
    panel = _panel(products, asked)
    panel._selected_product = products[0]  # pyright: ignore[reportPrivateUsage]
    panel._search = products[0].name or ""  # pyright: ignore[reportPrivateUsage]
    _press_delete(panel, monkeypatch)
    assert asked == [products[0]]
    assert panel._delete_target is products[0]  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("context")
def test_the_delete_key_leaves_a_product_alone_that_does_not_show(
    products: list[ProductSummary], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[ProductSummary] = []
    panel = _panel(products, asked)
    panel._selected_product = products[0]  # pyright: ignore[reportPrivateUsage]
    # The search hides it.
    panel._search = "no such product"  # pyright: ignore[reportPrivateUsage]
    _press_delete(panel, monkeypatch)
    assert asked == []


@pytest.mark.usefixtures("context")
def test_the_delete_key_waits_for_a_delete_under_way(
    products: list[ProductSummary], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[ProductSummary] = []
    panel = _panel(products, asked)
    panel._selected_product = products[0]  # pyright: ignore[reportPrivateUsage]
    panel._search = products[0].name or ""  # pyright: ignore[reportPrivateUsage]
    panel._deleting = "another"  # pyright: ignore[reportPrivateUsage]
    _press_delete(panel, monkeypatch)
    assert asked == []

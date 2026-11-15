"""The Product Catalog Import table says of each product version whether the import added it,
the new ones first, and is as wide as its texts."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.main import KnxGuiApp, _ProductRow
from xknxeditor.catalog import ProductSummary


def _product(ref: str, name: str, manufacturer: str = "Maker") -> ProductSummary:
    return ProductSummary(
        product_ref_id=ref,
        hardware2program_ref_id=f"{ref}_HP",
        name=name,
        order_number=None,
        application_id=None,
        manufacturer_id="M-0001",
        manufacturer_name=manufacturer,
    )


def test_new_versions_come_first_and_are_marked() -> None:
    app = cast(
        "Any",
        SimpleNamespace(
            _catalog_service=SimpleNamespace(product_facts=lambda _ref: None)
        ),
    )
    products = [
        _product("P-1", "Actuator"),
        _product("P-2", "Button"),
        _product("P-3", "Clock"),
    ]
    rows = KnxGuiApp._product_import_rows(app, products, frozenset({("P-3", "P-3_HP")}))
    assert [(row.product, row.new) for row in rows] == [
        ("Clock", True),
        ("Actuator", False),
        ("Button", False),
    ]


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    yield
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _row(product: str, manufacturer: str = "Maker") -> _ProductRow:
    return _ProductRow(manufacturer, product, "1", "App", "v1", "MV-07B0", "", "")


def test_the_table_is_as_wide_as_its_texts(context: None) -> None:
    app = cast("Any", SimpleNamespace(_product_widths=((0, 0.0), [])))
    app._product_column_widths = lambda rows: KnxGuiApp._product_column_widths(
        app, rows
    )
    short = [_row("Clock")]
    long = [_row("Switch Actuator 8-fold with current detection, 16 A, C-load")]
    narrow = KnxGuiApp._product_table_width(app, short)
    wide = KnxGuiApp._product_table_width(app, long)
    extra = imgui.calc_text_size(long[0].product).x - imgui.calc_text_size("Product").x
    # A free-text column takes its whole text.
    assert wide - narrow == pytest.approx(extra)
    # A short column only up to its limit.
    named = KnxGuiApp._product_table_width(app, [_row("Clock", "M" * 200)])
    assert named - narrow < imgui.calc_text_size("M" * 200).x

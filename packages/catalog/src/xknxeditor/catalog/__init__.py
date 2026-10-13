"""KNX hardware catalog backed by SQLite.

Two layers: a pure-Python core (``core``) that works on any SQLAlchemy session,
and an optional FastAPI HTTP layer (``http``) exposing the same data over REST.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

_LAZY: dict[str, tuple[str, str]] = {
    "ApplicationSummary": (".core.applications", "ApplicationSummary"),
    "CatalogSectionNode": (".core.catalog_sections", "CatalogSectionNode"),
    "ProductDeletion": (".core.delete", "ProductDeletion"),
    "CatalogService": (".core.service", "CatalogService"),
    "HardwareFilters": (".core.hardware", "HardwareFilters"),
    "ProductSummary": (".core.products", "ProductSummary"),
    "build_catalog_tree": (".core.catalog_sections", "build_catalog_tree"),
    "collect_section_ids": (".core.catalog_sections", "collect_section_ids"),
    "get_application_detail": (".core.applications", "get_application_detail"),
    "get_application_detail_by_id": (
        ".core.applications",
        "get_application_detail_by_id",
    ),
    "get_application_xml": (".core.applications", "get_application_xml"),
    "delete_product": (".core.delete", "delete_product"),
    "plan_product_deletion": (".core.delete", "plan_product_deletion"),
    "find_products_for_application": (
        ".core.products",
        "find_products_for_application",
    ),
    "get_hardware": (".core.hardware", "get_hardware"),
    "get_hardware_program": (".core.hardware", "get_hardware_program"),
    "get_manufacturer": (".core.manufacturers", "get_manufacturer"),
    "list_applications": (".core.applications", "list_applications"),
    "list_catalog_sections": (".core.catalog_sections", "list_catalog_sections"),
    "list_hardware": (".core.hardware", "list_hardware"),
    "list_manufacturers": (".core.manufacturers", "list_manufacturers"),
    "list_products": (".core.products", "list_products"),
    "upload_knxprod": (".core.upload", "upload_knxprod"),
    "default_db_url": (".db", "default_db_url"),
    "knxprod_dir_for": (".db", "knxprod_dir_for"),
    "make_engine": (".db", "make_engine"),
}

__all__ = [
    "ApplicationSummary",
    "CatalogSectionNode",
    "CatalogService",
    "HardwareFilters",
    "ProductDeletion",
    "ProductSummary",
    "build_catalog_tree",
    "collect_section_ids",
    "default_db_url",
    "delete_product",
    "find_products_for_application",
    "get_application_detail",
    "get_application_detail_by_id",
    "get_application_xml",
    "get_hardware",
    "get_hardware_program",
    "get_manufacturer",
    "knxprod_dir_for",
    "list_applications",
    "list_catalog_sections",
    "list_hardware",
    "list_manufacturers",
    "list_products",
    "make_engine",
    "plan_product_deletion",
    "upload_knxprod",
]

if TYPE_CHECKING:
    from .core.applications import (
        ApplicationSummary,
        get_application_detail,
        get_application_detail_by_id,
        get_application_xml,
        list_applications,
    )
    from .core.catalog_sections import (
        CatalogSectionNode,
        build_catalog_tree,
        collect_section_ids,
        list_catalog_sections,
    )
    from .core.delete import ProductDeletion, delete_product, plan_product_deletion
    from .core.hardware import (
        HardwareFilters,
        get_hardware,
        get_hardware_program,
        list_hardware,
    )
    from .core.manufacturers import get_manufacturer, list_manufacturers
    from .core.products import (
        ProductSummary,
        find_products_for_application,
        list_products,
    )
    from .core.service import CatalogService
    from .core.upload import upload_knxprod
    from .db import default_db_url, knxprod_dir_for, make_engine


def __getattr__(name: str) -> object:
    if name in _LAZY:
        import importlib

        module_path, attr = _LAZY[name]
        mod = importlib.import_module(module_path, __package__)
        value = getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

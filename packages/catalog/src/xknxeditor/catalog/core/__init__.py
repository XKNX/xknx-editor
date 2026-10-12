"""Session-level catalog queries and ingestion -- no FastAPI dependency."""

from __future__ import annotations

from typing import TYPE_CHECKING

_LAZY: dict[str, tuple[str, str]] = {
    "ApplicationSummary": (".applications", "ApplicationSummary"),
    "get_application_detail": (".applications", "get_application_detail"),
    "get_application_detail_by_id": (".applications", "get_application_detail_by_id"),
    "get_application_xml": (".applications", "get_application_xml"),
    "list_applications": (".applications", "list_applications"),
    "CatalogSectionNode": (".catalog_sections", "CatalogSectionNode"),
    "build_catalog_tree": (".catalog_sections", "build_catalog_tree"),
    "collect_section_ids": (".catalog_sections", "collect_section_ids"),
    "list_catalog_sections": (".catalog_sections", "list_catalog_sections"),
    "HardwareFilters": (".hardware", "HardwareFilters"),
    "get_hardware": (".hardware", "get_hardware"),
    "get_hardware_program": (".hardware", "get_hardware_program"),
    "list_hardware": (".hardware", "list_hardware"),
    "get_manufacturer": (".manufacturers", "get_manufacturer"),
    "list_manufacturers": (".manufacturers", "list_manufacturers"),
    "ProductSummary": (".products", "ProductSummary"),
    "list_products": (".products", "list_products"),
    "delete_product": (".delete", "delete_product"),
    "plan_product_deletion": (".delete", "plan_product_deletion"),
    "ProductDeletion": (".delete", "ProductDeletion"),
    "CatalogService": (".service", "CatalogService"),
    "upload_knxprod": (".upload", "upload_knxprod"),
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
    "delete_product",
    "get_application_detail",
    "get_application_detail_by_id",
    "get_application_xml",
    "get_hardware",
    "get_hardware_program",
    "get_manufacturer",
    "list_applications",
    "list_catalog_sections",
    "list_hardware",
    "list_manufacturers",
    "list_products",
    "plan_product_deletion",
    "upload_knxprod",
]

if TYPE_CHECKING:
    from .applications import (
        ApplicationSummary,
        get_application_detail,
        get_application_detail_by_id,
        get_application_xml,
        list_applications,
    )
    from .catalog_sections import (
        CatalogSectionNode,
        build_catalog_tree,
        collect_section_ids,
        list_catalog_sections,
    )
    from .delete import ProductDeletion, delete_product, plan_product_deletion
    from .hardware import (
        HardwareFilters,
        get_hardware,
        get_hardware_program,
        list_hardware,
    )
    from .manufacturers import get_manufacturer, list_manufacturers
    from .products import ProductSummary, list_products
    from .service import CatalogService
    from .upload import upload_knxprod


def __getattr__(name: str) -> object:
    if name in _LAZY:
        import importlib

        module_path, attr = _LAZY[name]
        mod = importlib.import_module(module_path, __package__)
        value = getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

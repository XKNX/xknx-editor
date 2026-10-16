"""High-level facade that owns the engine/store and wraps every core operation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from xknxeditor.catalog.core.applications import (
    ApplicationSummary,
    get_application_detail,
    get_application_detail_by_id,
    get_application_source,
    get_application_xml,
    get_replaces_versions,
    list_applications,
    load_application_program,
)
from xknxeditor.catalog.core.catalog_sections import (
    CatalogSectionNode,
    build_catalog_tree,
    collect_section_ids,
    list_catalog_sections,
)
from xknxeditor.catalog.core.delete import (
    ProductDeletion,
    delete_product,
    plan_product_deletion,
)
from xknxeditor.catalog.core.hardware import (
    HardwareFilters,
    ProgramSummary,
    get_hardware,
    get_hardware_program,
    get_program_application_id,
    get_program_source,
    is_program_without_application,
    list_hardware,
    list_hardware_programs,
)
from xknxeditor.catalog.core.manufacturers import get_manufacturer, list_manufacturers
from xknxeditor.catalog.core.products import (
    ProductSummary,
    find_products_for_application,
    list_products,
)
from xknxeditor.catalog.core.upload import upload_knxprod
from xknxeditor.catalog.db import knxprod_dir_for, make_engine
from xknxeditor.catalog.models import (
    CatalogSection,
    Hardware,
    HardwareProgram,
    Manufacturer,
)
from xknxeditor.prod import Application

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.application_program_t import (
        ApplicationProgram,
    )


class CatalogService:
    """Database-owning facade: one session per method call, no manual engine threading."""

    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        self._engine = make_engine(f"sqlite:///{db_path}")
        self._knxprod_dir = knxprod_dir_for(db_path)
        # Content-addressed cache for parsed application IR (see product.parse_cache), next to the
        # .knxprod store so it lives with the catalog it belongs to.
        self._parse_cache_dir = self._knxprod_dir.parent / "ir_cache"

    def close(self) -> None:
        """Release the database file (its pooled connections); later calls reconnect."""
        self._engine.dispose()

    # --- ingest -----------------------------------------------------------

    def import_knxprod(self, content: bytes, *, require_catalog: bool = True) -> Path:
        """Store and ingest .knxprod bytes (content-hash dedup). Returns the stored path.

        ``require_catalog=False`` ingests ``.knxproj``-embedded product data that omits
        ``Catalog.xml`` (hardware/applications still resolve devices)."""
        return upload_knxprod(
            content,
            self._knxprod_dir,
            self._engine,
            require_catalog=require_catalog,
            cache_dir=self._parse_cache_dir,
        )

    def delete_product(
        self, product_ref_id: str, hardware_program_id: str | None = None
    ) -> ProductDeletion | None:
        """Delete a product, or only its ``hardware_program_id`` version, and garbage-collect
        what it leaves unreferenced, including its ``.knxprod`` archives and parse-cache entries.
        ``None`` when the product is unknown."""
        return delete_product(
            self._engine,
            product_ref_id,
            hardware_program_id,
            cache_dir=self._parse_cache_dir,
        )

    def plan_product_deletion(
        self, product_ref_id: str, hardware_program_id: str | None = None
    ) -> ProductDeletion | None:
        """What :meth:`delete_product` would remove, without changing anything."""
        return plan_product_deletion(self._engine, product_ref_id, hardware_program_id)

    # --- manufacturers ----------------------------------------------------

    def list_manufacturers(self) -> Sequence[Manufacturer]:
        with Session(self._engine) as db:
            return list_manufacturers(db)

    def get_manufacturer(self, manufacturer_id: str) -> Manufacturer | None:
        with Session(self._engine) as db:
            return get_manufacturer(db, manufacturer_id)

    def manufacturer_catalog_status(self, manufacturer_id: str) -> tuple[bool, int]:
        """Diagnostics for a failed device resolution: ``(manufacturer present, hardware-program
        count)`` for ``manufacturer_id`` (e.g. ``M-0001``). Distinguishes 'product data never
        ingested' from 'ingested, but no program id matches the device'."""
        with Session(self._engine) as db:
            present = get_manufacturer(db, manufacturer_id) is not None
            count = (
                db.scalar(
                    select(func.count())
                    .select_from(HardwareProgram)
                    .join(Hardware, HardwareProgram.hardware_id == Hardware.id)
                    .where(Hardware.manufacturer_id == manufacturer_id)
                )
                or 0
            )
        return present, count

    # --- hardware ---------------------------------------------------------

    def list_hardware(
        self, filters: HardwareFilters | None = None
    ) -> Sequence[Hardware]:
        with Session(self._engine) as db:
            return list_hardware(db, filters)

    def get_hardware(self, hardware_id: str) -> Hardware | None:
        with Session(self._engine) as db:
            return get_hardware(db, hardware_id)

    def get_hardware_program(
        self, hardware_id: str, program_id: str
    ) -> HardwareProgram | None:
        with Session(self._engine) as db:
            return get_hardware_program(db, hardware_id, program_id)

    def coupler_capabilities(self, program_id: str) -> set[str]:
        """The coupler capabilities of a hardware program (e.g. ``SecurityProxy``)."""
        from xknxeditor.catalog.models import HardwareProgramCouplerCapability

        with Session(self._engine) as db:
            return {
                row.capability
                for row in db.query(HardwareProgramCouplerCapability).filter(
                    HardwareProgramCouplerCapability.hardware_program_id == program_id
                )
            }

    def get_application_xml(self, program_id: str) -> tuple[bytes, str] | None:
        with Session(self._engine) as db:
            return get_application_xml(db, program_id)

    def list_hardware_programs(self, hardware_id: str) -> list[ProgramSummary]:
        """Every program of a hardware with the application it loads."""
        with Session(self._engine) as db:
            return list_hardware_programs(db, hardware_id)

    def get_replaces_versions(self, program_id: str) -> list[int]:
        """The application versions the program's application can update."""
        with Session(self._engine) as db:
            return get_replaces_versions(db, program_id)

    def get_program_application_id(self, program_id: str) -> str | None:
        """The application id a hardware program loads, or ``None``."""
        with Session(self._engine) as db:
            return get_program_application_id(db, program_id)

    def is_program_without_application(self, program_id: str) -> bool:
        """Whether a known hardware program loads no application (e.g. a power supply's)."""
        with Session(self._engine) as db:
            return is_program_without_application(db, program_id)

    def load_application_program(
        self, application_id: str, language: str | None = None
    ) -> ApplicationProgram:
        """Parse the program of ``application_id`` on its own (any thread); raises
        :class:`LookupError` when it cannot be loaded."""
        with Session(self._engine) as db:
            return load_application_program(
                db, application_id, language, self._parse_cache_dir
            )

    def application_source(self, application_id: str) -> str | None:
        """What ``application_id`` is read from (stored product file and application id), the same
        for the same content on every machine; ``None`` when no program loads it."""
        with Session(self._engine) as db:
            return get_application_source(db, application_id)

    def has_application(self, application_id: str) -> bool:
        """Whether a hardware program loads ``application_id`` (so it can be resolved)."""
        with Session(self._engine) as db:
            return bool(
                db.scalar(
                    select(
                        exists().where(HardwareProgram.application_id == application_id)
                    )
                )
            )

    def get_program_source(self, program_id: str) -> tuple[str, str] | None:
        """Return ``(knxprod_path, manufacturer_id)`` for a program id, or ``None``."""
        with Session(self._engine) as db:
            return get_program_source(db, program_id)

    def get_application_detail(self, program_id: str) -> Application | None:
        with Session(self._engine) as db:
            return get_application_detail(db, program_id)

    # --- catalog sections -------------------------------------------------

    def list_catalog_sections(self, manufacturer_id: str) -> Sequence[CatalogSection]:
        with Session(self._engine) as db:
            return list_catalog_sections(db, manufacturer_id)

    def catalog_tree(self, manufacturer_id: str) -> list[CatalogSectionNode]:
        """Nested catalog-section tree for a manufacturer."""
        with Session(self._engine) as db:
            return build_catalog_tree(list_catalog_sections(db, manufacturer_id))

    def collect_section_ids(self, section_id: str) -> list[str]:
        with Session(self._engine) as db:
            return collect_section_ids(db, section_id)

    # --- applications -----------------------------------------------------

    def list_applications(self) -> list[ApplicationSummary]:
        """App-first listing of all applications with their manufacturer."""
        with Session(self._engine) as db:
            return list_applications(db)

    def list_products(self) -> list[ProductSummary]:
        """All orderable products with program/application refs."""
        with Session(self._engine) as db:
            return list_products(db)

    def get_application(
        self, application_id: str, language: str | None = None
    ) -> Application | None:
        """Resolve an application id to its parsed IR, or ``None``.

        ``language`` selects label locale. The XML parse result is
        content-addressed and cached on disk next to the .knxprod store."""
        with Session(self._engine) as db:
            return get_application_detail_by_id(
                db, application_id, language, cache_dir=self._parse_cache_dir
            )

    def find_products_for_application(
        self,
        *,
        manufacturer_id: str,
        application_number: int,
        application_version: int | None = None,
        mask_version: str | None = None,
    ) -> list[ProductSummary]:
        """Candidate products matching an application id read off a device."""
        with Session(self._engine) as db:
            return find_products_for_application(
                db,
                manufacturer_id=manufacturer_id,
                application_number=application_number,
                application_version=application_version,
                mask_version=mask_version,
            )

"""Product-first catalog queries (orderable items with their program/application refs)."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from xknxeditor.catalog.models import (
    Application,
    CatalogSectionProduct,
    Hardware,
    HardwareProgram,
    Manufacturer,
)


@dataclass
class ProductSummary:
    """Product refs (product_ref_id + hw2program) with display fields."""

    product_ref_id: str
    hardware2program_ref_id: str
    name: str | None
    order_number: str | None
    application_id: str | None
    manufacturer_id: str
    manufacturer_name: str | None
    application_version: int | None = None
    hardware_name: str | None = None
    application_name: str | None = None
    is_rail_mounted: bool | None = None
    width_mm: float | None = None
    bus_current: float | None = None


def list_products(db: Session) -> list[ProductSummary]:
    """All catalog items with a ProductRefId, joined to program/application/manufacturer."""
    rows = db.execute(
        select(
            CatalogSectionProduct.product_ref_id,
            CatalogSectionProduct.hardware_program_id,
            CatalogSectionProduct.name,
            Hardware.order_number,
            HardwareProgram.application_id,
            Manufacturer.id,
            Manufacturer.name,
            Application.application_version,
            Hardware.name,
            Application.name,
            Hardware.is_rail_mounted,
            Hardware.width_mm,
            Hardware.bus_current,
        )
        .join(
            HardwareProgram,
            HardwareProgram.id == CatalogSectionProduct.hardware_program_id,
        )
        .join(Hardware, Hardware.id == HardwareProgram.hardware_id)
        .join(Manufacturer, Manufacturer.id == Hardware.manufacturer_id)
        .join(
            Application, Application.id == HardwareProgram.application_id, isouter=True
        )
        .where(CatalogSectionProduct.product_ref_id.is_not(None))
        .distinct()
    ).all()
    return [
        ProductSummary(
            product_ref_id=product_ref_id,
            hardware2program_ref_id=hardware_program_id,
            name=name,
            order_number=order_number,
            application_id=application_id,
            manufacturer_id=manufacturer_id,
            manufacturer_name=manufacturer_name,
            application_version=application_version,
            hardware_name=hardware_name,
            application_name=application_name,
            is_rail_mounted=is_rail_mounted,
            width_mm=width_mm,
            bus_current=bus_current,
        )
        for (
            product_ref_id,
            hardware_program_id,
            name,
            order_number,
            application_id,
            manufacturer_id,
            manufacturer_name,
            application_version,
            hardware_name,
            application_name,
            is_rail_mounted,
            width_mm,
            bus_current,
        ) in rows
        if product_ref_id is not None
    ]


def find_products_for_application(
    db: Session,
    *,
    manufacturer_id: str,
    application_number: int,
    application_version: int | None = None,
    mask_version: str | None = None,
) -> list[ProductSummary]:
    """Candidate products that run an application matching an id read off a device.

    A device only exposes its manufacturer, application number and application
    version (the 5-octet application program id), never a hardware/product ref.
    This resolves that triple back to the orderable products a project device can
    reference. When ``application_version`` is given only exact-version matches are
    returned; pass ``None`` to match on manufacturer and application number alone
    (useful when the installed version is no longer in the catalog).
    ``mask_version`` (e.g. ``"MV-0705"``) additionally constrains the mask version,
    so a product for a different device model is not offered.
    """
    query = (
        select(
            CatalogSectionProduct.product_ref_id,
            CatalogSectionProduct.hardware_program_id,
            CatalogSectionProduct.name,
            Hardware.order_number,
            HardwareProgram.application_id,
            Manufacturer.id,
            Manufacturer.name,
            Hardware.name,
            Application.application_version,
            Application.name,
        )
        .join(
            HardwareProgram,
            HardwareProgram.id == CatalogSectionProduct.hardware_program_id,
        )
        .join(Hardware, Hardware.id == HardwareProgram.hardware_id)
        .join(Manufacturer, Manufacturer.id == Hardware.manufacturer_id)
        .join(Application, Application.id == HardwareProgram.application_id)
        .where(
            CatalogSectionProduct.product_ref_id.is_not(None),
            Manufacturer.id == manufacturer_id,
            Application.application_number == application_number,
        )
        .distinct()
    )
    if application_version is not None:
        query = query.where(Application.application_version == application_version)
    if mask_version is not None:
        query = query.where(Application.mask_version == mask_version)
    rows = db.execute(query).all()
    return [
        ProductSummary(
            product_ref_id=product_ref_id,
            hardware2program_ref_id=hardware_program_id,
            name=name,
            order_number=order_number,
            application_id=application_id,
            manufacturer_id=row_manufacturer_id,
            manufacturer_name=manufacturer_name,
            hardware_name=hardware_name,
            application_version=version,
            application_name=application_name,
        )
        for (
            product_ref_id,
            hardware_program_id,
            name,
            order_number,
            application_id,
            row_manufacturer_id,
            manufacturer_name,
            hardware_name,
            version,
            application_name,
        ) in rows
        if product_ref_id is not None
    ]

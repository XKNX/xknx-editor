"""Hardware listing, filtering, and program lookups."""

import base64
import datetime
import io
from collections.abc import Sequence
from dataclasses import dataclass
from xml.etree import ElementTree

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from xknxeditor.catalog.core.catalog_sections import collect_section_ids
from xknxeditor.catalog.models import (
    Application,
    CatalogSection,
    CatalogSectionProduct,
    Hardware,
    HardwareProgram,
    HardwareProgramMediumType,
)


class HardwareFilters(BaseModel):
    """Pydantic filter bag for ``list_hardware``. All fields are optional; lists are OR-matched,
    scalars are AND-matched. Doubles as a FastAPI ``Query()`` model.
    """

    manufacturer_id: list[str] = Field(default_factory=list)
    """M-XXXX ids to match (OR)."""
    medium_type: list[str] = Field(default_factory=list)
    """Medium types to match, e.g. TP, IP, RF (OR)."""
    section_id: str | None = None
    """Section id; includes descendants."""
    is_secure_enabled: bool | None = None
    is_rail_mounted: bool | None = None
    is_coupler: bool | None = None
    is_power_supply: bool | None = None
    is_ip_enabled: bool | None = None
    has_application_program: bool | None = None
    no_download_without_plugin: bool | None = None
    registration_status: str | None = None
    registration_number: str | None = None
    registration_date_from: datetime.date | None = None
    registration_date_to: datetime.date | None = None
    mask_version: str | None = None
    search: str | None = None
    """Substring search on name/order_number (case-insensitive)."""
    limit: int = 50
    offset: int = 0


def _base_query():
    """Base select with eager-loaded programs, medium types, and application."""
    return select(Hardware).options(
        selectinload(Hardware.programs).selectinload(HardwareProgram.medium_types),
        selectinload(Hardware.programs).selectinload(HardwareProgram.application),
    )


def list_hardware(
    db: Session, filters: HardwareFilters | None = None
) -> Sequence[Hardware]:
    """Query hardware with optional filters; relationships are eager-loaded."""
    if filters is None:
        filters = HardwareFilters()

    q = _base_query()

    needs_program_join = bool(
        filters.medium_type
        or filters.is_secure_enabled is not None
        or filters.mask_version is not None
        or filters.registration_status is not None
        or filters.registration_number is not None
        or filters.registration_date_from is not None
        or filters.registration_date_to is not None
        or filters.section_id is not None
    )
    if needs_program_join:
        q = q.join(Hardware.programs)

    if filters.manufacturer_id:
        q = q.where(Hardware.manufacturer_id.in_(filters.manufacturer_id))
    if filters.is_rail_mounted is not None:
        q = q.where(Hardware.is_rail_mounted == filters.is_rail_mounted)
    if filters.is_coupler is not None:
        q = q.where(Hardware.is_coupler == filters.is_coupler)
    if filters.is_power_supply is not None:
        q = q.where(Hardware.is_power_supply == filters.is_power_supply)
    if filters.is_ip_enabled is not None:
        q = q.where(Hardware.is_ip_enabled == filters.is_ip_enabled)
    if filters.has_application_program is not None:
        q = q.where(Hardware.has_application_program == filters.has_application_program)
    if filters.no_download_without_plugin is not None:
        q = q.where(
            Hardware.no_download_without_plugin == filters.no_download_without_plugin
        )
    if filters.search:
        term = f"%{filters.search}%"
        q = q.where(Hardware.name.ilike(term) | Hardware.order_number.ilike(term))
    if filters.medium_type:
        q = q.join(HardwareProgram.medium_types).where(
            HardwareProgramMediumType.medium_type.in_(filters.medium_type)
        )
    if filters.is_secure_enabled is not None or filters.mask_version is not None:
        q = q.join(HardwareProgram.application, isouter=True)
        if filters.is_secure_enabled is not None:
            q = q.where(Application.is_secure_enabled == filters.is_secure_enabled)
        if filters.mask_version is not None:
            q = q.where(Application.mask_version == filters.mask_version)
    if filters.registration_status is not None:
        q = q.where(HardwareProgram.registration_status == filters.registration_status)
    if filters.registration_number is not None:
        q = q.where(HardwareProgram.registration_number == filters.registration_number)
    if filters.registration_date_from is not None:
        q = q.where(HardwareProgram.registration_date >= filters.registration_date_from)
    if filters.registration_date_to is not None:
        q = q.where(HardwareProgram.registration_date <= filters.registration_date_to)
    if filters.section_id is not None:
        ids = collect_section_ids(db, filters.section_id)
        q = (
            q.join(
                CatalogSectionProduct,
                CatalogSectionProduct.hardware_program_id == HardwareProgram.id,
            )
            .join(CatalogSection, CatalogSection.id == CatalogSectionProduct.section_id)
            .where(CatalogSection.id.in_(ids))
        )

    q = q.distinct().offset(filters.offset).limit(filters.limit)
    return db.scalars(q).unique().all()


def get_hardware(db: Session, hardware_id: str) -> Hardware | None:
    """Single hardware by id with eager-loaded programs, or ``None``."""
    return db.scalars(_base_query().where(Hardware.id == hardware_id)).first()


def get_hardware_program(
    db: Session, hardware_id: str, program_id: str
) -> HardwareProgram | None:
    """Fetch a program scoped to its hardware (both ids must match), or ``None``."""
    return db.scalars(
        select(HardwareProgram)
        .options(selectinload(HardwareProgram.hardware))
        .where(
            HardwareProgram.id == program_id,
            HardwareProgram.hardware_id == hardware_id,
        )
    ).first()


@dataclass(frozen=True)
class ProgramSummary:
    """A hardware program with the application it loads."""

    id: str
    hardware_id: str
    application_id: str | None
    application_name: str | None
    application_number: int | None
    application_version: int | None
    registration_status: str | None


def list_hardware_programs(db: Session, hardware_id: str) -> list[ProgramSummary]:
    """Every program of a hardware, with or without a catalog item."""
    rows = db.execute(
        select(
            HardwareProgram.id,
            HardwareProgram.hardware_id,
            HardwareProgram.application_id,
            Application.name,
            Application.application_number,
            Application.application_version,
            HardwareProgram.registration_status,
        )
        .join(
            Application, Application.id == HardwareProgram.application_id, isouter=True
        )
        .where(HardwareProgram.hardware_id == hardware_id)
        .order_by(HardwareProgram.id)
    ).all()
    return [ProgramSummary(*row) for row in rows]


def get_program_application_id(db: Session, program_id: str) -> str | None:
    """The application id a hardware program loads, or ``None`` if the program is unknown.

    Independent of catalog items: a program resolves even when no catalog entry lists it.
    """
    return db.scalars(
        select(HardwareProgram.application_id).where(HardwareProgram.id == program_id)
    ).first()


def is_program_without_application(db: Session, program_id: str) -> bool:
    """Whether ``program_id`` is a known hardware program that loads no application, as the
    program of a product without one (e.g. a power supply)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(HardwareProgram)
            .where(
                HardwareProgram.id == program_id,
                HardwareProgram.application_id.is_(None),
            )
        )
        or 0
    ) > 0


def get_program_source(db: Session, program_id: str) -> tuple[str, str] | None:
    """Return ``(knxprod_path, manufacturer_id)`` for a hardware program, or ``None``.

    Resolves a program by its id alone (as stored on a project device's
    ``hardware2program_ref_id``) to the on-disk ``.knxprod`` it was imported from and its
    manufacturer, so callers can re-extract the manufacturer XMLs from that archive.

    Args:
      db: An active SQLAlchemy session.
      program_id: The hardware program's primary-key identifier.

    Returns:
      A ``(knxprod_path, manufacturer_id)`` tuple, or ``None`` if the program is unknown.
    """
    row = db.execute(
        select(HardwareProgram.knxprod_path, Hardware.manufacturer_id)
        .join(Hardware, Hardware.id == HardwareProgram.hardware_id)
        .where(HardwareProgram.id == program_id)
    ).first()
    if row is None:
        return None
    return row[0], row[1]


@dataclass(frozen=True)
class FactoryData:
    """What devices of a hardware program leave the factory with, base64 decoded: the image
    (``Hardware2Program/@LoadedImage``) and the stamp they show in that state (``@CheckSums``)."""

    image: bytes | None
    stamp: bytes | None = None


def get_factory_image(db: Session, program_id: str) -> FactoryData | None:
    """The image devices of a hardware program leave the factory with, and its stamp, or
    ``None`` when the product has no image.

    Read from the hardware XML of the ``.knxprod`` the program was imported from.
    """
    source = get_program_source(db, program_id)
    if source is None:
        return None
    knxprod_path, manufacturer_id = source

    from xknxeditor.prod.archive import Archive
    from xknxeditor.prod.errors import ArchiveError

    try:
        with Archive(knxprod_path, require_catalog=False) as archive:
            hardware_xml = archive.get_hardware_xml(manufacturer_id)
    except (ArchiveError, KeyError, OSError):
        return None
    for _, element in ElementTree.iterparse(io.BytesIO(hardware_xml)):
        if element.tag.rpartition("}")[2] != "Hardware2Program":
            continue
        if element.get("Id") == program_id:
            image = element.get("LoadedImage")
            if not image:
                return None
            stamp = element.get("CheckSums")
            return FactoryData(
                base64.b64decode(image), base64.b64decode(stamp) if stamp else None
            )
        element.clear()
    return None

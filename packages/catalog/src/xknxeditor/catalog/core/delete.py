"""Delete a product from the catalog and garbage-collect what it leaves unreferenced."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, exists, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from xknxeditor.catalog.models import (
    Application,
    CatalogSection,
    CatalogSectionProduct,
    Hardware,
    HardwareProgram,
    HardwareProgramMediumType,
    Manufacturer,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductDeletion:
    """What deleting one product removes (or would remove) from the catalog."""

    product_ref_id: str
    removed_programs: list[str]
    # Application id of each removed program (None when it had none).
    program_applications: dict[str, str | None]
    removed_applications: list[str]
    removed_hardware: list[str]
    removed_sections: list[str]
    removed_manufacturers: list[str]
    removed_files: list[str]


@dataclass(frozen=True)
class _Program:
    id: str
    hardware_id: str
    application_id: str | None
    knxprod_path: str
    manufacturer_id: str


def _delete_rows(
    session: Session, product_ref_id: str, hardware_program_id: str | None = None
) -> tuple[ProductDeletion, list[_Program]] | None:
    """Delete the product's rows and everything left unreferenced, without committing."""
    csp = CatalogSectionProduct
    selected = csp.product_ref_id == product_ref_id
    if hardware_program_id is not None:
        selected &= csp.hardware_program_id == hardware_program_id
    rows = session.execute(
        select(csp.section_id, csp.hardware_program_id).where(selected)
    ).all()
    if not rows:
        return None
    section_ids = {section_id for section_id, _ in rows}
    program_ids = {program_id for _, program_id in rows}
    session.execute(delete(csp).where(selected))

    # Hardware programs no longer bound to any catalog item.
    still_bound = set(
        session.scalars(
            select(csp.hardware_program_id).where(
                csp.hardware_program_id.in_(program_ids)
            )
        )
    )
    programs = [
        _Program(*row)
        for row in session.execute(
            select(
                HardwareProgram.id,
                HardwareProgram.hardware_id,
                HardwareProgram.application_id,
                HardwareProgram.knxprod_path,
                Hardware.manufacturer_id,
            )
            .join(Hardware, Hardware.id == HardwareProgram.hardware_id)
            .where(HardwareProgram.id.in_(program_ids - still_bound))
        ).all()
    ]
    removed_program_ids = [p.id for p in programs]
    session.execute(
        delete(HardwareProgramMediumType).where(
            HardwareProgramMediumType.hardware_program_id.in_(removed_program_ids)
        )
    )
    session.execute(
        delete(HardwareProgram).where(HardwareProgram.id.in_(removed_program_ids))
    )

    # Hardware without programs.
    hardware_ids = {p.hardware_id for p in programs}
    removed_hardware = list(
        session.scalars(
            select(Hardware.id).where(
                Hardware.id.in_(hardware_ids),
                ~exists().where(HardwareProgram.hardware_id == Hardware.id),
            )
        )
    )
    session.execute(delete(Hardware).where(Hardware.id.in_(removed_hardware)))

    # Applications without programs.
    app_ids = {p.application_id for p in programs if p.application_id is not None}
    removed_apps = list(
        session.scalars(
            select(Application.id).where(
                Application.id.in_(app_ids),
                ~exists().where(HardwareProgram.application_id == Application.id),
            )
        )
    )
    session.execute(delete(Application).where(Application.id.in_(removed_apps)))

    # Sections with no products and no child sections, walking up the parents.
    removed_sections: list[str] = []
    section_manufacturers: set[str] = set()
    pending = set(section_ids)
    while pending:
        next_pending: set[str] = set()
        for section_id in sorted(pending):
            section = session.execute(
                select(CatalogSection.manufacturer_id, CatalogSection.parent_id).where(
                    CatalogSection.id == section_id
                )
            ).first()
            if section is None:
                continue
            has_products = session.scalar(
                select(exists().where(csp.section_id == section_id))
            )
            has_children = session.scalar(
                select(exists().where(CatalogSection.parent_id == section_id))
            )
            if has_products or has_children:
                continue
            removed_sections.append(section_id)
            section_manufacturers.add(section.manufacturer_id)
            if section.parent_id is not None:
                next_pending.add(section.parent_id)
            session.execute(
                delete(CatalogSection).where(CatalogSection.id == section_id)
            )
        pending = next_pending

    # Manufacturers with no hardware and no sections left.
    manufacturer_ids = {
        p.manufacturer_id for p in programs if p.hardware_id in removed_hardware
    } | section_manufacturers
    removed_manufacturers = list(
        session.scalars(
            select(Manufacturer.id).where(
                Manufacturer.id.in_(manufacturer_ids),
                ~exists().where(Hardware.manufacturer_id == Manufacturer.id),
                ~exists().where(CatalogSection.manufacturer_id == Manufacturer.id),
            )
        )
    )
    session.execute(
        delete(Manufacturer).where(Manufacturer.id.in_(removed_manufacturers))
    )

    # Archives no hardware program references any more.
    paths = {p.knxprod_path for p in programs}
    still_used = set(
        session.scalars(
            select(HardwareProgram.knxprod_path).where(
                HardwareProgram.knxprod_path.in_(paths)
            )
        )
    )
    deletion = ProductDeletion(
        product_ref_id=product_ref_id,
        removed_programs=sorted(removed_program_ids),
        program_applications={p.id: p.application_id for p in programs},
        removed_applications=sorted(removed_apps),
        removed_hardware=sorted(removed_hardware),
        removed_sections=removed_sections,
        removed_manufacturers=sorted(removed_manufacturers),
        removed_files=sorted(paths - still_used),
    )
    return deletion, programs


def plan_product_deletion(
    engine: Engine, product_ref_id: str, hardware_program_id: str | None = None
) -> ProductDeletion | None:
    """What :func:`delete_product` would remove, without changing anything.

    Returns ``None`` when no catalog item has ``product_ref_id``."""
    with Session(engine) as session:
        try:
            result = _delete_rows(session, product_ref_id, hardware_program_id)
        finally:
            session.rollback()
    return None if result is None else result[0]


def _application_digests(
    programs: list[_Program], removed_apps: list[str]
) -> list[str]:
    """Parse-cache keys (SHA-256 of the application XML) of the removed applications."""
    from xknxeditor.prod.archive import Archive

    digests: list[str] = []
    for app_id in removed_apps:
        program = next(p for p in programs if p.application_id == app_id)
        try:
            with Archive(program.knxprod_path, require_catalog=False) as archive:
                xml = archive.get_application_xml(program.manufacturer_id, app_id)
        except Exception as exc:
            logger.debug("parse cache key not readable: %s: %s", app_id, exc)
            continue
        if xml is not None:
            digests.append(hashlib.sha256(xml).hexdigest())
    return digests


def delete_product(
    engine: Engine,
    product_ref_id: str,
    hardware_program_id: str | None = None,
    *,
    cache_dir: Path | None = None,
) -> ProductDeletion | None:
    """Delete one product (every catalog item with ``product_ref_id``) in a single transaction.

    With ``hardware_program_id`` only the catalog items of that hardware program, i.e. one
    application version of the product, are deleted.

    Removes what is left unreferenced: hardware programs bound to no catalog item (with their medium
    types), hardware without programs, applications without programs, empty catalog sections up the
    tree, and manufacturers with neither hardware nor sections. Only rows reached from the deleted
    product are considered, so programs ingested without a catalog item (project product data) stay.
    After the commit, unreferenced ``.knxprod`` archives and the removed applications' parse-cache
    entries in ``cache_dir`` are deleted and the database is vacuumed.

    Returns ``None`` (and changes nothing) when no catalog item has ``product_ref_id``."""
    with Session(engine) as session:
        result = _delete_rows(session, product_ref_id, hardware_program_id)
        if result is None:
            return None
        deletion, programs = result
        # Read the cache keys before the archives are deleted.
        digests = (
            _application_digests(programs, deletion.removed_applications)
            if cache_dir is not None
            else []
        )
        session.commit()

    for path in deletion.removed_files:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not delete archive %s: %s", path, exc)
    if cache_dir is not None:
        for digest in digests:
            for entry in cache_dir.glob(f"ir_*_{digest}.pkl"):
                try:
                    entry.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("could not delete parse cache %s: %s", entry, exc)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("VACUUM")
    except OperationalError as exc:
        logger.warning("catalog vacuum failed: %s", exc)
    return deletion

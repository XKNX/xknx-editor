"""Ingest .knxprod archives into the catalog DB via the product Registry."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from xknxeditor.catalog.models import (
    Application,
    CatalogSection,
    CatalogSectionProduct,
    Hardware,
    HardwareProgram,
    HardwareProgramCouplerCapability,
    HardwareProgramMediumType,
    Manufacturer,
)
from xknxeditor.prod import DeviceProgram, Registry, load

logger = logging.getLogger(__name__)


def _ingest_manufacturers(session: Session, reg: Registry) -> None:
    names = reg.master.manufacturers
    mids = set(reg.manufacturer_to_hardware) | set(reg.manufacturer_to_section)
    for mid in mids:
        session.merge(Manufacturer(id=mid, name=names.get(mid)))


def _ingest_applications(session: Session, reg: Registry) -> None:
    for app in reg.applications.values():
        program = app.program
        session.merge(
            Application(
                id=app.id,
                name=app.name,
                application_number=program.application_number,
                application_version=program.application_version,
                mask_version=program.mask_version,
                is_secure_enabled=bool(program.is_secure_enabled),
            )
        )


def _ingest_hardware(
    session: Session, reg: Registry, knxprod_path: str, project_mode: bool
) -> None:
    known_app_ids = set(reg.applications)
    for mfr_id, hardware_ids in reg.manufacturer_to_hardware.items():
        for hardware_id in hardware_ids:
            hw = reg.hardware[hardware_id]
            raw = hw.raw
            products = list(reg.products_for_hardware(hardware_id).values())
            product = products[0] if products else None
            session.merge(
                Hardware(
                    id=hw.id,
                    manufacturer_id=mfr_id,
                    name=product.name if product else hw.name,
                    order_number=product.order_number if product else None,
                    is_rail_mounted=product.rail_mounted if product else None,
                    width_mm=product.width_mm if product else None,
                    description=product.raw.visible_description if product else None,
                    default_language=product.raw.default_language if product else None,
                    serial_number=raw.serial_number,
                    version_number=raw.version_number,
                    bus_current=raw.bus_current,
                    has_application_program=raw.has_application_program,
                    is_coupler=raw.is_coupler,
                    is_power_supply=raw.is_power_supply,
                    is_ip_enabled=raw.is_ipenabled,
                    no_download_without_plugin=raw.no_download_without_plugin,
                )
            )
            for program in reg.programs_for_hardware(hardware_id).values():
                _ingest_program(
                    session, hw.id, program, knxprod_path, known_app_ids, project_mode
                )


def _ingest_program(
    session: Session,
    hardware_id: str,
    program: DeviceProgram,
    knxprod_path: str,
    known_app_ids: set[str],
    project_mode: bool,
) -> None:
    # Only reference an application that was actually ingested. Project-embedded product data
    # (.knxproj) ships Hardware.xml listing every program but only the application XMLs the project
    # uses, so a program may name an absent app; writing that id would violate the FK
    # (applications.id) and roll back the whole ingest.
    app_id = next((a for a in program.application_ref_ids if a in known_app_ids), None)

    if project_mode and session.get(HardwareProgram, program.id) is not None:
        # Already ingested (e.g. from a full .knxprod carrying Catalog.xml + baggages). A leaner
        # project archive must not overwrite it: that would downgrade the source path and could null
        # a resolved application_id. Project-mode ingest only ADDS programs that are missing.
        return

    reg_status = reg_number = reg_date = None
    info = program.raw.registration_info
    if info is not None:
        reg_status = info.registration_status.value
        reg_number = info.registration_number
        reg_date = info.registration_date.to_date() if info.registration_date else None

    session.merge(
        HardwareProgram(
            id=program.id,
            hardware_id=hardware_id,
            application_id=app_id,
            knxprod_path=knxprod_path,
            registration_status=reg_status,
            registration_number=reg_number,
            registration_date=reg_date,
        )
    )
    for medium_type in program.raw.medium_types or []:
        session.merge(
            HardwareProgramMediumType(
                hardware_program_id=program.id, medium_type=medium_type
            )
        )
    for capability in program.raw.coupler_capabilities or []:
        session.merge(
            HardwareProgramCouplerCapability(
                hardware_program_id=program.id,
                capability=str(getattr(capability, "value", capability)),
            )
        )


def _ingest_catalog(session: Session, reg: Registry) -> None:
    for mfr_id, section_ids in reg.manufacturer_to_section.items():
        for section_id in section_ids:
            section = reg.catalog_sections[section_id]
            session.merge(
                CatalogSection(
                    id=section.id,
                    manufacturer_id=mfr_id,
                    parent_id=section.parent_id,
                    name=section.name or section.id,
                    number=section.number,
                )
            )
            for item in reg.items_for_section(section_id).values():
                if item.hardware2_program_ref_id:
                    session.merge(
                        CatalogSectionProduct(
                            id=item.id,
                            section_id=section.id,
                            hardware_program_id=item.hardware2_program_ref_id,
                            product_ref_id=item.product_ref_id,
                            name=item.name,
                        )
                    )


def upload_knxprod(
    content: bytes,
    dest_dir: Path,
    engine: Engine,
    *,
    require_catalog: bool = True,
    cache_dir: Path | None = None,
) -> Path:
    """Ingest .knxprod bytes into the database behind ``engine``. Returns the path where the file was saved.

    ``require_catalog=False`` ingests product data embedded in a ``.knxproj`` (which omits
    ``Catalog.xml``): hardware and applications are stored so devices resolve, only the catalog
    browse index is absent for those manufacturers.

    Idempotent: skips the ingest only when this content is already IN THE DATABASE (a hardware
    program referencing its stored path exists), not merely when the stored file is present. This
    keeps the store file and the DB in sync — e.g. if the catalog DB was deleted/rebuilt while the
    file cache remained, re-importing repopulates the DB instead of wrongly skipping. The stored
    file is (re)written whenever it is missing, since resolution reads it at runtime by path.
    """
    path = dest_dir / f"{hashlib.sha3_256(content).hexdigest()}.knxprod"
    with Session(engine) as session:
        in_db = (
            session.query(HardwareProgram).filter_by(knxprod_path=str(path)).first()
            is not None
        )
        if not in_db:
            registry = load(
                content, require_catalog=require_catalog, cache_dir=cache_dir
            )
            logger.debug(
                "catalog import: %d bytes, %d manufacturers, %d hardware",
                len(content),
                len(set(registry.manufacturer_to_hardware)),
                len(registry.hardware),
            )
            _ingest_manufacturers(session, registry)
            _ingest_applications(session, registry)
            _ingest_hardware(
                session, registry, str(path), project_mode=not require_catalog
            )
            _ingest_catalog(session, registry)
            # Write the file BEFORE committing so a committed row never references a missing
            # archive (resolution reads it by path). A stray file from a failed commit is
            # harmless: dedup is DB-driven, so the next import re-ingests it.
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            session.commit()
            logger.debug("catalog import committed: %s", path.name)
        elif not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        else:
            logger.debug("catalog import: already in db, skipping ingest")
    return path

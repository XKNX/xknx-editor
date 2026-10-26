"""App-first queries: flat application listings and XML/detail resolution by program or app id."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from xknxeditor.catalog.models import (
    Application,
    Hardware,
    HardwareProgram,
    Manufacturer,
)

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.application_program_t import (
        ApplicationProgram,
    )
    from xknxeditor.prod import Application as ProductApplication

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplicationSummary:
    """Application plus its shipping manufacturer, for app-first browsing."""

    application_id: str
    name: str
    manufacturer_id: str
    manufacturer_name: str | None


def list_applications(db: Session) -> list[ApplicationSummary]:
    """All applications joined to their manufacturer through hardware programs."""
    rows = db.execute(
        select(Application.id, Application.name, Manufacturer.id, Manufacturer.name)
        .join(HardwareProgram, HardwareProgram.application_id == Application.id)
        .join(Hardware, Hardware.id == HardwareProgram.hardware_id)
        .join(Manufacturer, Manufacturer.id == Hardware.manufacturer_id)
        .distinct()
    ).all()
    return [
        ApplicationSummary(
            application_id=app_id,
            name=name,
            manufacturer_id=mfr_id,
            manufacturer_name=mfr_name,
        )
        for app_id, name, mfr_id, mfr_name in rows
    ]


def get_application_xml(db: Session, program_id: str) -> tuple[bytes, str] | None:
    """Extract application XML from the program's .knxprod archive.

    Returns ``(xml_bytes, manufacturer_id)`` or ``None`` when the program,
    application, or XML entry is missing.
    """
    program = db.scalars(
        select(HardwareProgram)
        .options(selectinload(HardwareProgram.hardware))
        .where(HardwareProgram.id == program_id)
    ).first()
    if not program or not program.application_id:
        return None

    from xknxeditor.prod.archive import Archive

    manufacturer_id = program.hardware.manufacturer_id
    # Reads only the application XML, never Catalog.xml, so tolerate archives that omit it
    # (product data embedded in a .knxproj — see upload_knxprod require_catalog).
    archive = Archive(program.knxprod_path, require_catalog=False)
    with archive:
        xml_bytes = archive.get_application_xml(manufacturer_id, program.application_id)
        if xml_bytes is None:
            logger.debug(
                "application XML not in archive: program=%s app=%s archive=%s",
                program_id,
                program.application_id,
                program.knxprod_path,
            )
            return None
        return xml_bytes, manufacturer_id


def _application_attributes(db: Session, program_id: str) -> dict[str, str] | None:
    """The attributes of the ``ApplicationProgram`` element a program loads, read without
    parsing the rest of its XML; ``None`` when unknown."""
    source = get_application_xml(db, program_id)
    if source is None:
        return None
    from io import BytesIO
    from xml.etree.ElementTree import iterparse

    for _, element in iterparse(BytesIO(source[0]), events=("start",)):
        if element.tag.rsplit("}", 1)[-1] == "ApplicationProgram":
            return dict(element.attrib)
    return None


def get_replaces_versions(db: Session, program_id: str) -> list[int]:
    """The ``ReplacesVersions`` of the application a program loads (empty when unknown)."""
    attributes = _application_attributes(db, program_id) or {}
    return [int(v) for v in attributes.get("ReplacesVersions", "").split()]


def get_ip_config(db: Session, program_id: str) -> str | None:
    """Who sets the IP configuration of the device the program loads (``IPConfig``): "Tool"
    (the default) or "Custom" (the application itself); ``None`` when unknown."""
    attributes = _application_attributes(db, program_id)
    return None if attributes is None else attributes.get("IPConfig", "Tool")


def get_application_detail(
    db: Session,
    program_id: str,
    language: str | None = None,
    cache_dir: Path | None = None,
) -> ProductApplication | None:
    """Parse and return the full application IR for a hardware program.

    Returns ``None`` when the program, application link, or archive entry is
    absent. ``language`` selects label locale; ``cache_dir`` enables on-disk
    caching of the expensive parse.
    """
    program = db.scalars(
        select(HardwareProgram)
        .options(selectinload(HardwareProgram.hardware))
        .where(HardwareProgram.id == program_id)
    ).first()
    if not program or not program.application_id:
        return None

    manufacturer_id = program.hardware.manufacturer_id
    app = _load_application(
        program.knxprod_path,
        manufacturer_id,
        program.application_id,
        language,
        cache_dir,
    )
    if app is not None:
        knxprod_path, application_id = program.knxprod_path, program.application_id

        def reload() -> ApplicationProgram:
            again = _load_application(
                knxprod_path, manufacturer_id, application_id, language, cache_dir
            )
            if again is None:
                raise LookupError(
                    f"application {application_id} is no longer available"
                )
            return again.program

        app.set_reloader(reload)
    return app


def _load_application(
    knxprod_path: str,
    manufacturer_id: str,
    application_id: str,
    language: str | None,
    cache_dir: Path | None,
) -> ProductApplication | None:
    from xknxeditor.prod import parse_application_xml
    from xknxeditor.prod.archive import Archive

    # Only the application XML is read (not Catalog.xml), so tolerate project-embedded archives.
    with Archive(knxprod_path, require_catalog=False) as archive:
        xml_bytes = archive.get_application_xml(manufacturer_id, application_id)
    if xml_bytes is None:
        logger.debug(
            "application XML not in archive: app=%s manufacturer=%s archive=%s",
            application_id,
            manufacturer_id,
            knxprod_path,
        )
        return None
    apps = parse_application_xml(
        xml_bytes, manufacturer_id, language, cache_dir=cache_dir
    )
    return next((a for a in apps if a.id == application_id), None)


def get_application_detail_by_id(
    db: Session,
    application_id: str,
    language: str | None = None,
    cache_dir: Path | None = None,
) -> ProductApplication | None:
    """Resolve an application id to its parsed IR via any referencing hardware program.

    ``language`` (e.g. "de-DE" or "de") localizes labels when the archive carries
    that locale. Returns ``None`` when no program references the id.
    """
    program = _application_program(db, application_id)
    if program is None:
        return None
    return get_application_detail(db, program.id, language, cache_dir)


def _application_program(db: Session, application_id: str) -> HardwareProgram | None:
    """The hardware program an application id is resolved through (the same one every time)."""
    return db.scalars(
        select(HardwareProgram)
        .where(HardwareProgram.application_id == application_id)
        .order_by(HardwareProgram.id)
        .limit(1)
    ).first()


def load_application_program(
    db: Session,
    application_id: str,
    language: str | None = None,
    cache_dir: Path | None = None,
) -> ApplicationProgram:
    """Parse the program of ``application_id`` (as :func:`get_application_detail_by_id` resolves
    it), without keeping it among the applications held in memory; raises :class:`LookupError`
    when it cannot be loaded."""
    program = _application_program(db, application_id)
    if program is None:
        raise LookupError(f"application {application_id} is not in the catalog")
    app = _load_application(
        program.knxprod_path,
        program.hardware.manufacturer_id,
        application_id,
        language,
        cache_dir,
    )
    if app is None:
        raise LookupError(f"application {application_id} could not be loaded")
    return app.program


def get_application_source(db: Session, application_id: str) -> str | None:
    """What the application of ``application_id`` is read from: the stored product file (named by
    its content hash) and the application id. ``None`` when no program references the id."""
    program = _application_program(db, application_id)
    if program is None:
        return None
    return f"{Path(program.knxprod_path).name}|{application_id}"

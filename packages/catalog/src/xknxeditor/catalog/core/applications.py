"""App-first queries: flat application listings and XML/detail resolution by program or app id."""

from __future__ import annotations

from dataclasses import dataclass
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
    from pathlib import Path

    from xknxeditor.namespaces.intermediate.application_program_t import (
        ApplicationProgram,
    )
    from xknxeditor.prod import Application as ProductApplication


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
    archive = Archive(program.knxprod_path)
    with archive:
        xml_bytes = archive.get_application_xml(manufacturer_id, program.application_id)
        if xml_bytes is None:
            return None
        return xml_bytes, manufacturer_id


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

    with Archive(knxprod_path) as archive:
        xml_bytes = archive.get_application_xml(manufacturer_id, application_id)
    if xml_bytes is None:
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
    program_id = db.scalars(
        select(HardwareProgram.id)
        .where(HardwareProgram.application_id == application_id)
        .limit(1)
    ).first()
    if program_id is None:
        return None
    return get_application_detail(db, program_id, language, cache_dir)

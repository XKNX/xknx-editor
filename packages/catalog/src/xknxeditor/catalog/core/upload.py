"""Ingest .knxprod archives into the catalog DB via the product Registry."""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import logging
import os
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from xknxeditor.catalog.core.product_members import (
    has_other_members,
    product_members,
)
from xknxeditor.catalog.models import (
    Application,
    CatalogMarker,
    CatalogSection,
    CatalogSectionProduct,
    Hardware,
    HardwareProgram,
    HardwareProgramCouplerCapability,
    HardwareProgramMediumType,
    Manufacturer,
)
from xknxeditor.prod import DeviceProgram, Registry, load
from xknxeditor.prod.errors import ArchiveError, check_cancelled

if TYPE_CHECKING:
    import threading

logger = logging.getLogger(__name__)


def _ingest_manufacturers(session: Session, reg: Registry) -> None:
    names = reg.master.manufacturers
    mids = set(reg.manufacturer_to_hardware) | set(reg.manufacturer_to_section)
    for mid in mids:
        session.merge(Manufacturer(id=mid, name=names.get(mid)))


def _ingest_applications(session: Session, reg: Registry) -> None:
    # From the facts the load kept: the parsed programs are not in memory any more.
    for app in reg.applications.values():
        facts = reg.application_facts[app.id]
        session.merge(
            Application(
                id=app.id,
                name=app.name,
                application_number=facts.application_number,
                application_version=facts.application_version,
                mask_version=facts.mask_version,
                is_secure_enabled=facts.is_secure_enabled,
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


def _registration_date(value: object) -> datetime.date | None:
    """The day of a registration date: an ``xs:date``, or, as older products write it, a date
    with a time (``2005-03-10T00:00:00``), which stays a plain string. ``None`` when there is
    none or it is no date."""
    if value is None or value == "":
        return None
    to_date = getattr(value, "to_date", None)
    if callable(to_date):
        day = to_date()
        return day if isinstance(day, datetime.date) else None
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


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
        reg_date = _registration_date(info.registration_date)

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


def _stored_path(dest_dir: Path, content: bytes) -> Path:
    """Where the store keeps ``content``: named by its content hash."""
    return dest_dir / f"{hashlib.sha3_256(content).hexdigest()}.knxprod"


def _referenced(session: Session, path: Path) -> bool:
    """Whether a hardware program reads its product data from ``path``."""
    return (
        session.scalar(
            select(HardwareProgram.id)
            .where(HardwareProgram.knxprod_path == str(path))
            .limit(1)
        )
        is not None
    )


def write_stored(path: Path, content: bytes) -> None:
    """Store ``content`` at ``path`` unless it is there already.

    Written to ``<name>.<pid>.tmp`` and renamed, so the file under its name is always complete. A
    file of the name with another size (cut short by an older version that wrote it in place) is
    written again: the name is the content's hash, so its size is known. A file that is there
    already is dated now, so the store's tidying (which removes unreferenced files by their age)
    does not remove it while the import that uses it is not committed yet."""
    with contextlib.suppress(FileNotFoundError):
        if path.stat().st_size == len(content):
            with contextlib.suppress(OSError):
                os.utime(path)
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    partial.write_bytes(content)
    partial.replace(path)


def upload_knxprod(
    content: bytes,
    dest_dir: Path,
    engine: Engine,
    *,
    require_catalog: bool = True,
    cache_dir: Path | None = None,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Ingest .knxprod bytes into the database behind ``engine``. Returns the path the content is
    stored at, or would be: an archive no hardware program refers to is not stored.

    Setting ``cancel`` stops the ingest with :class:`ImportCancelled` up to the moment it writes;
    nothing is stored then. ``progress`` hears the application programs parsed, out of how many.

    ``require_catalog=False`` ingests product data embedded in a ``.knxproj`` (which omits
    ``Catalog.xml``): hardware and applications are stored so devices resolve, only the catalog
    browse index is absent for those manufacturers.

    Idempotent: skips the ingest only when this content is already IN THE DATABASE (a hardware
    program referencing its stored path exists), not merely when the stored file is present. This
    keeps the store file and the DB in sync — e.g. if the catalog DB was deleted/rebuilt while the
    file cache remained, re-importing repopulates the DB instead of wrongly skipping. The stored
    file is (re)written whenever it is missing, since resolution reads it at runtime by path.

    Only the product data is stored (see :func:`product_members`): a ``.knxproj`` is stored without
    its project, and the content hash is that of what is stored.
    """
    content = product_members(content)
    path = _stored_path(dest_dir, content)
    with Session(engine) as session:
        in_db = (
            session.query(HardwareProgram).filter_by(knxprod_path=str(path)).first()
            is not None
        )
        if not in_db:
            registry = load(
                content,
                require_catalog=require_catalog,
                cache_dir=cache_dir,
                cancel=cancel,
                progress=progress,
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
            # The last moment to stop: the session is not committed yet, so nothing is stored.
            check_cancelled(cancel)
            # Write the file BEFORE committing so a committed row never references a missing
            # archive (resolution reads it by path). A stray file from a failed commit is
            # harmless: dedup is DB-driven, so the next import re-ingests it, and the store's
            # tidying removes it once it is old (see tidy_store). Only the hardware programs read
            # the file: when none refers to it (a project whose programs the catalog has already),
            # it is not stored at all.
            session.flush()
            if _referenced(session, path):
                write_stored(path, content)
            session.commit()
            logger.debug("catalog import committed: %s", path.name)
        else:
            write_stored(path, content)
    return path


STORE_REWRITTEN = "store-without-projects"
# A file this young may be another editor's import that has not committed yet (see tidy_store).
_UNREFERENCED_AGE = datetime.timedelta(hours=1)


@dataclass(frozen=True)
class StoreTidying:
    """What :func:`tidy_store` changed: archives rewritten without a project, files removed."""

    rewritten: int = 0
    removed: int = 0


def tidy_store(
    engine: Engine, dest_dir: Path, now: float | None = None
) -> StoreTidying:
    """Keep the store to the archives the catalog reads, with product data only.

    Once per catalog (marked in the database): each archive the hardware programs refer to that
    holds more than product data (stored whole from a ``.knxproj`` by an older version) is written
    again with its product data only, under the hash of the result, and its hardware programs are
    pointed at it. The old file is not removed then but dated now: an editor that is running
    already may still read it, and the removal of unreferenced files below takes it at a later
    start. Only files in ``dest_dir`` are ever removed.

    At every call: files in ``dest_dir`` no hardware program refers to, and leftover ``.tmp``
    files, are removed when they are older than an hour (``now``: the time to measure from, as
    :func:`time.time`). A younger one may be the import of another editor using this catalog that
    has written its file and not committed yet.

    An archive or file that cannot be read, written or removed is left as it is (and logged). Each
    step can run twice, so another editor tidying the same catalog at the same time does no harm;
    a database it holds locked postpones the tidying to the next start."""
    rewritten = 0
    with Session(engine) as session:
        try:
            if session.get(CatalogMarker, STORE_REWRITTEN) is None:
                rewritten, complete = _rewrite_archives(session, dest_dir)
                if complete:
                    # Another editor on this catalog may have marked it at the same moment.
                    session.execute(
                        sqlite_insert(CatalogMarker)
                        .values(name=STORE_REWRITTEN)
                        .on_conflict_do_nothing()
                    )
                    session.commit()
            referenced = {
                Path(p).name
                for p in session.scalars(
                    select(HardwareProgram.knxprod_path).distinct()
                )
            }
        except SQLAlchemyError as exc:
            # E.g. the database locked by another editor: try again at the next start, and
            # remove nothing without knowing what is referenced.
            session.rollback()
            logger.warning("catalog store not tidied: %s", exc)
            return StoreTidying(rewritten=rewritten)
    removed = _remove_unreferenced(dest_dir, referenced, now)
    return StoreTidying(rewritten=rewritten, removed=removed)


def _rewrite_archives(session: Session, dest_dir: Path) -> tuple[int, bool]:
    """Rewrite the referenced archives that hold a project; how many, and whether the rewrite is
    complete (else the next start tries again). A missing or broken archive has nothing to rewrite
    and does not keep it from completing; one that could not be written or committed does."""
    rewritten = 0
    complete = True
    stored = session.scalars(select(HardwareProgram.knxprod_path).distinct()).all()
    for old in sorted(stored):
        old_path = Path(old)
        try:
            if not has_other_members(old):
                continue
            content = product_members(old_path.read_bytes())
        except (FileNotFoundError, zipfile.BadZipFile, ArchiveError) as exc:
            logger.warning("stored archive not readable: %s: %s", old_path.name, exc)
            continue
        except OSError as exc:  # e.g. no permission: may work at the next start
            logger.warning("stored archive not read: %s: %s", old_path.name, exc)
            complete = False
            continue
        except Exception as exc:  # broken in another way (a name that is no UTF-8, …)
            logger.warning("stored archive not readable: %s: %s", old_path.name, exc)
            continue
        new_path = _stored_path(dest_dir, content)
        try:
            # The steps keep every state in between consistent: the new file is complete before
            # the programs point to it, and the old one goes only after they do (by its age).
            write_stored(new_path, content)
            session.execute(
                update(HardwareProgram)
                .where(HardwareProgram.knxprod_path == old)
                .values(knxprod_path=str(new_path))
            )
            session.commit()
        except (OSError, SQLAlchemyError) as exc:
            session.rollback()
            logger.warning("stored archive not rewritten: %s: %s", old_path.name, exc)
            complete = False
            continue
        with contextlib.suppress(OSError):
            os.utime(old_path)
        rewritten += 1
        logger.info(
            "stored archive rewritten without project data: %s -> %s",
            old_path.name,
            new_path.name,
        )
    return rewritten, complete


def _remove_unreferenced(
    dest_dir: Path, referenced: set[str], now: float | None
) -> int:
    """Remove the files of ``dest_dir`` not named in ``referenced`` that are old enough."""
    if not dest_dir.is_dir():
        return 0
    limit = (time.time() if now is None else now) - _UNREFERENCED_AGE.total_seconds()
    removed = 0
    try:
        files = list(dest_dir.iterdir())
    except OSError as exc:
        logger.warning("catalog store not listed: %s", exc)
        return 0
    for file in files:
        unused = file.suffix == ".tmp" or (
            file.suffix == ".knxprod" and file.name not in referenced
        )
        if not unused:
            continue
        try:
            if file.stat().st_mtime > limit:
                continue
            file.unlink()
        except OSError as exc:
            logger.warning("store file not removed: %s: %s", file.name, exc)
            continue
        removed += 1
        logger.info("unreferenced store file removed: %s", file.name)
    return removed

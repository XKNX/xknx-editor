"""Compact a project file: drop stored product data no device uses, then ``VACUUM``.

Not undoable: the stored manufacturer members are rewritten in place (no history event) and the
removed data has to be imported again when such a product is added later.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from xknxeditor.proj.core.product_prune import (
    STEP_DEVICES,
    STEP_STORE,
    STEP_VACUUM,
    ProductPruneReport,
    ProgressCallback,
    prune_product_blob,
)
from xknxeditor.proj.models import Device, Project


class CompactCancelled(Exception):
    """The compaction was cancelled before anything was written."""


@dataclass(frozen=True, slots=True)
class CompactResult:
    """Outcome of an analysis or a compaction.

    ``file_size_after`` is an estimate for an analysis (``applied`` false) and the measured size
    after a compaction."""

    report: ProductPruneReport
    file_size_before: int
    file_size_after: int
    applied: bool


@dataclass(frozen=True, slots=True)
class UsedProductIds:
    """The product, hardware2program and application ids the project's devices reference."""

    products: frozenset[str]
    hardware2programs: frozenset[str]
    applications: frozenset[str]


def used_product_ids(session: Session) -> UsedProductIds:
    """Collect the product data ids all devices of the project reference."""
    rows = session.query(
        Device.product_ref_id,
        Device.hardware2program_ref_id,
        Device.application_program_ref_id,
    ).all()
    return UsedProductIds(
        frozenset(r[0] for r in rows if r[0]),
        frozenset(r[1] for r in rows if r[1]),
        frozenset(r[2] for r in rows if r[2]),
    )


def _pruned(
    session: Session, progress: ProgressCallback | None
) -> tuple[Project, bytes | None, ProductPruneReport]:
    if progress is not None:
        progress(STEP_DEVICES, 0, 1)
    project = session.query(Project).first()
    if project is None:
        raise ValueError("not a project (no project row)")
    used = used_product_ids(session)
    if progress is not None:
        progress(STEP_DEVICES, 1, 1)
    blob = project.imported_product_members
    if not blob:
        return project, None, ProductPruneReport()
    new_blob, report = prune_product_blob(
        blob,
        used.products,
        used.hardware2programs,
        used.applications,
        progress=progress,
    )
    return project, (None if report.nothing_removed else new_blob), report


def _free_bytes(session: Session) -> int:
    pages = session.execute(text("PRAGMA freelist_count")).scalar() or 0
    size = session.execute(text("PRAGMA page_size")).scalar() or 0
    return int(pages) * int(size)


def analyse_compaction(
    session: Session, path: Path, progress: ProgressCallback | None = None
) -> CompactResult:
    """Report what :func:`compact_project` would remove, without changing anything."""
    project, _blob, report = _pruned(session, progress)
    session.expire(project, ["imported_product_members"])
    before = path.stat().st_size
    saved = report.size_before - report.size_after + _free_bytes(session)
    return CompactResult(report, before, max(0, before - saved), applied=False)


def compact_project(
    session: Session,
    engine: Engine,
    path: Path,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> CompactResult:
    """Remove unused stored product data and ``VACUUM`` the project file.

    ``cancelled`` is polled at every analysis step and before the first write; when it returns true
    the project is left unchanged and :class:`CompactCancelled` is raised."""

    def step(name: str, done: int, total: int) -> None:
        if cancelled is not None and cancelled():
            raise CompactCancelled
        if progress is not None:
            progress(name, done, total)

    session.commit()
    before = path.stat().st_size
    try:
        project, new_blob, report = _pruned(session, step)
        step(STEP_STORE, 0, 1)
    except CompactCancelled:
        session.expire_all()
        raise
    if new_blob is not None:
        project.imported_product_members = new_blob
    session.commit()
    if progress is not None:
        progress(STEP_STORE, 1, 1)
        progress(STEP_VACUUM, 0, 1)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM")
    if progress is not None:
        progress(STEP_VACUUM, 1, 1)
    return CompactResult(report, before, path.stat().st_size, applied=True)

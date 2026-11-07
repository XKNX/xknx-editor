"""The product data a project stores per hardware program, beside what an imported archive brought.

A device added in the editor (or given another application program) needs product data the imported
members do not have. Each such hardware program gets a row of its own
(:class:`~xknxeditor.proj.models.ProductData`): only its part of the product file it came from, so
adding one never repacks the others and :func:`prune_rows` drops a program no device uses any more
without editing XML. :func:`product_rows` builds rows from product files without a project, so it
can run in the background; the rest reads or writes a project's session.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Collection, Iterable, Mapping

from sqlalchemy.orm import Session

from xknxeditor.proj.core.manufacturer_xml import (
    DeviceProduct,
    add_product_members,
    defined_programs,
    merge_members,
)
from xknxeditor.proj.core.product_prune import ProductPruneReport
from xknxeditor.proj.models import ProductData

# A row: the products of the program's hardware it has, and its members as a ZIP.
Row = tuple[list[str], bytes]


def zip_members(members: Mapping[str, bytes]) -> bytes:
    """Pack a member map into a ZIP. The same members always give the same bytes (sorted, with a
    fixed date), so a store that tells files apart by their content keeps one copy."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, members[name])
    return buffer.getvalue()


def unzip_members(blob: bytes | None) -> dict[str, bytes]:
    """The members of a ZIP (empty for none)."""
    if not blob:
        return {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {
            i.filename: zf.read(i.filename) for i in zf.infolist() if not i.is_dir()
        }


def stored_products(session: Session) -> dict[str, frozenset[str]]:
    """Each stored hardware program with the products its row has (without reading the data)."""
    return {
        program: frozenset(products)
        for program, products in session.query(
            ProductData.hardware2program_ref_id, ProductData.product_ref_ids
        )
    }


def stored_members(session: Session) -> dict[str, bytes]:
    """The members of all rows as one product data map (manufacturer files merged)."""
    members: dict[str, bytes] = {}
    for (data,) in session.query(ProductData.data).order_by(
        ProductData.hardware2program_ref_id
    ):
        members = merge_members(members, unzip_members(data))
    return members


def missing_programs(
    devices: Iterable[DeviceProduct],
    imported_programs: Collection[str],
    stored: Mapping[str, Collection[str]],
) -> dict[str, list[DeviceProduct]]:
    """The hardware programs that need a row, each with all devices that use it: one the imported
    data does not declare and no row has, or whose row lacks a product a device uses with it."""
    by_program: dict[str, list[DeviceProduct]] = {}
    for device in devices:
        if device.hardware2program:
            by_program.setdefault(device.hardware2program, []).append(device)
    return {
        program: users
        for program, users in by_program.items()
        if program not in imported_programs
        and (
            program not in stored
            or any(d.product and d.product not in stored[program] for d in users)
        )
    }


def product_rows(
    sources: Mapping[str, bytes], missing: Mapping[str, list[DeviceProduct]]
) -> dict[str, Row]:
    """The row of each program of ``missing`` (see :func:`missing_programs`) from ``sources``, the
    members of the product files the devices were added from; a program they do not declare gets
    none. Reads no project."""
    rows: dict[str, Row] = {}
    for program, users in missing.items():
        members = add_product_members({}, sources, users)
        if program not in defined_programs(members):
            continue
        products = sorted({d.product for d in users if d.product})
        rows[program] = (products, zip_members(members))
    return rows


def store_rows(session: Session, rows: Mapping[str, Row]) -> None:
    """Store (or replace) the rows; the caller commits."""
    for program, (products, data) in rows.items():
        session.merge(
            ProductData(
                hardware2program_ref_id=program, product_ref_ids=products, data=data
            )
        )


def prune_rows(
    session: Session, used_programs: Collection[str], *, apply: bool
) -> ProductPruneReport:
    """Report what the rows of programs no device uses hold (their application programs and
    members), and delete those rows with ``apply`` (the caller commits)."""
    report = ProductPruneReport()
    for row in session.query(ProductData):
        size = len(row.data)
        report.size_before += size
        if row.hardware2program_ref_id in used_programs:
            report.size_after += size
            continue
        for name in unzip_members(row.data):
            report.removed_members.append(name)
            file = name.rsplit("/", 1)[-1]
            if "_A-" in file and file.endswith(".xml"):
                report.application_programs.append(file.removesuffix(".xml"))
        if apply:
            session.delete(row)
    return report

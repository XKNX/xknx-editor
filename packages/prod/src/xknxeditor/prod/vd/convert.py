"""Convert a product database in the newest schema to the XML files of a product package.

The order follows the converter: baggages, application programs, hardware (products and
Hardware2Program), catalog. Ids carry the unsigned fingerprint ``0000``; hashes and signatures
are added by :mod:`.signing`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application import AppConverter
from .baggage import convert_baggages
from .catalog import convert_catalog
from .context import Context, ConversionError
from .hardware import convert_hardware
from .master import MasterData
from .xmlout import document, serialize

if TYPE_CHECKING:
    from .database import Database, Row

__all__ = ["ConversionError", "convert", "convert_with_report"]


def _registration(ctx: Context, row: Row) -> tuple[int, int, int]:
    """Registration number, status code and version style of a program (for duplicates)."""
    number = status = 0
    for p2p in ctx.select("product_to_program", "PROGRAM_ID", row["PROGRAM_ID"]):
        n = (
            int(p2p["REGISTRATION_NUMBER"] or 0)
            if p2p["REGISTRATION_NUMBER"] is not None
            else 0
        )
        st = (
            int(p2p["PROD2PROG_STATUS_CODE"] or 0)
            if p2p["PROD2PROG_STATUS_CODE"] is not None
            else 0
        )
        if n > number or (n == number and st > status):
            number, status = n, st
    version = row.str("PROGRAM_VERSION") or ""
    style = 1 if not version or "." not in version else 0
    return number, status, style


def convert_with_report(
    db: Database, master_xml: bytes
) -> tuple[dict[str, bytes], list[str], list[str]]:
    """Like :func:`convert`, also returning the warnings and errors of the conversion."""
    ctx = Context(db, MasterData(master_xml))
    ctx.init_manufacturer_languages()
    files: dict[str, bytes] = {}
    for doc in convert_baggages(ctx):
        if doc.baggages:
            files[f"{doc.manufacturer}/Baggages.xml"] = serialize(
                document(doc.to_xml()), bom=False
            )
        files.update(doc.files)
    apps: dict[str, tuple[bytes, tuple[int, int, int]]] = {}
    for row in ctx.rows("application_program"):
        manufacturer = ctx.manufacturer_ref(row["MANUFACTURER_ID"])
        if ctx.master.manufacturer(manufacturer) is None:
            ctx.errors.append(f"UnknownManufacturerId: {manufacturer}")
            continue
        ctx.default_language = ctx.language_of(manufacturer)
        try:
            result = AppConverter(ctx, row).convert()
        except ConversionError:
            raise
        except (KeyError, IndexError, ValueError, AssertionError) as error:
            ctx.errors.append(
                f"ApplicationProgramConversionFailed: Unable to convert ApplicationProgram with "
                f"Id = {row['PROGRAM_ID']}, name = {row['PROGRAM_NAME']}: {error!r}"
            )
            continue
        if result is None:
            continue
        app_id, data = result
        info = _registration(ctx, row)
        old = apps.get(app_id)
        if old is not None and _compare(old[1], info) >= 0:
            continue
        apps[app_id] = (data, info)
    for app_id, (data, _info) in apps.items():
        files[f"{app_id[:6]}/{app_id}.xml"] = data
    for doc in convert_hardware(ctx):
        files[f"{doc.manufacturer}/Hardware.xml"] = serialize(
            document(doc.to_xml()), bom=False
        )
    for doc in convert_catalog(ctx):
        files[f"{doc.manufacturer}/Catalog.xml"] = serialize(
            document(doc.to_xml()), bom=False
        )
    return files, ctx.warnings, ctx.errors


def _compare(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    for x, y in zip(a, b, strict=True):
        if x != y:
            return x - y
    return 0


def convert(db: Database, master_xml: bytes) -> dict[str, bytes]:
    """The unsigned package files (``M-XXXX/Catalog.xml``, ``Hardware.xml``, one file per
    application program, ``Baggages.xml`` and the baggage files) of a database in the newest
    schema; ``master_xml`` is the KNX master data (any schema version)."""
    return convert_with_report(db, master_xml)[0]

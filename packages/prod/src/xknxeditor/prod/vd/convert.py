"""Convert a product database in the newest schema to the XML files of a product package.

The order follows the converter: baggages, application programs, hardware (products and
Hardware2Program), catalog. Ids carry the unsigned fingerprint ``0000``; hashes and signatures
are added by :mod:`.signing`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xknxeditor.prod.errors import check_cancelled

from .application import AppConverter
from .baggage import convert_baggages
from .catalog import convert_catalog
from .context import Context, ConversionError
from .hardware import convert_hardware
from .master import MasterData
from .optimized import add_ex_factory_images
from .xmlout import document, serialize

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

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
    db: Database,
    master_xml: bytes,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, bytes], list[str], list[str]]:
    """Like :func:`convert`, also returning the warnings and errors of the conversion."""
    check_cancelled(cancel)
    ctx = Context(db, MasterData(master_xml))
    ctx.init_manufacturer_languages()
    files: dict[str, bytes] = {}
    # (programs may move baggages to their manufacturer)
    ctx.baggage_docs = convert_baggages(ctx)
    apps: dict[str, tuple[bytes, tuple[int, int, int]]] = {}
    programs = ctx.rows("application_program")
    for done, row in enumerate(programs):
        check_cancelled(cancel)
        if progress is not None:
            progress(done, len(programs))
        manufacturer = ctx.manufacturer_ref(row["MANUFACTURER_ID"])
        if not ctx.known_manufacturer(manufacturer):
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
    check_cancelled(cancel)
    if progress is not None:
        progress(len(programs), len(programs))
    hardware_docs = convert_hardware(ctx)
    baggage_docs = ctx.baggage_docs
    # replaced plugins: their data goes into the hardware, their baggages and the plugin flag
    # of their hardware go
    add_ex_factory_images(ctx, ctx.optimized_download, baggage_docs, hardware_docs)
    for replacer in (ctx.byte_swapper, ctx.comfort_plugin, ctx.optimized_download):
        baggage_docs = replacer.remove_unused_baggages(baggage_docs)
    for replacer in (ctx.byte_swapper, ctx.comfort_plugin):
        replacer.reset_no_download_without_plugin(hardware_docs)
    for doc in baggage_docs:
        # (also without baggages, when all of the manufacturer's failed)
        files[f"{doc.manufacturer}/Baggages.xml"] = serialize(
            document(doc.to_xml()), bom=False, tool_info=doc.tool_info
        )
        files.update(doc.files)
    for app_id, (data, _info) in apps.items():
        files[f"{app_id[:6]}/{app_id}.xml"] = data
    for doc in hardware_docs:
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


def convert(
    db: Database,
    master_xml: bytes,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, bytes]:
    """The unsigned package files (``M-XXXX/Catalog.xml``, ``Hardware.xml``, one file per
    application program, ``Baggages.xml`` and the baggage files) of a database in the newest
    schema; ``master_xml`` is the KNX master data (any schema version). ``progress`` is called
    with the application programs converted so far and their number; setting ``cancel`` stops
    the conversion with :class:`ImportCancelled`."""
    return convert_with_report(db, master_xml, cancel=cancel, progress=progress)[0]

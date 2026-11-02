"""Import KNX product data that is not packaged as a signed ``.knxprod``.

OpenKNX (and its ``OpenKNXproducer`` toolchain) ships apps as a **monolithic KNX product XML** — one
``<KNX>`` document with ``ManufacturerData → Manufacturer → Catalog + ApplicationPrograms + Hardware``
inline — either raw or inside a GitHub *release ZIP* (``data/<App>.xml`` + ``content.xml`` + firmware).
There is no ``.knxprod`` and no signature; that is only produced later by ``Build-knxprod.ps1`` via
the standard toolchain / OpenKNXproducer.

Our loader only needs the standard package files (``knx_master.xml`` + per-manufacturer
``Catalog.xml`` / ``Hardware.xml`` / application XMLs) and ignores signatures. So we can wrap a
monolithic XML into an in-memory ``.knxprod`` and feed it to the normal :func:`~.loader.load` path —
no external toolchain, no OpenKNXproducer. The caller supplies ``knx_master.xml`` (the app bundles one), since the
monolithic XML carries no ``<MasterData>``.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import TYPE_CHECKING

from xknxeditor.prod.errors import ArchiveError

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

# Manufacturer/application identifiers as they appear in the monolithic XML (and the package paths).
_MANUFACTURER_RE = re.compile(rb'<Manufacturer\s+RefId="(M-[0-9A-Fa-f]{4})"')
_APPLICATION_RE = re.compile(rb'<ApplicationProgram\s+Id="(M-[0-9A-Fa-f]{4}_A-[^"]+)"')
_APPLICATION_PROGRAMS_OPEN = b"<ApplicationPrograms"
_APPLICATION_PROGRAMS_CLOSE = b"</ApplicationPrograms>"


def monolithic_to_knxprod(product_xml: bytes, master_xml: bytes) -> bytes:
    """Wrap a monolithic KNX product XML into an in-memory ``.knxprod`` archive (ZIP bytes).

    Builds ``knx_master.xml`` + ``M-XXXX/Catalog.xml`` + ``M-XXXX/Hardware.xml`` +
    ``M-XXXX/<app-id>.xml``. The application file keeps the full document; the Catalog/Hardware
    copies have the (huge) ``<ApplicationPrograms>`` block sliced out so those two parses stay cheap
    (each parser only reads its own section anyway). ``master_xml`` is the ``knx_master.xml`` the
    document references but does not embed.
    """
    mfr_match = _MANUFACTURER_RE.search(product_xml)
    if mfr_match is None:
        raise ArchiveError('not a KNX product XML (no <Manufacturer RefId="M-XXXX">)')
    manufacturer_id = mfr_match.group(1).decode("ascii")

    app_match = _APPLICATION_RE.search(product_xml)
    if app_match is None:
        raise ArchiveError("KNX product XML has no <ApplicationProgram>")
    app_id = app_match.group(1).decode("ascii")

    trimmed = _without_application_programs(product_xml)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("knx_master.xml", master_xml)
        zf.writestr(f"{manufacturer_id}/Catalog.xml", trimmed)
        zf.writestr(f"{manufacturer_id}/Hardware.xml", trimmed)
        zf.writestr(f"{manufacturer_id}/{app_id}.xml", product_xml)
    return buffer.getvalue()


def _without_application_programs(product_xml: bytes) -> bytes:
    """Return ``product_xml`` with the single ``<ApplicationPrograms>…</ApplicationPrograms>`` block
    removed, keeping Catalog + Hardware. Falls back to the full document if the block is not found."""
    start = product_xml.find(_APPLICATION_PROGRAMS_OPEN)
    if start == -1:
        return product_xml
    end = product_xml.rfind(_APPLICATION_PROGRAMS_CLOSE)
    if end == -1 or end < start:
        return product_xml
    return product_xml[:start] + product_xml[end + len(_APPLICATION_PROGRAMS_CLOSE) :]


def openknx_release_to_knxprod(release_zip: bytes, master_xml: bytes) -> bytes:
    """Turn an OpenKNX release ZIP into a ``.knxprod``.

    Reads ``content.xml`` (``<ETSapp XmlFile="…"/>``) to find the monolithic product XML under
    ``data/`` and wraps it via :func:`monolithic_to_knxprod`."""
    with zipfile.ZipFile(io.BytesIO(release_zip)) as zf:
        names = zf.namelist()
        content_name = _find_entry(names, "content.xml")
        if content_name is None:
            raise ArchiveError("OpenKNX release has no content.xml")
        content = zf.read(content_name)
        xml_match = re.search(rb'XmlFile="([^"]+)"', content)
        if xml_match is None:
            raise ArchiveError('content.xml has no <ETSapp XmlFile="…"/>')
        xml_file = xml_match.group(1).decode("utf-8")
        product_name = _find_entry(names, xml_file)
        if product_name is None:
            raise ArchiveError(f"product XML {xml_file!r} not found in the release")
        return monolithic_to_knxprod(zf.read(product_name), master_xml)


def knxprod_from_source(
    data: bytes,
    master_xml: bytes,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> bytes:
    """Return ``.knxprod`` bytes for any supported source, ready for :func:`~.loader.load`.

    Accepts a real ``.knxprod`` (returned unchanged), an OpenKNX release ZIP, a ``.vd1`` … ``.vd5``
    product database (converted), or a raw monolithic KNX product XML. ``master_xml`` supplies
    ``knx_master.xml`` for the other cases. ``cancel`` and ``progress`` go to the conversion of a
    product database (see :func:`~.vd.vd_to_knxprod`)."""
    # A ZIP is a real .knxprod, an OpenKNX release or a .vd* database.
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        if "knx_master.xml" in names:
            return data  # already a .knxprod (signatures are ignored on load)
        if _find_entry(names, "content.xml") is not None:
            return openknx_release_to_knxprod(data, master_xml)
        from xknxeditor.prod import vd

        if vd.holds_vd_dump(names):
            return vd.vd_to_knxprod(data, master_xml, cancel=cancel, progress=progress)
        raise ArchiveError(
            "ZIP is neither a .knxprod (no knx_master.xml), an OpenKNX release (no content.xml) "
            "nor a product database (.vd1-.vd5)"
        )
    if b"<KNX" in data[:4096]:  # a raw monolithic product XML
        return monolithic_to_knxprod(data, master_xml)
    raise ArchiveError(
        "unrecognized data: not a .knxprod, an OpenKNX release ZIP, or a KNX product XML"
    )


def _find_entry(names: list[str], suffix: str) -> str | None:
    """First ZIP entry whose (slash-normalized) path is or ends with ``suffix``. Release ZIPs from
    Windows tools use backslash separators, so normalize before matching."""
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized == suffix or normalized.endswith("/" + suffix):
            return name
    return None

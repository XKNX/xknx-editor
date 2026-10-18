"""Collect the manufacturer archive members a ``.knxproj`` export needs to bundle.

``xknxeditor-proj`` is catalog-free, so its :func:`~xknxeditor.proj.export_knxproj` writes only the
project structure. To make the archive self-contained (applications resolvable), the GUI — which has
the catalog — re-extracts each used manufacturer's ``M-XXXX/`` tree (Hardware/Catalog/application
program XMLs, baggages) and its ``M-XXXX.signature`` from the original ``.knxprod`` the device was
imported from, and merges those archives' ``knx_master.xml`` into one. A manufacturer whose
products come from several ``.knxprod`` files has a ``Hardware.xml``/``Catalog.xml`` in each; those
are merged into one, so every device's hardware and catalog entry is in the archive (the export
re-signs the changed folder). The result is passed to
``export_knxproj(..., extra_files=..., master_xml=...)``.

The original ``.knxprod`` files must still exist at the paths the catalog recorded; refs that can't
be resolved are reported in :attr:`ManufacturerBundle.skipped_refs` and simply left out.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from xknxeditor.proj.core.manufacturer_xml import merge_manufacturer_xml

if TYPE_CHECKING:
    from editor_gui.plugins.catalog.service import CatalogService


@dataclass
class ManufacturerBundle:
    """Result of :func:`collect_manufacturer_bundle`."""

    extra_files: dict[str, bytes] = field(default_factory=dict[str, bytes])
    master_xml: bytes | None = None
    resolved_manufacturers: set[str] = field(default_factory=set[str])
    skipped_refs: set[str] = field(default_factory=set[str])


def collect_manufacturer_bundle(
    program_refs: Iterable[str], catalog: CatalogService
) -> ManufacturerBundle:
    """Gather manufacturer archive members for the given hardware-program refs.

    Args:
      program_refs: Distinct hardware-program (or product) refs used by the project's devices.
      catalog: The GUI catalog service (resolves a ref to its source ``.knxprod`` and manufacturer).

    Returns:
      A :class:`ManufacturerBundle` with the verbatim ``extra_files`` to add, a merged
      ``knx_master.xml`` (or ``None`` if nothing resolved), and bookkeeping of what was resolved
      or skipped.
    """
    bundle = ManufacturerBundle()

    # Map each source .knxprod to the manufacturer ids we need from it.
    needed: dict[str, set[str]] = {}
    for ref in program_refs:
        source = catalog.get_program_source(ref)
        if source is None:
            bundle.skipped_refs.add(ref)
            continue
        knxprod_path, manufacturer_id = source
        needed.setdefault(knxprod_path, set()).add(manufacturer_id)

    masters: list[bytes] = []
    for knxprod_path, manufacturer_ids in needed.items():
        try:
            with zipfile.ZipFile(knxprod_path) as zf:
                names = zf.namelist()
                if "knx_master.xml" in names:
                    masters.append(zf.read("knx_master.xml"))
                for mid in manufacturer_ids:
                    prefix = f"{mid}/"
                    signature = f"{mid}.signature"
                    for name in names:
                        if name.endswith("/"):
                            continue  # zip directory entry
                        member = name == signature or name.startswith(prefix)
                        if not member:
                            continue
                        if name not in bundle.extra_files:
                            bundle.extra_files[name] = zf.read(name)
                        elif name.endswith(".xml") and name.count("/") == 1:
                            # The same manufacturer file from another source: merge what it adds
                            # (e.g. Hardware.xml of a product imported on its own).
                            bundle.extra_files[name] = merge_manufacturer_xml(
                                bundle.extra_files[name], zf.read(name)
                            )
                        # Other files of the same name (application programs, baggages) are the
                        # same data: the first source's copy is kept.
                    bundle.resolved_manufacturers.add(mid)
        except (OSError, zipfile.BadZipFile):
            # Source archive gone or unreadable: skip every ref that pointed at it.
            bundle.skipped_refs.update(manufacturer_ids)

    bundle.master_xml = _merge_masters(masters)
    return bundle


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_manufacturers(root: ET.Element) -> ET.Element | None:
    for el in root.iter():
        if _localname(el.tag) == "Manufacturers":
            return el
    return None


def _merge_masters(masters: list[bytes]) -> bytes | None:
    """Merge several ``knx_master.xml`` blobs into one, unioning their ``<Manufacturer>`` entries.

    The blob with the most manufacturers is the base (it carries the full MasterData the importer needs);
    manufacturers present only in the others are appended to it.

    Genuine ``knx_master.xml`` carries a ``MasterData`` ``Signature`` over its exact bytes, and the
    importer rejects the whole import ("Invalid import data") when that signature no longer matches the
    content. Re-serializing the parsed tree changes the bytes (BOM, attribute order, whitespace,
    self-closing style) and thus breaks the signature — so we only re-serialize when a real merge
    across sources actually adds a manufacturer. In the common single-source case (or when every
    other master's manufacturers are already in the base) we return the base blob **verbatim**,
    keeping its signature valid.
    """
    if not masters:
        return None

    parsed = [ET.fromstring(blob) for blob in masters]

    def manufacturer_count(root: ET.Element) -> int:
        container = _find_manufacturers(root)
        return len(list(container)) if container is not None else 0

    base_index = max(range(len(parsed)), key=lambda i: manufacturer_count(parsed[i]))
    base = parsed[base_index]
    base_container = _find_manufacturers(base)
    if base_container is None:
        return masters[base_index]

    known = {m.get("Id") for m in base_container}
    appended = False
    for i, root in enumerate(parsed):
        if i == base_index:
            continue
        container = _find_manufacturers(root)
        if container is None:
            continue
        for manufacturer in container:
            mid = manufacturer.get("Id")
            if mid not in known:
                base_container.append(manufacturer)
                known.add(mid)
                appended = True

    # Nothing added: keep the base's original bytes so its MasterData signature stays valid.
    if not appended:
        return masters[base_index]

    # A real merge changed the content: the signature can no longer be valid, so blank it out
    # rather than shipping a mismatched one (a signed master can be supplied separately if needed).
    master_data = next(
        (el for el in base.iter() if _localname(el.tag) == "MasterData"), None
    )
    if master_data is not None and master_data.get("Signature"):
        master_data.set("Signature", "")
    # Keep the default namespace unprefixed so the merged master parses like the originals.
    if base.tag.startswith("{"):
        ET.register_namespace("", base.tag[1:].split("}", 1)[0])
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        base, encoding="unicode"
    ).encode("utf-8")

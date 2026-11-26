"""Collect the manufacturer archive members a ``.knxproj`` export needs to bundle.

The project supplies its stored product data. The GUI obtains any missing members from
the devices' original ``.knxprod`` files in the catalog. It merges shared manufacturer
files and chooses a ``knx_master.xml`` from the archives of all devices.

The original ``.knxprod`` files must still exist at the paths the catalog recorded; refs that can't
be resolved are reported in :attr:`ManufacturerBundle.skipped_refs` and simply left out.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from xknxeditor.proj.core.manufacturer_xml import DeviceProduct, merge_members
from xknxeditor.proj.core.product_prune import read_product_subset

if TYPE_CHECKING:
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.catalog.service import CatalogService


@dataclass
class ManufacturerBundle:
    """Result of :func:`collect_manufacturer_bundle`."""

    extra_files: dict[str, bytes] = field(default_factory=dict[str, bytes])
    master_xml: bytes | None = None
    resolved_manufacturers: set[str] = field(default_factory=set[str])
    skipped_refs: set[str] = field(default_factory=set[str])
    has_product_members: bool = False


def collect_manufacturer_bundle(
    program_refs: Iterable[str],
    catalog: CatalogService,
    missing_products: Iterable[DeviceProduct] | None = None,
) -> ManufacturerBundle:
    """Gather manufacturer archive members for the given hardware-program refs.

    Args:
      program_refs: Distinct hardware-program (or product) refs used by the project's devices.
      catalog: The GUI catalog service (resolves a ref to its source ``.knxprod`` and manufacturer).
      missing_products: When supplied, collect only these devices' product data.
        The master is still chosen from all sources.

    Returns:
      A :class:`ManufacturerBundle` with the ``extra_files`` to add, a merged
      ``knx_master.xml`` (or ``None`` if nothing resolved), and bookkeeping of what was resolved
      or skipped.
    """
    needed, skipped = program_sources(program_refs, catalog)
    selected: dict[str, dict[str, list[DeviceProduct]]] | None = None
    if missing_products is not None:
        selected = {}
        for device in missing_products:
            ref = device.hardware2program or device.product
            if not ref:
                continue
            source = catalog.get_program_source(ref)
            if source is not None:
                path, manufacturer = source
                needed.setdefault(path, set()).add(manufacturer)
                selected.setdefault(path, {}).setdefault(manufacturer, []).append(
                    device
                )
                skipped.discard(ref)
    bundle = read_manufacturer_bundle(needed, selected)
    bundle.skipped_refs |= skipped
    return bundle


def program_sources(
    program_refs: Iterable[str], catalog: CatalogService
) -> tuple[dict[str, set[str]], set[str]]:
    """Each source ``.knxprod`` the catalog has the given hardware-program refs from, with the
    manufacturer ids needed from it, and the refs it has no source of."""
    needed: dict[str, set[str]] = {}
    skipped: set[str] = set()
    for ref in program_refs:
        source = catalog.get_program_source(ref)
        if source is None:
            skipped.add(ref)
            continue
        knxprod_path, manufacturer_id = source
        needed.setdefault(knxprod_path, set()).add(manufacturer_id)
    return needed, skipped


def read_manufacturer_bundle(
    needed: dict[str, set[str]],
    selected: dict[str, dict[str, list[DeviceProduct]]] | None = None,
) -> ManufacturerBundle:
    """Read the master from the given sources and their manufacturer members for ``selected``.

    Without a selection, read the manufacturers in ``needed`` in full. No catalog access
    is needed here, so the reader can also run outside the shared project lock.
    """
    bundle = ManufacturerBundle()
    masters: list[bytes] = []
    for knxprod_path, manufacturer_ids in needed.items():
        try:
            with zipfile.ZipFile(knxprod_path) as zf:
                names = zf.namelist()
                if "knx_master.xml" in names:
                    masters.append(zf.read("knx_master.xml"))
                members = [
                    name
                    for mid in manufacturer_ids
                    for name in names
                    if not name.endswith("/")
                    and (name == f"{mid}.signature" or name.startswith(f"{mid}/"))
                ]
                bundle.has_product_members |= bool(members)
                if selected is None:
                    files = {name: zf.read(name) for name in members}
                elif manufacturers := selected.get(knxprod_path):
                    products = [
                        d for devices in manufacturers.values() for d in devices
                    ]
                    folders = set(manufacturers) | {
                        d.application.split("_", 1)[0]
                        for d in products
                        if d.application
                    }
                    files = read_product_subset(
                        zf,
                        [
                            name
                            for name in names
                            if not name.endswith("/")
                            and name.partition("/")[0].removesuffix(".signature")
                            in folders
                        ],
                        (d.product for d in products),
                        (d.hardware2program for d in products),
                        (d.application for d in products),
                    )
                else:
                    continue
                bundle.extra_files = merge_members(bundle.extra_files, files)
                bundle.resolved_manufacturers.update(manufacturer_ids)
        except (OSError, zipfile.BadZipFile):
            # Source archive gone or unreadable: skip every ref that pointed at it.
            bundle.skipped_refs.update(manufacturer_ids)

    bundle.master_xml = _merge_masters(masters)
    return bundle


def catalog_export_sources(
    devices: list[DeviceProduct],
    missing: list[DeviceProduct] | None,
    *,
    catalog: CatalogService,
    log: Logger | None = None,
    on_collected: Callable[[], None] | None = None,
) -> tuple[dict[str, bytes], bytes | None, bool]:
    """Supply the missing product members and the catalog master to the project exporter."""
    refs = {d.hardware2program or d.product for d in devices} - {""}
    try:
        bundle = collect_manufacturer_bundle(refs, catalog, missing)
    except Exception as e:
        if log is None:
            raise
        log.warning("manufacturer bundle failed", error=f"{type(e).__name__}: {e}")
        return {}, None, False
    finally:
        if on_collected is not None:
            on_collected()
    if log is not None:
        log.info(
            "manufacturer bundle collected",
            manufacturers=len(bundle.resolved_manufacturers),
            files=len(bundle.extra_files),
            skipped=len(bundle.skipped_refs),
        )
    return bundle.extra_files, bundle.master_xml, bundle.has_product_members


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

"""What a product archive provides, read from its catalog and hardware lists only.

Much quicker than loading the archive (which parses every application): it tells whether an
import would change the catalog, and which product versions an imported file lists."""

from __future__ import annotations

import io
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from xknxeditor.prod import Archive


@dataclass(frozen=True, slots=True)
class ArchiveVersions:
    """The catalog items and hardware programs of a product archive, and its product versions
    (product and hardware program of each catalog item)."""

    items: frozenset[str]
    programs: frozenset[str]
    versions: frozenset[tuple[str, str]]


def _elements(xml: bytes, name: str) -> list[dict[str, str]]:
    """The attributes of every element called ``name`` (in any namespace) in ``xml``."""
    found: list[dict[str, str]] = []
    for _event, element in ET.iterparse(io.BytesIO(xml)):
        if element.tag.rpartition("}")[2] == name:
            found.append(dict(element.attrib))
    return found


def archive_versions(
    content: bytes, *, require_catalog: bool = True
) -> ArchiveVersions:
    """The catalog items, hardware programs and product versions ``content`` (a product archive)
    provides. A catalog item counts only with a hardware program, as the import takes it; a
    version needs a product too. Raises :class:`~xknxeditor.prod.errors.ArchiveError` for an
    archive the import would refuse."""
    items: set[str] = set()
    programs: set[str] = set()
    versions: set[tuple[str, str]] = set()
    with Archive(content, require_catalog=require_catalog) as archive:
        for manufacturer_id in sorted(archive.manufacturer_ids):
            if not archive.has_hardware(manufacturer_id):
                continue
            hardware = archive.get_hardware_xml(manufacturer_id)
            programs.update(
                a["Id"] for a in _elements(hardware, "Hardware2Program") if "Id" in a
            )
            if not archive.has_catalog(manufacturer_id):
                continue
            catalog = archive.get_catalog_xml(manufacturer_id)
            for item in _elements(catalog, "CatalogItem"):
                program = item.get("Hardware2ProgramRefId")
                if not program or "Id" not in item:
                    continue
                items.add(item["Id"])
                if product := item.get("ProductRefId"):
                    versions.add((product, program))
    return ArchiveVersions(frozenset(items), frozenset(programs), frozenset(versions))

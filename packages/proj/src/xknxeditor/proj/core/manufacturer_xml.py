"""Merge manufacturer product data (``M-XXXX/*`` archive members) from several sources.

A manufacturer's ``Hardware.xml``/``Catalog.xml``/``Baggages.xml`` exist once per source (the
project's own product data, each ``.knxprod``); :func:`merge_manufacturer_xml` adds what another
source's file has to a base file. :func:`combine_product_members` builds an export's product data
from the project's own (kept exactly) plus what its devices need from other sources.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from xknxeditor.proj.core.product_prune import prune_product_members

_KNX_PROJECT_NS = "http://knx.org/xml/project/"

# Attributes that identify an element among its siblings in manufacturer data, in order.
_KEY_ATTRIBUTES = ("Id", "RefId", "Identifier", "AttributeName")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _element_key(element: ET.Element) -> tuple[str, str] | None:
    for attribute in _KEY_ATTRIBUTES:
        value = element.get(attribute)
        if value is not None:
            return attribute, value
    return None


def _merge_elements(base: ET.Element, extra: ET.Element) -> bool:
    """Add to ``base`` what ``extra`` has and it lacks: children are matched by tag and their
    ``Id``/``RefId``/``Identifier``/``AttributeName``, a child without one by tag when ``base``
    has exactly one such child (containers like ``Hardware`` or ``Languages``); matched children
    are merged the same way, the others appended. Returns whether anything was added."""
    keyed: dict[tuple[str, tuple[str, str]], ET.Element] = {}
    unkeyed: dict[str, list[ET.Element]] = {}
    for child in base:
        key = _element_key(child)
        if key is None:
            unkeyed.setdefault(child.tag, []).append(child)
        else:
            keyed[(child.tag, key)] = child
    added = False
    for child in extra:
        key = _element_key(child)
        if key is not None:
            match = keyed.get((child.tag, key))
        else:
            same = unkeyed.get(child.tag, [])
            match = same[0] if len(same) == 1 else None
            if match is None and any(
                ET.tostring(child) == ET.tostring(other) for other in same
            ):
                continue
        if match is not None:
            added = _merge_elements(match, child) or added
            continue
        base.append(child)
        if key is not None:
            keyed[(child.tag, key)] = child
        else:
            unkeyed.setdefault(child.tag, []).append(child)
        added = True
    return added


def merge_manufacturer_xml(base: bytes, extra: bytes) -> bytes:
    """Merge a manufacturer XML file (``Hardware.xml``, ``Catalog.xml``) from another source into
    ``base``. A source of another KNX schema version (``project/20`` into ``project/23``) is merged
    in the base's namespace. ``base`` is returned verbatim when ``extra`` adds nothing or either
    does not parse."""
    try:
        base_root = ET.fromstring(base)
        extra_root = ET.fromstring(extra)
    except ET.ParseError:
        return base
    base_ns, extra_ns = _namespace(base_root.tag), _namespace(extra_root.tag)
    if base_ns != extra_ns:
        if not (
            base_ns.startswith(_KNX_PROJECT_NS) and extra_ns.startswith(_KNX_PROJECT_NS)
        ):
            return base
        for element in extra_root.iter():
            if _namespace(element.tag) == extra_ns:
                element.tag = f"{{{base_ns}}}{_localname(element.tag)}"
    if base_root.tag != extra_root.tag or not _merge_elements(base_root, extra_root):
        return base
    # Keep the default namespace unprefixed so the merged file parses like the originals.
    if base_root.tag.startswith("{"):
        ET.register_namespace("", base_root.tag[1:].split("}", 1)[0])
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        base_root, encoding="unicode"
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class DeviceProduct:
    """The product data refs of one device. ``imported`` is true for a device that came with the
    project's own product data (imported from a ``.knxproj``), false for one added in the editor."""

    product: str
    hardware2program: str
    application: str
    imported: bool


def _is_manufacturer_file(name: str) -> bool:
    """A file directly in an ``M-XXXX/`` folder that :func:`merge_manufacturer_xml` can merge
    (``Hardware.xml``, ``Catalog.xml``, ``Baggages.xml``), not an application program."""
    folder, _, file = name.partition("/")
    return (
        folder.startswith("M-")
        and "/" not in file
        and file.endswith(".xml")
        and not file.startswith(f"{folder}_A-")
    )


def _defined_programs(members: Mapping[str, bytes]) -> set[str]:
    """Every ``Hardware2Program`` id the members' ``Hardware.xml`` files declare."""
    programs: set[str] = set()
    for name, data in members.items():
        if not name.endswith("/Hardware.xml"):
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        programs |= {
            el.get("Id", "")
            for el in root.iter()
            if _localname(el.tag) == "Hardware2Program"
        }
    return programs


def _manufacturer(ref: str) -> str:
    return ref.split("_", 1)[0]


def _subset(
    members: Mapping[str, bytes], devices: Iterable[DeviceProduct]
) -> dict[str, bytes]:
    """``members`` reduced to the product data of ``devices``."""
    devices = list(devices)
    subset, _ = prune_product_members(
        members,
        {d.product for d in devices},
        {d.hardware2program for d in devices},
        {d.application for d in devices},
    )
    return subset


def combine_product_members(
    own: Mapping[str, bytes],
    sources: Mapping[str, bytes],
    devices: Iterable[DeviceProduct],
) -> dict[str, bytes]:
    """The product data an export writes: the project's ``own`` members exactly, plus, from
    ``sources`` (the product files the devices were added from), the data of each device whose
    ``Hardware2Program`` the own data does not declare.

    Only what those devices use is added: their hardware, products and programs, their application
    programs and baggages. A catalog entry is added for a device added in the editor, as one is when
    a product is inserted into a project; a device of the project's own data whose application was
    replaced keeps the catalog as it was. Without own catalog data for a manufacturer, the entries
    of all its added devices are written. A project without own product data (created in the editor)
    writes ``sources`` unchanged."""
    if not own:
        return dict(sources)
    defined = _defined_programs(own)
    missing = [
        d for d in devices if d.hardware2program and d.hardware2program not in defined
    ]
    result = dict(own)
    if not missing or not sources:
        return result
    own_catalogs = {
        name.split("/", 1)[0] for name in own if name.endswith("/Catalog.xml")
    }
    listed = [
        d
        for d in missing
        if not d.imported or _manufacturer(d.hardware2program) not in own_catalogs
    ]
    data_subset = _subset(sources, missing)
    catalog_subset = _subset(sources, listed) if listed else {}
    for name, data in data_subset.items():
        if name.endswith("/Catalog.xml"):
            catalog = catalog_subset.get(name)
            if catalog is None:
                continue
            data = catalog
        if name in result:
            if _is_manufacturer_file(name):
                result[name] = merge_manufacturer_xml(result[name], data)
            continue
        result[name] = data
    return result

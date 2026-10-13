"""Import and export of an ETS group-address export (``ga-export/01``).

An ETS GA export is only the group-address tree - main/middle group folders and the
group addresses under them, with their names and datapoint types. It carries no
devices, topology, parameters or product data. :func:`import_ga_export` reads one and
writes it as a new project whose group-address tree mirrors the export; the rest of the
project is the default skeleton (:func:`seed_new_project`). :func:`write_ga_export` is
the inverse: it reads a project and emits its group-address tree as such an .xml.

The export's own ``<GroupRange>`` nesting is reproduced verbatim (folder names and
address windows), so the imported tree looks like it did in ETS. Group addresses are
placed into the leaf range that contains their address.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga, parse_ga
from xknxeditor.proj.core.dpt import normalize_datapoint_type
from xknxeditor.proj.core.skeleton import DEFAULT_INSTALLATION, seed_new_project
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import GroupAddress, GroupRange, Installation, Project

logger = logging.getLogger(__name__)

_NS = "http://knx.org/xml/ga-export/01"
_ROOT_TAG = f"{{{_NS}}}GroupAddress-Export"
_RANGE_TAG = f"{{{_NS}}}GroupRange"
_ADDRESS_TAG = f"{{{_NS}}}GroupAddress"


@dataclass(frozen=True)
class GaRange:
    """A ``<GroupRange>`` folder: its name, address window and nested child ranges."""

    name: str
    range_start: int
    range_end: int
    children: tuple["GaRange", ...] = ()


@dataclass
class GaAddress:
    """A ``<GroupAddress>``: raw 16-bit value, name, ETS datapoint-type token, description."""

    address: int
    name: str
    datapoint_type: str | None
    description: str


@dataclass
class GaExport:
    """A parsed GA export: the detected style plus the range tree and flat address list."""

    style: GroupAddressStyle
    ranges: tuple[GaRange, ...] = ()
    addresses: tuple[GaAddress, ...] = field(default=())


def is_ga_export(path: Path | str) -> bool:
    """Return whether ``path`` is a group-address export (its root is the export element).

    Cheap and tolerant: a parse failure or any other root means "not a GA export" so the
    caller can fall through to the other open/import paths rather than crash.
    """
    try:
        for _event, elem in ET.iterparse(path, events=("start",)):
            return elem.tag == _ROOT_TAG
    except ET.ParseError:
        return False
    return False


def read_ga_export(path: Path | str) -> GaExport:
    """Parse a GA export into its style, range tree and address list.

    The style is inferred from the address notation (``1/2/3`` is three-level, ``1/3``
    two-level, a bare number free); addresses are parsed against it. A ``<GroupAddress>``
    with an unparsable datapoint type keeps ``None`` rather than failing the whole import.
    """
    root = ET.parse(path).getroot()
    if root.tag != _ROOT_TAG:
        raise ValueError(f"{path} is not a group-address export (root {root.tag!r})")

    style = _detect_style(root)
    ranges = tuple(_read_range(child) for child in root.findall(_RANGE_TAG))
    addresses = tuple(_read_addresses(root, style))
    return GaExport(style=style, ranges=ranges, addresses=addresses)


def import_ga_export(
    source: Path | str,
    dest: Path | str,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
) -> str:
    """Parse ``source`` (a GA export) and write it as a new project at ``dest``.

    Returns the project id. An existing ``dest`` is overwritten (an import is a fresh
    project). The project has no devices - a GA export does not carry any. ``project_name``
    sets the project/building name; the caller passes the user-chosen name because ``dest``
    may be a temp file whose stem is not meaningful (see the GUI facade's atomic swap).
    """
    export = read_ga_export(source)
    pid = project_id or f"P-{uuid4().hex[:4].upper()}"
    dest_path = Path(dest)
    dest_path.unlink(missing_ok=True)
    name = project_name or dest_path.stem or "GA export"

    engine = make_engine(url_for(dest_path))
    try:
        with Session(engine) as session:
            seed_new_project(session, pid, name, export.style, name)
            installation = (
                session.query(Installation).filter_by(index=DEFAULT_INSTALLATION).one()
            )
            collected: list[GroupRange] = []
            _graft_ranges(installation, export.ranges, None, collected)
            placed = 0
            for addr in export.addresses:
                group_range = _range_for(collected, addr.address)
                if group_range is None:
                    continue  # a GA outside every range would violate the schema; skip it
                group_range.group_addresses.append(
                    GroupAddress(
                        address=addr.address,
                        name=addr.name,
                        datapoint_type=addr.datapoint_type,
                        description=addr.description,
                    )
                )
                placed += 1
            session.commit()
    finally:
        engine.dispose()
    logger.info(
        "ga-export import done: pid=%s ranges=%d addresses=%d placed=%d -> %s",
        pid,
        len(export.ranges),
        len(export.addresses),
        placed,
        dest,
    )
    return pid


def write_ga_export(source: Path | str, dest: Path | str) -> int:
    """Read the project at ``source`` (``.xknx``) and write its group-address tree to ``dest``.

    The inverse of :func:`import_ga_export`: only the group-address tree is written - the
    ``<GroupRange>`` folders with their address windows and the group addresses under them,
    each with name, datapoint type and description. No devices, topology or product data,
    matching what an ETS GA export carries. The range nesting is reproduced verbatim so the
    file re-imports to the same tree. Returns the number of addresses written. An existing
    ``dest`` is overwritten.
    """
    engine = make_engine(url_for(Path(source)))
    try:
        with Session(engine) as session:
            project = session.query(Project).one()
            style = GroupAddressStyle(project.group_address_style)
            roots = (
                session.query(GroupRange)
                .filter(GroupRange.parent_id.is_(None))
                .order_by(GroupRange.range_start)
                .all()
            )
            ET.register_namespace("", _NS)
            root_el = ET.Element(_ROOT_TAG)
            count = sum(_write_range(gr, root_el, style) for gr in roots)
    finally:
        engine.dispose()

    tree = ET.ElementTree(root_el)
    ET.indent(tree, space="  ")
    tree.write(Path(dest), encoding="utf-8", xml_declaration=True)
    logger.info("ga-export write done: addresses=%d -> %s", count, dest)
    return count


def _write_range(
    gr: GroupRange, parent_el: ET.Element, style: GroupAddressStyle
) -> int:
    """Emit ``gr`` (and its subtree) under ``parent_el``; return addresses written below it."""
    el = ET.SubElement(parent_el, _RANGE_TAG)
    el.set("Name", gr.name)
    el.set("RangeStart", str(gr.range_start))
    el.set("RangeEnd", str(gr.range_end))
    count = sum(
        _write_range(child, el, style)
        for child in sorted(gr.children, key=lambda c: c.range_start)
    )
    for ga in sorted(gr.group_addresses, key=lambda a: a.address):
        _write_address(ga, el, style)
        count += 1
    return count


def _write_address(
    ga: GroupAddress, parent_el: ET.Element, style: GroupAddressStyle
) -> None:
    el = ET.SubElement(parent_el, _ADDRESS_TAG)
    el.set("Name", ga.name)
    el.set("Address", format_ga(ga.address, style))
    if ga.datapoint_type:
        el.set("DPTs", ga.datapoint_type)
    if ga.description:
        el.set("Description", ga.description)


def _detect_style(root: ET.Element) -> GroupAddressStyle:
    """Infer the style from the widest address notation used (default three-level).

    An export with no addresses keeps the three-level default (the common ETS case).
    """
    slashes = 0
    seen = False
    for ga in root.iter(_ADDRESS_TAG):
        if not (ga.get("Address") or ""):
            continue
        seen = True
        slashes = max(slashes, (ga.get("Address") or "").count("/"))
        if slashes >= 2:
            break
    if not seen:
        return GroupAddressStyle.THREE_LEVEL
    if slashes >= 2:
        return GroupAddressStyle.THREE_LEVEL
    if slashes == 1:
        return GroupAddressStyle.TWO_LEVEL
    return GroupAddressStyle.FREE


def _read_range(elem: ET.Element) -> GaRange:
    return GaRange(
        name=elem.get("Name", ""),
        range_start=int(elem.get("RangeStart", "0")),
        range_end=int(elem.get("RangeEnd", "0")),
        children=tuple(_read_range(child) for child in elem.findall(_RANGE_TAG)),
    )


def _read_addresses(root: ET.Element, style: GroupAddressStyle) -> list[GaAddress]:
    addresses: list[GaAddress] = []
    for ga in root.iter(_ADDRESS_TAG):
        text = ga.get("Address")
        if not text:
            continue
        try:
            value = parse_ga(text, style)
        except ValueError:
            logger.warning("ga-export: skipping unparsable address %r", text)
            continue
        # Guard against out-of-bounds components silently aliasing a valid address (e.g. a
        # three-level 1/8/0 overflowing the 3-bit middle group onto 1/0/0): re-format and
        # require an exact round-trip, otherwise skip the address rather than store a wrong one.
        if format_ga(value, style) != text.strip():
            logger.warning("ga-export: skipping out-of-range address %r", text)
            continue
        addresses.append(
            GaAddress(
                address=value,
                name=ga.get("Name", ""),
                datapoint_type=_normalize_dpt(ga.get("DPTs")),
                description=ga.get("Description", ""),
            )
        )
    return addresses


def _normalize_dpt(token: str | None) -> str | None:
    """ETS export tokens pass straight through; a malformed one is dropped, not fatal."""
    try:
        return normalize_datapoint_type(token)
    except ValueError:
        logger.warning("ga-export: dropping unrecognized datapoint type %r", token)
        return None


def _graft_ranges(
    installation: Installation,
    ranges: tuple[GaRange, ...],
    parent: GroupRange | None,
    collected: list[GroupRange],
) -> None:
    for r in ranges:
        gr = GroupRange(
            range_start=r.range_start,
            range_end=r.range_end,
            name=r.name,
            parent=parent,
        )
        installation.group_ranges.append(gr)
        collected.append(gr)
        _graft_ranges(installation, r.children, gr, collected)


def _range_for(ranges: list[GroupRange], address: int) -> GroupRange | None:
    """The smallest (leaf) range that contains ``address``.

    ``ranges`` is in pre-order (parent before its children), so on an equal-width tie the
    later entry is the deeper one; ``<=`` keeps it, placing the address in the leaf.
    """
    best: GroupRange | None = None
    for gr in ranges:
        if gr.range_start <= address <= gr.range_end and (
            best is None
            or (gr.range_end - gr.range_start) <= (best.range_end - best.range_start)
        ):
            best = gr
    return best

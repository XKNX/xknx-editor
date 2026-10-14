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

:func:`read_group_address_file` also reads the CSV exports (tab, semicolon or comma
separated; the 9-column form with central, unfiltered, description, datapoint type and
security, and the 4-column form with names and address only).
"""

import csv
import io
import logging
import re
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


class GroupAddressStyleMismatch(ValueError):
    """A group address file uses another address style than the project it is imported into."""

    def __init__(
        self, file_style: GroupAddressStyle, project_style: GroupAddressStyle
    ) -> None:
        super().__init__(
            f"The file uses {file_style.value} addresses, the project {project_style.value}."
        )
        self.file_style = file_style
        self.project_style = project_style


@dataclass(frozen=True)
class GaRange:
    """A ``<GroupRange>`` folder: its name, address window and nested child ranges."""

    name: str
    range_start: int
    range_end: int
    children: tuple["GaRange", ...] = ()


@dataclass
class GaAddress:
    """A group address: raw 16-bit value, name, datapoint-type token, description, and the
    security mode (``On``/``Off``; ``None`` for the default ``Auto`` or when not given).
    ``central`` and ``unfiltered`` are ``None`` when the file does not carry them."""

    address: int
    name: str
    datapoint_type: str | None
    description: str
    security: str | None = None
    central: bool | None = None
    unfiltered: bool | None = None


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


def read_group_address_file(path: Path | str) -> GaExport:
    """Read a group address export, XML or CSV, into its style, range tree and addresses."""
    if is_ga_export(path):
        return read_ga_export(path)
    return read_ga_csv(path)


def read_ga_csv(path: Path | str) -> GaExport:
    """Parse a CSV group address export.

    Each row is ``main name, middle name, address name, address`` followed optionally by
    ``central, unfiltered, description, datapoint type, security``. A row whose address
    ends in ``/-`` is a main (``1/-/-``) or middle group (``1/2/-``); the others are group
    addresses. A header row is skipped. Raises ``ValueError`` for a file without any.
    """
    rows = _csv_rows(Path(path).read_bytes())
    addresses_text = [
        row[3].strip()
        for row in rows
        if len(row) >= 4 and _CSV_ADDRESS.match(row[3].strip())
    ]
    if not addresses_text:
        raise ValueError(f"{path} contains no group addresses")
    slashes = max(text.count("/") for text in addresses_text)
    style = (
        GroupAddressStyle.THREE_LEVEL
        if slashes >= 2
        else GroupAddressStyle.TWO_LEVEL
        if slashes == 1
        else GroupAddressStyle.FREE
    )

    mains: dict[int, tuple[str, dict[int, str]]] = {}
    addresses: list[GaAddress] = []
    for row in rows:
        if len(row) < 4:
            continue
        cells = [cell.strip() for cell in row] + [""] * (9 - len(row))
        text = cells[3]
        if not _CSV_ADDRESS.match(text):
            continue
        parts = text.split("/")
        if parts[-1] == "-":
            main = int(parts[0])
            name, middles = mains.setdefault(main, ("", {}))
            if len(parts) == 3 and parts[1] != "-":
                middles[int(parts[1])] = cells[1] or cells[0]
            else:
                mains[main] = (cells[0] or name, middles)
            continue
        try:
            value = parse_ga(text, style)
        except ValueError:
            logger.warning("ga csv: skipping unparsable address %r", text)
            continue
        if format_ga(value, style) != text:
            logger.warning("ga csv: skipping out-of-range address %r", text)
            continue
        addresses.append(
            GaAddress(
                address=value,
                name=cells[2] or cells[1] or cells[0],
                datapoint_type=_normalize_dpt(cells[7] or None),
                description=cells[6],
                security=_security(cells[8]),
                central=_flag(cells[4]) if len(row) > 4 else None,
                unfiltered=_flag(cells[5]) if len(row) > 5 else None,
            )
        )

    ranges: list[GaRange] = []
    if style is not GroupAddressStyle.FREE:
        for main in sorted(mains):
            name, middles = mains[main]
            base = main << 11
            children: tuple[GaRange, ...] = ()
            if style is GroupAddressStyle.THREE_LEVEL:
                children = tuple(
                    GaRange(
                        name=middles[middle],
                        range_start=max(1, base + (middle << 8)),
                        range_end=base + (middle << 8) + 0xFF,
                    )
                    for middle in sorted(middles)
                )
            ranges.append(
                GaRange(
                    name=name,
                    range_start=max(1, base),
                    range_end=base + 0x7FF,
                    children=children,
                )
            )
    return GaExport(style=style, ranges=tuple(ranges), addresses=tuple(addresses))


_CSV_ADDRESS = re.compile(r"^\d+(/(\d+|-)){0,2}$")


def _csv_rows(data: bytes) -> list[list[str]]:
    """Decode (UTF-8 with or without BOM, else Windows-1252) and split a CSV export."""
    if data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max("\t;,", key=first.count)
    return list(csv.reader(io.StringIO(text), delimiter=delimiter, quotechar='"'))


def _security(text: str | None) -> str | None:
    """``On``/``Off`` as given; ``Auto`` (the default) and anything else are ``None``."""
    value = (text or "").strip().capitalize()
    return value if value in ("On", "Off") else None


def _flag(text: str) -> bool | None:
    """A yes/no column; ``None`` when empty."""
    value = text.strip().lower()
    if not value:
        return None
    return value in ("1", "true", "yes", "x", "ja", "on")


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
    export = read_group_address_file(source)
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
                        security_mode=addr.security,
                        central=bool(addr.central),
                        unfiltered=bool(addr.unfiltered),
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
    # Byte layout of the exports this format comes from: UTF-8 BOM, standalone declaration, CRLF.
    body = ET.tostring(root_el, encoding="unicode")
    declaration = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'
    text = f"{declaration}\n{body}".replace("\n", "\r\n")
    Path(dest).write_bytes(b"\xef\xbb\xbf" + text.encode())
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
    if ga.security_mode and ga.security_mode != "Auto":
        el.set("Security", ga.security_mode)


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
        central, unfiltered = ga.get("Central"), ga.get("Unfiltered")
        addresses.append(
            GaAddress(
                address=value,
                name=ga.get("Name", ""),
                datapoint_type=_normalize_dpt(ga.get("DPTs")),
                description=ga.get("Description", ""),
                security=_security(ga.get("Security")),
                central=None if central is None else _flag(central),
                unfiltered=None if unfiltered is None else _flag(unfiltered),
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

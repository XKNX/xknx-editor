"""Remove product data no device uses from a project's stored manufacturer members.

A project imported from a ``.knxproj`` keeps the archive's manufacturer folders (``M-XXXX/*`` and
``M-XXXX.signature``) and writes them back on export. They usually carry far more than the devices
use. :func:`prune_product_members` (a ``{path: bytes}`` map) and :func:`prune_product_blob` (the
stored ZIP) drop, per manufacturer, everything no device references:

- products and ``Hardware2Program`` entries no device uses, and hardware left without either;
- application program files no kept ``Hardware2Program`` (or device) refers to;
- catalog items and catalog sections that pointed at removed data only;
- translation units/elements of removed elements;
- baggages that only removed data referenced;
- whole manufacturers without a used product, plus their ``.signature``.

XML is edited by cutting the removed elements out of the original bytes, so the encoding, BOM,
declaration and formatting of what remains stay untouched. Folder signatures are not touched here:
the export re-signs any folder whose content changed. A manufacturer folder whose data is not
understood (no ``Hardware.xml``, non UTF-8, unparsable) is left unchanged.
"""

from __future__ import annotations

import copy
import html
import io
import re
import struct
import xml.parsers.expat
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

# (step, done, total): ``step`` is one of the STEP_* keys, a manufacturer step carries the id
# after a colon (``"manufacturer:M-00FA"``).
ProgressCallback = Callable[[str, int, int], None]

STEP_DEVICES = "devices"
STEP_MANUFACTURER = "manufacturer"
STEP_STORE = "store"
STEP_VACUUM = "vacuum"

_BAGGAGE_REF = re.compile(rb"M-[0-9A-Fa-f]{4}_BG-[^\"'<>\s]+")
# Containers removed together with their last element child.
_PRUNABLE_CONTAINERS = frozenset(
    {
        "Products",
        "Hardware2Programs",
        "CatalogSection",
        "TranslationUnit",
        "Language",
        "Languages",
    }
)


@dataclass
class ProductPruneReport:
    """What a prune removed (or would remove).

    The id lists name the removed elements; ``baggages`` and ``removed_members`` are archive paths.
    ``names`` maps removed ids to their display names, where the product data has one.
    ``size_before``/``size_after`` are the total member bytes for a mapping and the ZIP size for a
    blob."""

    application_programs: list[str] = field(default_factory=list[str])
    hardware: list[str] = field(default_factory=list[str])
    products: list[str] = field(default_factory=list[str])
    catalog_items: list[str] = field(default_factory=list[str])
    baggages: list[str] = field(default_factory=list[str])
    manufacturers: list[str] = field(default_factory=list[str])
    removed_members: list[str] = field(default_factory=list[str])
    changed_members: list[str] = field(default_factory=list[str])
    names: dict[str, str] = field(default_factory=dict[str, str])
    size_before: int = 0
    size_after: int = 0

    @property
    def nothing_removed(self) -> bool:
        return not self.removed_members and not self.changed_members


@dataclass(eq=False)
class _Element:
    name: str
    attrs: dict[str, str]
    start: int
    end: int = -1
    parent: _Element | None = None
    children: list[_Element] = field(default_factory=list["_Element"])

    def iter(self) -> Iterable[_Element]:
        yield self
        for child in self.children:
            yield from child.iter()

    def ids(self) -> set[str]:
        return {el.attrs["Id"] for el in self.iter() if "Id" in el.attrs}


def _tag_end(data: bytes, start: int) -> int:
    """Index just past the ``>`` closing the tag that starts at ``start`` (quotes respected)."""
    quote = 0
    i = start + 1
    n = len(data)
    while i < n:
        c = data[i]
        if quote:
            if c == quote:
                quote = 0
        elif c in (0x22, 0x27):
            quote = c
        elif c == 0x3E:
            return i + 1
        i += 1
    raise ValueError("unterminated tag")


def _parse(data: bytes) -> _Element | None:
    """Parse ``data`` into an element tree with byte spans, or ``None`` if not handled."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    parser = xml.parsers.expat.ParserCreate(namespace_separator="}")
    stack: list[_Element] = []
    root: list[_Element] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        pos = parser.CurrentByteIndex
        el = _Element(name.rsplit("}", 1)[-1], attrs, pos)
        tag_end = _tag_end(data, pos)
        if data[tag_end - 2 : tag_end] == b"/>":
            el.end = tag_end
        if stack:
            el.parent = stack[-1]
            stack[-1].children.append(el)
        else:
            root.append(el)
        stack.append(el)

    def end(_name: str) -> None:
        el = stack.pop()
        if el.end < 0:
            pos = parser.CurrentByteIndex
            el.end = data.index(b">", pos) + 1

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except (xml.parsers.expat.ExpatError, ValueError):
        return None
    return root[0] if root else None


def _cut(data: bytes, elements: Iterable[_Element]) -> bytes:
    """Remove ``elements`` from ``data``, with their line when they stand alone on it."""
    spans = sorted((el.start, el.end) for el in elements)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            continue  # nested in an element already removed
        line_start = start
        while line_start > 0 and data[line_start - 1] in b" \t":
            line_start -= 1
        if line_start == 0 or data[line_start - 1] in b"\r\n":
            line_end = end
            while line_end < len(data) and data[line_end] in b" \t":
                line_end += 1
            if data.startswith(b"\r\n", line_end):
                start, end = line_start, line_end + 2
            elif data.startswith(b"\n", line_end):
                start, end = line_start, line_end + 1
        merged.append((start, end))
    out = bytearray()
    pos = 0
    for start, end in merged:
        out += data[pos:start]
        pos = end
    out += data[pos:]
    return bytes(out)


def _close_containers(removed: list[_Element]) -> list[_Element]:
    """Add each prunable container whose element children are now all removed."""
    gone = {id(el) for el in removed}
    result = list(removed)
    queue = list(removed)
    while queue:
        parent = queue.pop().parent
        if (
            parent is None
            or id(parent) in gone
            or parent.name not in _PRUNABLE_CONTAINERS
            or not all(id(child) in gone for child in parent.children)
        ):
            continue
        gone.add(id(parent))
        result.append(parent)
        queue.append(parent)
    return result


def _translation_removals(root: _Element, removed_ids: set[str]) -> list[_Element]:
    """Translation units/elements that translate one of ``removed_ids``."""
    return [
        el
        for el in root.iter()
        if el.name in ("TranslationUnit", "TranslationElement")
        and el.attrs.get("RefId") in removed_ids
    ]


def _baggage_refs(data: bytes) -> set[bytes]:
    return set(_BAGGAGE_REF.findall(data))


def _baggage_path(mid: str, el: _Element) -> str:
    target = el.attrs.get("TargetPath", "").replace("\\", "/").strip("/")
    name = el.attrs.get("Name", "")
    return f"{mid}/Baggages/{target}/{name}" if target else f"{mid}/Baggages/{name}"


@dataclass
class _Plan:
    removed: set[str] = field(default_factory=set[str])
    replaced: dict[str, bytes] = field(default_factory=dict[str, bytes])
    report: ProductPruneReport = field(default_factory=ProductPruneReport)


class _Folder:
    """Prune one manufacturer folder."""

    def __init__(
        self,
        mid: str,
        names: list[str],
        read: Callable[[str], bytes],
        products: frozenset[str],
        programs: frozenset[str],
        applications: frozenset[str],
        plan: _Plan,
    ) -> None:
        self.mid = mid
        self.names = names
        self.read = read
        self.products = products
        self.programs = programs
        self.applications = applications
        self.plan = plan

    def _app_files(self) -> dict[str, str]:
        prefix = f"{self.mid}/{self.mid}_A-"
        return {
            name[len(self.mid) + 1 : -4]: name
            for name in self.names
            if name.startswith(prefix)
            and name.endswith(".xml")
            and name.count("/") == 1
        }

    def run(self) -> None:
        mid = self.mid
        hw_name = f"{mid}/Hardware.xml"
        if hw_name not in self.names:
            return
        hw_data = self.read(hw_name)
        hw_root = _parse(hw_data)
        if hw_root is None:
            return

        removed_hw: list[_Element] = []
        removed_ids: set[str] = set()
        kept_any = False
        kept_refs: set[str] = set()
        report = ProductPruneReport()
        for hw in hw_root.iter():
            if hw.name != "Hardware" or "Id" not in hw.attrs:
                continue
            products = [el for el in hw.iter() if el.name == "Product"]
            programs = [el for el in hw.iter() if el.name == "Hardware2Program"]
            used_products = [p for p in products if p.attrs.get("Id") in self.products]
            used_programs = [p for p in programs if p.attrs.get("Id") in self.programs]
            if not used_products and not used_programs:
                removed_hw.append(hw)
                report.hardware.append(hw.attrs["Id"])
                _name(report, hw, "Name")
                report.products += [p.attrs.get("Id", "") for p in products]
                for el in products:
                    _name(report, el, "Text")
                removed_ids |= hw.ids()
                continue
            kept_any = True
            for el in products:
                if el not in used_products:
                    removed_hw.append(el)
                    report.products.append(el.attrs.get("Id", ""))
                    _name(report, el, "Text")
                    removed_ids |= el.ids()
            for el in programs:
                if el in used_programs:
                    kept_refs |= {
                        sub.attrs["RefId"] for sub in el.iter() if "RefId" in sub.attrs
                    }
                else:
                    removed_hw.append(el)
                    removed_ids |= el.ids()

        apps = self._app_files()
        used_apps = (kept_refs | self.applications) & set(apps)
        if not kept_any and not used_apps:
            if any(
                ref.startswith(f"{mid}_")
                for ref in self.products | self.programs | self.applications
            ):
                return  # used by id, but not found in this data: leave it alone
            self._drop_folder(report, apps)
            return
        if not removed_hw and len(used_apps) == len(apps):
            return

        removed_content: list[bytes] = []
        changed: dict[str, bytes] = {}
        hw_removals = _close_containers(
            removed_hw + _translation_removals(hw_root, removed_ids)
        )
        if hw_removals:
            changed[hw_name] = _cut(hw_data, hw_removals)
            removed_content += [hw_data[el.start : el.end] for el in hw_removals]

        removed_files: set[str] = set()
        for app_id, name in apps.items():
            if app_id not in used_apps:
                removed_files.add(name)
                report.application_programs.append(app_id)
                data = self.read(name)
                _application_name(report, app_id, data)
                removed_content.append(data)

        cat_name = f"{mid}/Catalog.xml"
        if cat_name in self.names:
            cat_data = self.read(cat_name)
            cat_root = _parse(cat_data)
            if cat_root is not None:
                items = [
                    el
                    for el in cat_root.iter()
                    if el.name == "CatalogItem"
                    and (
                        el.attrs.get("ProductRefId") in removed_ids
                        or el.attrs.get("Hardware2ProgramRefId") in removed_ids
                    )
                ]
                report.catalog_items += [el.attrs.get("Id", "") for el in items]
                for el in items:
                    _name(report, el, "Name")
                cat_removals = _close_containers(items)
                cat_ids = {i for el in cat_removals for i in el.ids()}
                cat_removals = _close_containers(
                    cat_removals + _translation_removals(cat_root, cat_ids)
                )
                if cat_removals:
                    changed[cat_name] = _cut(cat_data, cat_removals)
                    removed_content += [
                        cat_data[el.start : el.end] for el in cat_removals
                    ]

        self._prune_baggages(removed_content, removed_files, changed, report)

        self.plan.removed |= removed_files
        self.plan.replaced.update(changed)
        self._merge(report)

    def _prune_baggages(
        self,
        removed_content: list[bytes],
        removed_files: set[str],
        changed: dict[str, bytes],
        report: ProductPruneReport,
    ) -> None:
        bag_name = f"{self.mid}/Baggages.xml"
        if bag_name not in self.names:
            return
        candidates: set[bytes] = set()
        for data in removed_content:
            candidates |= _baggage_refs(data)
        if not candidates:
            return
        still: set[bytes] = set()
        for name in self.names:
            if (
                name.endswith(".xml")
                and name != bag_name
                and name not in removed_files
                and "/Baggages/" not in name
            ):
                data = changed.get(name)
                still |= _baggage_refs(self.read(name) if data is None else data)
        drop = {c.decode("utf-8", "replace") for c in candidates - still}
        if not drop:
            return
        bag_data = self.read(bag_name)
        bag_root = _parse(bag_data)
        if bag_root is None:
            return
        baggages = [el for el in bag_root.iter() if el.name == "Baggage"]
        gone = [el for el in baggages if el.attrs.get("Id") in drop]
        if not gone:
            return
        for el in gone:
            path = _baggage_path(self.mid, el)
            if path in self.names:
                removed_files.add(path)
                report.baggages.append(path)
        if len(gone) == len(baggages):
            removed_files.add(bag_name)
        else:
            changed[bag_name] = _cut(bag_data, gone)

    def _drop_folder(self, report: ProductPruneReport, apps: dict[str, str]) -> None:
        mid = self.mid
        report.manufacturers.append(mid)
        report.application_programs += sorted(apps)
        for app_id, name in apps.items():
            _application_name(report, app_id, self.read(name))
        cat_name = f"{mid}/Catalog.xml"
        if cat_name in self.names:
            cat_root = _parse(self.read(cat_name))
            if cat_root is not None:
                items = [el for el in cat_root.iter() if el.name == "CatalogItem"]
                report.catalog_items += [el.attrs.get("Id", "") for el in items]
                for el in items:
                    _name(report, el, "Name")
        report.baggages += [n for n in self.names if n.startswith(f"{mid}/Baggages/")]
        self.plan.removed |= set(self.names)
        self._merge(report)

    def _merge(self, report: ProductPruneReport) -> None:
        total = self.plan.report
        total.application_programs += report.application_programs
        total.hardware += report.hardware
        total.products += report.products
        total.catalog_items += report.catalog_items
        total.baggages += report.baggages
        total.manufacturers += report.manufacturers
        total.names.update(report.names)


def _name(report: ProductPruneReport, element: _Element, attribute: str) -> None:
    """Record the display name of a removed element, if it has one."""
    element_id = element.attrs.get("Id")
    text = element.attrs.get(attribute, "").strip()
    if element_id and text:
        report.names[element_id] = text


_APPLICATION_TAG = re.compile(rb"<ApplicationProgram\s[^>]*>")
_ATTRIBUTE = re.compile(rb'\s(Name|ApplicationVersion)="([^"]*)"')


def _application_name(report: ProductPruneReport, app_id: str, data: bytes) -> None:
    """Record ``Name`` and version of the application program in ``data``."""
    tag = _APPLICATION_TAG.search(data, 0, 65536)
    if tag is None:
        return
    attrs = {
        k.decode(): v.decode("utf-8", "replace")
        for k, v in _ATTRIBUTE.findall(tag.group(0))
    }
    name = html.unescape(attrs.get("Name", "")).strip()
    if not name:
        return
    version = attrs.get("ApplicationVersion", "")
    if version.isdigit():
        number = int(version)
        name = f"{name} V{number >> 4}.{number & 0x0F}"
    report.names[app_id] = name


def _plan(
    names: list[str],
    read: Callable[[str], bytes],
    product_ids: Iterable[str],
    hardware2program_ids: Iterable[str],
    application_ids: Iterable[str],
    progress: ProgressCallback | None,
) -> _Plan:
    products = frozenset(i for i in product_ids if i)
    programs = frozenset(i for i in hardware2program_ids if i)
    applications = frozenset(i for i in application_ids if i)
    folders: dict[str, list[str]] = {}
    for name in names:
        top = name.split("/", 1)[0]
        if "/" in name and top.startswith("M-"):
            folders.setdefault(top, []).append(name)
    plan = _Plan()
    total = len(folders)
    for done, mid in enumerate(sorted(folders)):
        if progress is not None:
            progress(f"{STEP_MANUFACTURER}:{mid}", done, total)
        _Folder(mid, folders[mid], read, products, programs, applications, plan).run()
        if mid in plan.report.manufacturers and f"{mid}.signature" in names:
            plan.removed.add(f"{mid}.signature")
    if progress is not None and total:
        progress(STEP_MANUFACTURER, total, total)
    plan.report.removed_members = sorted(plan.removed)
    plan.report.changed_members = sorted(set(plan.replaced) - plan.removed)
    return plan


def prune_product_members(
    members: Mapping[str, bytes],
    product_ids: Iterable[str],
    hardware2program_ids: Iterable[str],
    application_ids: Iterable[str] = (),
    *,
    progress: ProgressCallback | None = None,
) -> tuple[dict[str, bytes], ProductPruneReport]:
    """Return ``members`` without the product data no device uses, plus a report.

    ``product_ids``/``hardware2program_ids`` are the devices' ``ProductRefId``s and
    ``Hardware2ProgramRefId``s; ``application_ids`` are application program ids devices name
    directly (the ones a kept ``Hardware2Program`` refers to are always kept)."""
    names = list(members)
    plan = _plan(
        names,
        members.__getitem__,
        product_ids,
        hardware2program_ids,
        application_ids,
        progress,
    )
    out = {
        name: plan.replaced.get(name, data)
        for name, data in members.items()
        if name not in plan.removed
    }
    plan.report.size_before = sum(len(d) for d in members.values())
    plan.report.size_after = sum(len(d) for d in out.values())
    return out, plan.report


def prune_product_blob(
    blob: bytes,
    product_ids: Iterable[str],
    hardware2program_ids: Iterable[str],
    application_ids: Iterable[str] = (),
    *,
    progress: ProgressCallback | None = None,
) -> tuple[bytes, ProductPruneReport]:
    """Like :func:`prune_product_members` for the stored ZIP blob.

    Members are read on demand and unchanged ones are copied without recompression. Returns
    ``blob`` itself when nothing is removed."""
    with zipfile.ZipFile(io.BytesIO(blob)) as src:
        names = [n for n in src.namelist() if not n.endswith("/")]
        plan = _plan(
            names,
            src.read,
            product_ids,
            hardware2program_ids,
            application_ids,
            progress,
        )
        plan.report.size_before = len(blob)
        if plan.report.nothing_removed:
            plan.report.size_after = len(blob)
            return blob, plan.report
        out = _rewrite_zip(blob, src, plan.removed, plan.replaced)
    plan.report.size_after = len(out)
    return out, plan.report


def _rewrite_zip(
    blob: bytes,
    src: zipfile.ZipFile,
    removed: set[str],
    replaced: Mapping[str, bytes],
) -> bytes:
    """Write ``src`` without ``removed`` and with ``replaced`` content; copy the rest raw."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            name = info.filename
            if name in removed or name.endswith("/"):
                continue
            if name in replaced:
                fresh = zipfile.ZipInfo(name, info.date_time)
                fresh.compress_type = zipfile.ZIP_DEFLATED
                fresh.external_attr = info.external_attr
                out.writestr(fresh, replaced[name])
                continue
            start = info.header_offset
            header = blob[start : start + 30]
            if header[:4] != b"PK\x03\x04" or info.flag_bits & 0x08:
                out.writestr(copy.copy(info), src.read(name))
                continue
            name_len, extra_len = struct.unpack("<HH", header[26:30])
            end = start + 30 + name_len + extra_len + info.compress_size
            fp = out.fp
            assert fp is not None
            offset = fp.tell()
            fp.write(blob[start:end])
            copied = copy.copy(info)
            copied.header_offset = offset
            out.filelist.append(copied)
            out.NameToInfo[name] = copied
            out.start_dir = fp.tell()
    return buffer.getvalue()

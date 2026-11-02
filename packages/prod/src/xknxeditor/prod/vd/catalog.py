"""``Catalog.xml``: catalog sections (functional entities) and catalog items (virtual devices)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ids import fragment
from .language import Translations, translate_entity
from .netsort import list_sort
from .text import from_db
from .xmlout import El

if TYPE_CHECKING:
    from .context import Context
    from .database import Row


class CatalogDoc:
    """The catalog of one manufacturer."""

    def __init__(self, manufacturer: str) -> None:
        self.manufacturer = manufacturer
        self.sections: list[El] = []
        self.by_id: dict[str, El] = {}
        self.translations = Translations()

    def to_xml(self) -> El:
        m = El("Manufacturer", {"RefId": self.manufacturer})
        if self.sections:
            m.sub("Catalog").children.extend(self.sections)
        languages = self.translations.to_xml()
        if languages is not None:
            m.add(languages)
        return m


def _append_section(
    ctx: Context, doc: CatalogDoc, fe_id: int, parent: El | None
) -> El | None:
    row = ctx.first("functional_entity", "FUNCTIONAL_ENTITY_ID", fe_id)
    if row is None:
        return None
    lang = ctx.default_language
    number = row.str("FUNCTIONAL_ENTITY_NUMB") or ""
    if not number:
        number = f"Generated-{fe_id}"
    if parent is None:
        ident = f"{doc.manufacturer}_CS-{fragment(number)}"
    else:
        ident = f"{parent['Id']}-{fragment(number)}"
    el = El(
        "CatalogSection",
        {
            "Id": ident,
            "Name": from_db(lang, row.str("FUNCTIONAL_ENTITY_NAME")),
            "Number": number,
            "VisibleDescription": from_db(
                lang, row.str("FUNCTIONAL_ENTITY_DESCRIPTION")
            ),
        },
    )
    if parent is None:
        doc.sections.append(el)
    else:
        # sub sections come before the items of a section
        items = [c for c in parent.children if c.tag == "CatalogItem"]
        sections = [c for c in parent.children if c.tag == "CatalogSection"]
        parent.children = [*sections, el, *items]
    doc.by_id.setdefault(ident, el)
    el["DefaultLanguage"] = lang
    translate_entity(ctx, doc.translations, fe_id, "CatalogSection", el, ident, ident)
    return el


def _insert_section(ctx: Context, doc: CatalogDoc, fe: Row) -> El | None:
    ids = _section_ids(ctx, fe)
    section: El | None = None
    if not ids:
        return None
    root = ids.pop()
    ref = ctx.refid(root)
    if ref is not None and ref in doc.by_id:
        section = doc.by_id[ref]
    else:
        section = _append_section(ctx, doc, root, None)
        if section is None:
            return None
        ctx.add_refid(root, section["Id"])
    while ids:
        current = ids.pop()
        ref = ctx.refid(current)
        if ref is None or ref not in doc.by_id:
            child = _append_section(ctx, doc, current, section)
            if child is None:
                return None
            ctx.add_refid(current, child["Id"])
            section = child
        else:
            section = doc.by_id[ref]
    return section


def _section_ids(ctx: Context, fe: Row) -> list[int]:
    ids: list[int] = []
    entity: Row | None = fe
    if entity["FUNCTIONAL_ENTITY_ID"] is not None:
        ids.append(int(entity["FUNCTIONAL_ENTITY_ID"] or 0))
    while entity is not None and entity["FUN_FUNCTIONAL_ENTITY_ID"] is not None:
        parent = int(entity["FUN_FUNCTIONAL_ENTITY_ID"] or 0)
        ids.append(parent)
        entity = ctx.first("functional_entity", "FUNCTIONAL_ENTITY_ID", parent)
    return ids


def _same_programs(p2p: Row, vd: Row) -> bool:
    def s(v: object) -> str:
        return "" if v is None else str(v)

    return s(p2p["PROGRAM_ID"]) == s(vd["PROGRAM_ID"]) and s(
        p2p["PEI_PROGRAM_ID"]
    ) == s(vd["PEI_PROGRAM_ID"])


def _append_item(ctx: Context, doc: CatalogDoc, vd: Row, section: El) -> None:
    number = vd["VIRTUAL_DEVICE_NUMBER"]
    if number is None:
        ctx.errors.append(f"CatalogItemNotConvertable: {vd['VIRTUAL_DEVICE_ID']}")
        return
    lang = ctx.default_language
    item = El("CatalogItem")
    item["Name"] = from_db(lang, vd.str("VIRTUAL_DEVICE_NAME"))
    item["Number"] = str(int(number))
    entry = ctx.first("catalog_entry", "CATALOG_ENTRY_ID", vd["CATALOG_ENTRY_ID"])
    if entry is None:
        return
    if vd["VIRTUAL_DEVICE_DESCRIPTION"] is not None:
        item["VisibleDescription"] = from_db(lang, vd.str("VIRTUAL_DEVICE_DESCRIPTION"))
    product = ctx.refid(entry["CATALOG_ENTRY_ID"])
    if product is None:
        ctx.warnings.append(
            f"NoProductRefIdFound: No ProductRefId found for 'virtual_device' DbId {vd['VIRTUAL_DEVICE_ID']}"
        )
    else:
        item["ProductRefId"] = product
    h2p: str | None = None
    for p2p in ctx.select("product_to_program", "PRODUCT_ID", entry["PRODUCT_ID"]):
        if not _same_programs(p2p, vd) or p2p["PROD2PROG_ID"] is None:
            continue
        h2p = ctx.refid(p2p["PROD2PROG_ID"])
        if h2p is not None:
            break
    if h2p is None or product is None:
        return
    item["Hardware2ProgramRefId"] = h2p
    ident = f"{h2p}_CI-{product[product.rfind('-') + 1 :]}-{item['Number']}"
    item["Id"] = ident
    if any(c.get("Id") == ident for c in section.children if c.tag == "CatalogItem"):
        return
    section.children.append(item)
    item["DefaultLanguage"] = lang
    vd_id = int(vd["VIRTUAL_DEVICE_ID"] or 0)
    translate_entity(ctx, doc.translations, vd_id, "CatalogItem", item, ident, ident)


def _empty_sections(ctx: Context, doc: CatalogDoc, number: int) -> None:
    """Sections of functional entities without devices (and without sub entities)."""
    entities = ctx.select("functional_entity", "MANUFACTURER_ID", number)
    leaves: list[int] = []
    for fe in entities:
        if fe["FUNCTIONAL_ENTITY_ID"] is not None:
            ident = int(fe["FUNCTIONAL_ENTITY_ID"] or 0)
            if ident not in leaves:
                leaves.append(ident)
    used = {int(v["FUNCTIONAL_ENTITY_ID"] or 0) for v in ctx.rows("virtual_device")}
    leaves = [x for x in leaves if x not in used]
    parents = {int(fe["FUN_FUNCTIONAL_ENTITY_ID"] or 0) for fe in entities}
    leaves = [x for x in leaves if x not in parents]
    for ident in leaves:
        _section_with_parents(ctx, doc, ident)


def _section_with_parents(ctx: Context, doc: CatalogDoc, fe_id: int) -> None:
    row = ctx.first("functional_entity", "FUNCTIONAL_ENTITY_ID", fe_id)
    if row is None:
        return
    parent_id = row["FUN_FUNCTIONAL_ENTITY_ID"]
    if parent_id is None:
        el = _append_section(ctx, doc, fe_id, None)
    else:
        ref = ctx.refid(parent_id)
        if not ref:
            _section_with_parents(ctx, doc, int(parent_id))
            ref = ctx.refid(parent_id)
            if not ref:
                return
        parent = doc.by_id.get(ref)
        if parent is None:
            return
        el = _append_section(ctx, doc, fe_id, parent)
    if el is not None:
        ctx.add_refid(fe_id, el["Id"])


def convert_catalog(ctx: Context) -> list[CatalogDoc]:
    """The catalog documents, one per manufacturer of the virtual devices."""
    mapping: list[tuple[int, Row]] = []
    for vd in ctx.rows("virtual_device"):
        entry = ctx.first("catalog_entry", "CATALOG_ENTRY_ID", vd["CATALOG_ENTRY_ID"])
        if entry is None:
            continue
        mapping.append((int(entry["MANUFACTURER_ID"] or 0), vd))
    list_sort(mapping, lambda a, b: (a[0] > b[0]) - (a[0] < b[0]))
    docs: list[CatalogDoc] = []
    doc: CatalogDoc | None = None
    current = -1
    for number, vd in mapping:
        if not ctx.known_manufacturer(ctx.manufacturer_ref(number)):
            ctx.errors.append(f"UnknownManufacturerId: {number}")
            continue
        if number != current or doc is None:
            doc = CatalogDoc(ctx.manufacturer_ref(number))
            docs.append(doc)
            current = number
        ctx.default_language = ctx.language_of(doc.manufacturer)
        fe = ctx.first(
            "functional_entity", "FUNCTIONAL_ENTITY_ID", vd["FUNCTIONAL_ENTITY_ID"]
        )
        section = _insert_section(ctx, doc, fe) if fe is not None else None
        if section is None:
            continue
        _append_item(ctx, doc, vd, section)
    if doc is not None:
        _empty_sections(ctx, doc, current)
    return docs

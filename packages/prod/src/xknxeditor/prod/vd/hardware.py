"""``Hardware.xml``: hardware (hw_product), products (catalog_entry) and Hardware2Program."""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import TYPE_CHECKING

from .ids import fragment, hardware2program_id, hardware_id
from .language import Translations, translate_entity
from .text import from_db
from .xmlout import El, boolean, float32

if TYPE_CHECKING:
    from .context import Context
    from .database import Row
    from .dump import Value

_STATUS = {
    10: "Registered",
    12: "Registered",
    20: "Certified",
    22: "Certified",
    30: "FutureUseNotRecommended",
    32: "FutureUseNotRecommended",
    40: "FutureUseNotAllowed",
    42: "FutureUseNotAllowed",
}
_STATUS_RANK = {
    "Unregistered": 0,
    "Registered": 1,
    "Certified": 2,
    "FutureUseNotRecommended": 3,
    "FutureUseNotAllowed": 4,
}
_DATE = re.compile(r"^\s*(\d{4})[./-](\d{1,2})[./-](\d{1,2})")
# a date with the year last is read month first (invariant culture)
_DATE_MDY = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})")


class HardwareDoc:
    """The hardware of one manufacturer."""

    def __init__(self, manufacturer: str) -> None:
        self.manufacturer = manufacturer
        self.hardware: list[El] = []
        self.by_id: dict[str, El] = {}
        self.translations = Translations()

    def to_xml(self) -> El:
        m = El("Manufacturer", {"RefId": self.manufacturer})
        if self.hardware:
            m.sub("Hardware").children.extend(self.hardware)
        languages = self.translations.to_xml()
        if languages is not None:
            m.add(languages)
        return m


def parse_date(value: Value) -> dt.date | None:
    if value is None:
        return None
    text = str(value)
    m = _DATE.match(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _DATE_MDY.match(text)
        if not m:
            return None
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def registration_info(row: Row, status_column: str, *, for_h2p: bool) -> El:
    """A RegistrationInfo from the registration columns of a catalog entry or product_to_program."""
    info = El("RegistrationInfo")
    status = "Unregistered"
    if row[status_column] is not None:
        status = _STATUS.get(int(row[status_column] or 0), "Unregistered")
    info["RegistrationStatus"] = status
    date = parse_date(row["REGISTRATION_DATE"])
    if date is not None and dt.date(1990, 1, 1) <= date <= dt.date.today():
        info["RegistrationDate"] = date.isoformat()
    if for_h2p:
        orig_number = row["ORIGINAL_REGISTRATION_NUMBER"]
        orig_year = row["ORIGINAL_REGISTRATION_YEAR"]
        if orig_number is not None or orig_year is not None:
            year = 1900 + (int(orig_year) if orig_year is not None else -1900)
            info["OriginalRegistrationNumber"] = f"{year}/{int(orig_number or 0)}"
    number = row["REGISTRATION_NUMBER"]
    year = row["REGISTRATION_YEAR"]
    if number is not None and year is not None and status != "Unregistered":
        info["RegistrationNumber"] = f"{1900 + int(year)}/{int(number)}"
    return info


def _registration_number(text: str | None) -> int:
    if not text:
        return 0
    pos = text.find("/")
    if pos < 0:
        return 0
    digits = text[pos + 1 :]
    if not digits.isdigit():
        return -1 if digits else 0
    return int(digits)


def compare_registration(a: El | None, b: El | None) -> int:
    if a is None:
        return 0 if b is None else -1
    if b is None:
        return 1
    diff = _registration_number(a.get("RegistrationNumber")) - _registration_number(
        b.get("RegistrationNumber")
    )
    if diff == 0:
        diff = (
            _STATUS_RANK[a["RegistrationStatus"]]
            - _STATUS_RANK[b["RegistrationStatus"]]
        )
    return diff


def _original_manufacturer(
    ctx: Context, hw: Row, serial: str, manufacturer: str
) -> str | None:
    value = hw["ORIGINAL_MANUFACTURER_ID"]
    original: str | None = None
    if value is not None:
        original = f"M-{int(value):04X}"
        if original == "M-0044" and serial == "9 // TG052":
            original = "M-00C9"
        if original == "M-003C" and str(hw["MANUFACTURER_ID"]) == "126":
            original = None
        if original == "M-0044" and str(hw["MANUFACTURER_ID"]) == "201":
            original = None
        if original in ("M-00DC", "M-00FA"):
            original = None
        if original == "M-0041":
            ctx.errors.append(
                f"UnknownManufacturerId: OriginalManufacturer of Hardware {serial}"
            )
        elif original is not None and original not in ctx.master.ids:
            from .context import ConversionError

            raise ConversionError(
                f"Hardware: {serial}, OriginalManufacturer: {original}"
            )
    if manufacturer == "M-0004" and serial in ("4 // RCD 3094 M", "4 // RCD 3096 M"):
        original = "M-00F4"
    return original


def _append_hardware(ctx: Context, doc: HardwareDoc, hw: Row) -> El:
    flags = (
        int(hw["COMPONENT_ATTRIBUTES"] or 0)
        if hw["COMPONENT_ATTRIBUTES"] is not None
        else 0
    )
    version = 0
    if hw["PRODUCT_VERSION_NUMBER"] is not None:
        version = int(hw["PRODUCT_VERSION_NUMBER"] or 0)
        if version >= 32768:
            ctx.warnings.append(f"HardwareVersionNumberTooBig: {version}")
            version = 0
    name = hw.str("PRODUCT_NAME")
    if name is None:
        name = f"GeneratedName-{hw['PRODUCT_ID']}"
    serial = hw.str("PRODUCT_SERIAL_NUMBER")
    if serial is None:
        serial = f"GeneratedNumber-{hw['PRODUCT_ID']}"
    el = El("Hardware")
    el["Name"] = name
    el["SerialNumber"] = serial
    el["VersionNumber"] = str(version)
    if hw["BUS_CURRENT"] is not None:
        current = int(hw["BUS_CURRENT"] or 0) & 0xFFFF
        el["BusCurrent"] = float32(current)
    for bit, attr in (
        (1, "IsAccessory"),
        (2, "HasIndividualAddress"),
        (8, "HasApplicationProgram"),
        (0x20, "IsPowerSupply"),
        (0x40, "IsChoke"),
        (0x80, "IsCoupler"),
        (0x100, "HasApplicationProgram2"),
        (0x400, "IsPowerLineRepeater"),
        (0x800, "IsPowerLineSignalFilter"),
        (0x1000, "IsCable"),
    ):
        on = bool(flags & bit)
        if on or attr in ("HasIndividualAddress", "HasApplicationProgram"):
            el[attr] = boolean(on)
    original = _original_manufacturer(ctx, hw, serial, doc.manufacturer)
    if original is not None:
        el["OriginalManufacturer"] = original
    handling = hw["PRODUCT_HANDLING"]
    if handling is not None and int(handling) == 2 and hw["PRODUCT_DLL"] is not None:
        el["NoDownloadWithoutPlugin"] = "true"
    el["Id"] = hardware_id(doc.manufacturer, serial, version, original)
    doc.hardware.append(el)
    doc.by_id.setdefault(el["Id"], el)
    ctx.add_refid(int(hw["PRODUCT_ID"] or 0), el["Id"])
    if el.get("NoDownloadWithoutPlugin") == "true":
        ctx.warnings.append(
            f"NoDownloadWithoutPlugin: The hardware {el['Id']} ('{hw['PRODUCT_NAME']}') has a "
            "plugin of the old tool and cannot be downloaded without a plugin"
        )
    return el


def _width(value: Value) -> float | None:
    text = "" if value is None else str(value)
    if not text:
        return None
    return xml_double(text)


_XML_WHITESPACE = " \t\n\r"
_XML_DOUBLE = re.compile(
    r"[ \t\n\r\v\f]*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?[ \t\n\r\v\f]*"
)


def xml_double(text: str) -> float | None:
    """``text`` as ``XmlConvert.ToDouble`` reads it (invariant culture, no thousands
    separators), None where that fails."""
    s = text.strip(_XML_WHITESPACE)
    if s in _XML_SPECIAL:
        return _XML_SPECIAL[s]
    if not _XML_DOUBLE.fullmatch(s):
        return None
    value = float(s)
    # out of range is an overflow error there
    return None if math.isinf(value) else value


def _single(value: float) -> float:
    """``value`` rounded to single precision (infinite out of its range)."""
    import struct

    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


_XML_SPECIAL = {
    "INF": math.inf,
    "-INF": -math.inf,
    "Infinity": math.inf,
    "-Infinity": -math.inf,
    "NaN": math.nan,
}


def _children(el: El, tag: str) -> El:
    found = el.find(tag)
    if found is None:
        found = El(tag)
        el.children.append(found)
    return found


_PRODUCT_ORDER = ("Baggages", "Attributes", "RegistrationInfo")
_HW_ORDER = ("Products", "Hardware2Programs")


def _sort_children(el: El, order: tuple[str, ...]) -> None:
    el.children.sort(key=lambda c: order.index(c.tag) if c.tag in order else len(order))


def _append_product(ctx: Context, doc: HardwareDoc, entry: Row) -> El | None:
    lang = ctx.default_language
    product = El("Product")
    product["Text"] = from_db(lang, entry.str("ENTRY_NAME"))
    order = entry.str("ORDER_NUMBER") or ""
    if not order:
        order = f"GeneratedOrderNumber-{entry['CATALOG_ENTRY_ID']}"
    product["OrderNumber"] = order
    din = entry["DIN_FLAG"]
    product["IsRailMounted"] = boolean(din is not None and str(din) == "1")
    mm = _width(entry["ENTRY_WIDTH_IN_MILLIMETERS"])
    if mm is not None:
        product["WidthInMillimeter"] = float32(mm)
    elif (
        entry["ENTRY_WIDTH_IN_MILLIMETERS"] is None
        or str(entry["ENTRY_WIDTH_IN_MILLIMETERS"]) == ""
    ):
        modules = _width(entry["ENTRY_WIDTH_IN_MODULES"])
        if modules is not None:
            product["WidthInMillimeter"] = float32(17.5 * _single(modules))
    hw_ref = ctx.refid(entry["PRODUCT_ID"])
    hardware = doc.by_id.get(hw_ref) if hw_ref else None
    if hardware is None:
        return None
    product["Id"] = f"{hardware['Id']}_P-{fragment(order)}"
    _children(hardware, "Products").children.append(product)
    _sort_children(hardware, _HW_ORDER)
    # (the products of M-0069 stay registered: the converter tests a status property of the
    # product it never sets; only their Hardware2Programs become unregistered)
    info = registration_info(entry, "ENTRY_STATUS_CODE", for_h2p=False)
    product.children.append(info)
    product["DefaultLanguage"] = lang
    ctx.add_refid(int(entry["CATALOG_ENTRY_ID"] or 0), product["Id"])
    attrs = El("Attributes")
    for column, name in (
        ("CATALOG_NAME", "CatalogName"),
        ("SERIES", "Series"),
        ("ENTRY_COLOUR", "Colour"),
    ):
        if entry[column] is not None:
            attrs.sub(
                "Attribute",
                {
                    "Id": f"{product['Id']}_AT-{name}",
                    "Name": name,
                    "Value": from_db(lang, entry.str(column)),
                },
            )
    if attrs.children:
        product.children.append(attrs)
    baggages = El("Baggages")
    for row in ctx.select(
        "catalogentrybaggage", "CATALOGENTRYID", entry["CATALOG_ENTRY_ID"]
    ):
        # an unknown baggage is referred to without id (once, like every baggage)
        ref = ctx.refid(row["BAGGAGEID"]) or ""
        if not any(b.get("RefId") == ref for b in baggages.children):
            baggages.sub("Baggage", {"RefId": ref})
    if baggages.children:
        product.children.append(baggages)
    _sort_children(product, _PRODUCT_ORDER)
    _append_hardware2programs(ctx, doc, product, hardware, entry)

    def attribute(name: str) -> El:
        container = product.find("Attributes")
        if container is None:
            container = El("Attributes")
            product.children.append(container)
            _sort_children(product, _PRODUCT_ORDER)
        for a in container.children:
            if a.get("Name") == name:
                return a
        return container.sub(
            "Attribute", {"Id": f"{product['Id']}_AT-{name}", "Name": name, "Value": ""}
        )

    entry_id = int(entry["CATALOG_ENTRY_ID"] or 0)
    translated = translate_entity(
        ctx,
        doc.translations,
        entry_id,
        "Product",
        product,
        product["Id"],
        product["Id"],
        product_attribute=attribute,
    )
    # descriptions are only translated for a product with texts
    if translated is not None:
        _product_descriptions(ctx, doc, product, entry_id)
    return product


def _product_descriptions(
    ctx: Context, doc: HardwareDoc, product: El, entry_id: int
) -> None:
    from .language import translate_descriptions

    rows = ctx.select("product_description", "CATALOG_ENTRY_ID", entry_id)
    if rows:
        translate_descriptions(
            ctx,
            doc.translations,
            product,
            product["Id"],
            rows,
            "PRODUCT_DESCRIPTION_TEXT",
        )


def _append_hardware2programs(
    ctx: Context, doc: HardwareDoc, product: El, hardware: El, entry: Row
) -> None:
    for p2p in ctx.select("product_to_program", "PRODUCT_ID", entry["PRODUCT_ID"]):
        dbid = int(p2p["PROD2PROG_ID"] or 0)
        if ctx.refid(dbid) is not None:
            continue
        refs: list[str] = []
        if p2p["PROGRAM_ID"] is not None:
            app = ctx.refid(p2p["PROGRAM_ID"])
            if app is not None:
                refs.append(app)
        if p2p["PEI_PROGRAM_ID"] is not None:
            pei = ctx.refid(p2p["PEI_PROGRAM_ID"])
            if pei is not None:
                if pei[:6] != doc.manufacturer:
                    pei = pei.replace(pei[:6], doc.manufacturer)
                refs.append(pei)
        h2p = El("Hardware2Program")
        for ref in refs:
            h2p.sub("ApplicationProgramRef", {"RefId": ref})
        ident = hardware2program_id(hardware["Id"], refs)
        h2p["Id"] = ident
        container = _children(hardware, "Hardware2Programs")
        _sort_children(hardware, _HW_ORDER)
        container.children.append(h2p)
        ctx.add_refid(dbid, ident)
        info = registration_info(p2p, "PROD2PROG_STATUS_CODE", for_h2p=True)
        if doc.manufacturer == "M-0069":
            info["RegistrationStatus"] = "Unregistered"
        h2p.children.append(info)
        dropped = False
        for other in list(container.children):
            if other is h2p or other.get("Id") != ident:
                continue
            if compare_registration(other.find("RegistrationInfo"), info) >= 0:
                dropped = True
            else:
                container.children.remove(other)
            break
        if dropped:
            container.children.remove(h2p)
            continue
        media: list[str] = []
        for mt in ctx.select(
            "product_to_program_to_mt", "PROD2PROG_ID", p2p["PROD2PROG_ID"]
        ):
            ref = f"MT-{int(mt['MEDIUM_TYPE_NUMBER'] or 0)}"
            if ref not in ctx.master.medium_types:
                from .context import ConversionError

                raise ConversionError(f"medium type {ref}")
            media.append(ref)
        if media:
            h2p["MediumTypes"] = " ".join(media)
        if any(ref in ctx.ip_programs for ref in refs):
            hardware["IsIPEnabled"] = "true"


def convert_hardware(ctx: Context) -> list[HardwareDoc]:
    docs: list[HardwareDoc] = []
    doc: HardwareDoc | None = None
    current = -1
    from .context import sort_rows

    for hw in sort_rows(ctx.rows("hw_product"), "MANUFACTURER_ID"):
        ref = ctx.refid(hw["PRODUCT_ID"])
        if ref is not None and doc is not None and ref in doc.by_id:
            continue
        number = int(hw["MANUFACTURER_ID"] or 0)
        if not ctx.known_manufacturer(ctx.manufacturer_ref(number)):
            ctx.errors.append(f"UnknownManufacturerId: {number}")
            continue
        if number != current or doc is None:
            doc = HardwareDoc(ctx.manufacturer_ref(number))
            docs.append(doc)
            current = number
        ctx.default_language = ctx.language_of(doc.manufacturer)
        _append_hardware(ctx, doc, hw)
        for entry in ctx.select("catalog_entry", "PRODUCT_ID", hw["PRODUCT_ID"]):
            _append_product(ctx, doc, entry)
    return docs

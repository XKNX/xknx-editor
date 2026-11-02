"""Hardware, products, Hardware2Program and the catalog of a database with several devices."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import pytest

from xknxeditor.prod.vd.context import ConversionError

from .test_coverage_helpers import (
    Tables,
    base,
    children,
    every,
    find,
    findall,
    program,
    run,
    text,
    translations,
    xml,
)


def _fe(
    ident: int, number: str | None, parent: int | None, **kw: Any
) -> dict[str, Any]:
    return {
        "FUNCTIONAL_ENTITY_ID": ident,
        "MANUFACTURER_ID": kw.get("manufacturer", 131),
        "FUNCTIONAL_ENTITY_NAME": kw.get("name", f"Section {ident}"),
        "FUNCTIONAL_ENTITY_NUMB": number,
        "FUNCTIONAL_ENTITY_DESCRIPTION": kw.get("description"),
        "FUN_FUNCTIONAL_ENTITY_ID": parent,
    }


def _entry(ident: int, product: int, order: str | None, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "CATALOG_ENTRY_ID": ident,
        "PRODUCT_ID": product,
        "MANUFACTURER_ID": 131,
        "ORDER_NUMBER": order,
        "ENTRY_NAME": f"Entry {ident}",
        "ENTRY_WIDTH_IN_MODULES": None,
        "ENTRY_WIDTH_IN_MILLIMETERS": None,
        "DIN_FLAG": 0,
        "ENTRY_STATUS_CODE": 10,
        "REGISTRATION_DATE": None,
    }
    row.update({k.upper(): v for k, v in kw.items()})
    return row


def _vd(
    ident: int, entry: int, prog: int | None, fe: int, number: int | None, **kw: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "VIRTUAL_DEVICE_ID": ident,
        "CATALOG_ENTRY_ID": entry,
        "PROGRAM_ID": prog,
        "VIRTUAL_DEVICE_NAME": f"Device {ident}",
        "VIRTUAL_DEVICE_DESCRIPTION": None,
        "FUNCTIONAL_ENTITY_ID": fe,
        "PEI_PROGRAM_ID": None,
        "VIRTUAL_DEVICE_NUMBER": number,
    }
    row.update({k.upper(): v for k, v in kw.items()})
    return row


def _p2p(ident: int, product: int, prog: int | None, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "PROD2PROG_ID": ident,
        "PRODUCT_ID": product,
        "PROGRAM_ID": prog,
        "PEI_PROGRAM_ID": None,
        "PROD2PROG_STATUS_CODE": 10,
        "REGISTRATION_NUMBER": None,
        "REGISTRATION_YEAR": None,
        "REGISTRATION_DATE": None,
    }
    row.update({k.upper(): v for k, v in kw.items()})
    return row


def devices_tables() -> Tables:
    t = base()
    t["mask"].append({"MASK_ID": 55, "MASK_VERSION": 0x5705})
    t["application_program"] += [
        program(program_id=46, device_type=0x2006, program_type=1),
        program(program_id=48, device_type=0x2008, mask_id=55),
        program(program_id=49, device_type=0x2009, manufacturer_id=105),
    ]
    t["hw_product"] += [
        {
            "PRODUCT_ID": 24,
            "MANUFACTURER_ID": 131,
            "PRODUCT_NAME": None,
            "PRODUCT_VERSION_NUMBER": 3,
            "COMPONENT_ATTRIBUTES": 0x1DE9,
            "BUS_CURRENT": -2,
            "PRODUCT_SERIAL_NUMBER": None,
            "ORIGINAL_MANUFACTURER_ID": 8,
            "PRODUCT_HANDLING": 2,
            "PRODUCT_DLL": "plugin.dll",
        },
        {
            "PRODUCT_ID": 25,
            "MANUFACTURER_ID": 99,
            "PRODUCT_NAME": "Unknown",
            "PRODUCT_SERIAL_NUMBER": "U",
        },
        {
            "PRODUCT_ID": 26,
            "MANUFACTURER_ID": 131,
            "PRODUCT_NAME": "IP HW",
            "PRODUCT_VERSION_NUMBER": None,
            "COMPONENT_ATTRIBUTES": None,
            "PRODUCT_SERIAL_NUMBER": "IP 1",
            "ORIGINAL_MANUFACTURER_ID": 0xDC,
            "PRODUCT_HANDLING": 2,
            "PRODUCT_DLL": None,
        },
        {
            "PRODUCT_ID": 27,
            "MANUFACTURER_ID": 105,
            "PRODUCT_NAME": "RTS HW",
            "PRODUCT_VERSION_NUMBER": 1,
            "PRODUCT_SERIAL_NUMBER": "R1",
        },
    ]
    t["catalog_entry"] += [
        _entry(
            31,
            24,
            None,
            ENTRY_WIDTH_IN_MILLIMETERS="17,5",
            ENTRY_COLOUR="white",
            CATALOG_NAME="Catalog",
            SERIES=None,
            ENTRY_STATUS_CODE=20,
            REGISTRATION_DATE="04.05.2010",
        ),
        _entry(
            32,
            24,
            "Second",
            ENTRY_WIDTH_IN_MILLIMETERS="wide",
            ENTRY_WIDTH_IN_MODULES="1",
            ENTRY_STATUS_CODE=None,
            REGISTRATION_DATE="1980-01-01",
            DIN_FLAG=1,
        ),
        _entry(
            33, 26, "IP-1", ENTRY_WIDTH_IN_MODULES="0,5", REGISTRATION_DATE="31.02.2010"
        ),
        _entry(34, 27, "R-1", MANUFACTURER_ID=105, REGISTRATION_DATE="2010-13-01"),
    ]
    t["product_to_program"] += [
        _p2p(
            66,
            24,
            40,
            PEI_PROGRAM_ID=46,
            ORIGINAL_REGISTRATION_NUMBER=12,
            ORIGINAL_REGISTRATION_YEAR=105,
            REGISTRATION_NUMBER=5,
            REGISTRATION_YEAR=111,
            REGISTRATION_DATE="2011-02-03",
        ),
        # the same Hardware2Program again: worse registered (dropped), better (replaces)
        _p2p(
            67, 24, 40, PEI_PROGRAM_ID=46, REGISTRATION_NUMBER=4, REGISTRATION_YEAR=111
        ),
        _p2p(
            68,
            24,
            40,
            PEI_PROGRAM_ID=46,
            PROD2PROG_STATUS_CODE=20,
            REGISTRATION_NUMBER=None,
            ORIGINAL_REGISTRATION_YEAR=99,
            ORIGINAL_REGISTRATION_NUMBER=None,
        ),
        _p2p(69, 26, 48, PROD2PROG_STATUS_CODE=None),
        _p2p(70, 27, 49, REGISTRATION_NUMBER=3, REGISTRATION_YEAR=110),
        _p2p(71, 24, None),
    ]
    t["product_to_program_to_mt"] += [
        {"PROD2PROG2MT_ID": 62, "PROD2PROG_ID": 66, "MEDIUM_TYPE_NUMBER": 0},
        {"PROD2PROG2MT_ID": 63, "PROD2PROG_ID": 66, "MEDIUM_TYPE_NUMBER": 1},
        {"PROD2PROG2MT_ID": 64, "PROD2PROG_ID": 69, "MEDIUM_TYPE_NUMBER": 5},
    ]
    t["functional_entity"] += [
        _fe(11, None, 10),
        _fe(12, "Deep", 11, description="deep section"),
        _fe(13, "Empty", None),
        _fe(14, "Empty child", 13),
        _fe(16, "RTS", None, manufacturer=105),
    ]
    t["virtual_device"] += [
        _vd(71, 31, 40, 12, 2, VIRTUAL_DEVICE_DESCRIPTION="second", PEI_PROGRAM_ID=46),
        _vd(72, 31, 40, 12, None),
        _vd(74, 31, 40, 12, 2, PEI_PROGRAM_ID=46),
        _vd(75, 32, 40, 11, 4, PEI_PROGRAM_ID=46),
        _vd(76, 33, 48, 10, 5),
        _vd(77, 31, 41, 10, 6),
        _vd(78, 34, 49, 16, 1),
    ]
    t["product_description"] = [
        {
            "PRODUCT_DESCRIPTION_ID": 1,
            "CATALOG_ENTRY_ID": 31,
            "PRODUCT_DESCRIPTION_TEXT": "Line 2",
            "DISPLAY_ORDER": 2,
            "LANGUAGE_ID": 1033,
        },
        {
            "PRODUCT_DESCRIPTION_ID": 2,
            "CATALOG_ENTRY_ID": 31,
            "PRODUCT_DESCRIPTION_TEXT": "Line 1",
            "DISPLAY_ORDER": 1,
            "LANGUAGE_ID": 1033,
        },
        {
            "PRODUCT_DESCRIPTION_ID": 3,
            "CATALOG_ENTRY_ID": 31,
            "PRODUCT_DESCRIPTION_TEXT": "Zeile",
            "DISPLAY_ORDER": 1,
            "LANGUAGE_ID": 1031,
        },
    ]
    t["text_attribute"] = [
        text(1, 1031, 30, 10, "Licht"),
        text(2, 1031, 31, 12, "tief"),
        text(3, 1033, 30, 12, "Deep EN"),
        text(4, 1031, 40, 71, "Gerät 71"),
        text(5, 1031, 41, 71, "zweites"),
        text(6, 1031, 1, 31, "Eintrag"),
        text(7, 1031, 2, 31, "weiß"),
        text(8, 1033, 2, 31, "white EN"),
        text(9, 1031, 3, 31, "Serie"),
        text(10, 1031, 4, 32, "Katalog"),
        text(11, 1049, 1, 31, "Ïðèáîð"),
        text(12, 1031, 20, 31, "wrong kind"),
    ]
    return t


H24 = "M-0083_H-GeneratedNumber.2D24-3-O0008"
P31 = f"{H24}_P-GeneratedOrderNumber.2D31"
HP24 = f"{H24}_HP-1234-01-0000-2006-01-0000"


def _hardware(
    files: dict[str, bytes], name: str = "M-0083/Hardware.xml"
) -> dict[str, ET.Element]:
    root = xml(files, name)
    return {h.get("Id") or "": h for h in findall(root, ".//{}Hardware/{}Hardware")}


def test_hardware() -> None:
    files, warnings, errors = run(devices_tables())
    hardware = _hardware(files)
    assert list(hardware) == ["M-0083_H-D1-2", H24, "M-0083_H-IP.201-0"]
    # generated name and serial number, every flag, original manufacturer, old plugin
    assert dict(hardware[H24].attrib) == {
        "Id": H24,
        "Name": "GeneratedName-24",
        "SerialNumber": "GeneratedNumber-24",
        "VersionNumber": "3",
        "BusCurrent": "65534",
        "IsAccessory": "true",
        "HasIndividualAddress": "false",
        "HasApplicationProgram": "true",
        "HasApplicationProgram2": "true",
        "IsPowerSupply": "true",
        "IsChoke": "true",
        "IsCoupler": "true",
        "IsPowerLineRepeater": "true",
        "IsPowerLineSignalFilter": "true",
        "IsCable": "true",
        "OriginalManufacturer": "M-0008",
        "NoDownloadWithoutPlugin": "true",
    }
    # original manufacturer DC is dropped; the IP program makes the hardware IP enabled
    assert dict(hardware["M-0083_H-IP.201-0"].attrib) == {
        "Id": "M-0083_H-IP.201-0",
        "Name": "IP HW",
        "SerialNumber": "IP 1",
        "VersionNumber": "0",
        "HasIndividualAddress": "false",
        "HasApplicationProgram": "false",
        "IsIPEnabled": "true",
    }
    assert [w.split(":")[0] for w in warnings] == ["NoDownloadWithoutPlugin"]
    assert errors == ["UnknownManufacturerId: 99", "CatalogItemNotConvertable: 72"]


def test_products() -> None:
    files, _warnings, _errors = run(devices_tables())
    products = {
        p.get("Id"): p for p in every(xml(files, "M-0083/Hardware.xml"), "Product")
    }
    first = products[P31]
    assert {k: v for k, v in first.attrib.items() if k != "WidthInMillimeter"} == {
        "Id": P31,
        "Text": "Entry 31",
        "OrderNumber": "GeneratedOrderNumber-31",
        "IsRailMounted": "false",
        "VisibleDescription": "Line 1\r\nLine 2",
        "DefaultLanguage": "en-US",
    }
    assert [c[0] for c in children(first)] == ["Attributes", "RegistrationInfo"]
    # the attribute texts come from the translations (Series only from them)
    assert children(find(first, "{}Attributes")) == [
        (
            "Attribute",
            {"Id": f"{P31}_AT-CatalogName", "Name": "CatalogName", "Value": "Catalog"},
        ),
        (
            "Attribute",
            {"Id": f"{P31}_AT-Colour", "Name": "Colour", "Value": "white EN"},
        ),
        ("Attribute", {"Id": f"{P31}_AT-Series", "Name": "Series", "Value": ""}),
    ]
    assert find(first, "{}RegistrationInfo").get("RegistrationStatus") == "Certified"
    second = products[f"{H24}_P-Second"]
    # an invalid width in millimeters is not replaced by the modules; old dates are dropped
    assert dict(second.attrib) == {
        "Id": f"{H24}_P-Second",
        "Text": "Entry 32",
        "OrderNumber": "Second",
        "IsRailMounted": "true",
        "DefaultLanguage": "en-US",
    }
    assert children(find(second, "{}RegistrationInfo")) == []
    assert dict(find(second, "{}RegistrationInfo").attrib) == {
        "RegistrationStatus": "Unregistered"
    }
    root = xml(files, "M-0083/Hardware.xml")
    assert translations(root, "de-DE") == {
        P31: {"Text": "Eintrag", "VisibleDescription": "Zeile"},
        f"{P31}_AT-Colour": {"Value": "weiß"},
        f"{P31}_AT-Series": {"Value": "Serie"},
        f"{H24}_P-Second_AT-CatalogName": {"Value": "Katalog"},
    }
    assert translations(root, "en-US") == {
        f"{P31}_AT-Colour": {"Value": "white EN"},
        P31: {"VisibleDescription": "Line 1\r\nLine 2"},
    }
    # a text in the code page of its language
    assert translations(root, "ru-RU") == {P31: {"Text": "Прибор"}}


@pytest.mark.xfail(
    reason="the converter reads widths with XmlConvert (invariant): '17,5' mm and '0,5' "
    "modules give no WidthInMillimeter",
    strict=True,
)
def test_width_with_decimal_comma() -> None:
    files, _warnings, _errors = run(devices_tables())
    products = {
        p.get("Id"): p for p in every(xml(files, "M-0083/Hardware.xml"), "Product")
    }
    assert "WidthInMillimeter" not in products[P31].attrib
    assert "WidthInMillimeter" not in products["M-0083_H-IP.201-0_P-IP.2D1"].attrib


@pytest.mark.xfail(
    reason="the converter parses dates with the invariant culture: 04.05.2010 is April 5",
    strict=True,
)
def test_dotted_date_is_month_first() -> None:
    files, _warnings, _errors = run(devices_tables())
    products = {
        p.get("Id"): p for p in every(xml(files, "M-0083/Hardware.xml"), "Product")
    }
    info = find(products[P31], "{}RegistrationInfo")
    assert info.get("RegistrationDate") == "2010-04-05"


def test_hardware2programs() -> None:
    files, _warnings, _errors = run(devices_tables())
    h2ps = findall(_hardware(files)[H24], "{}Hardware2Programs/{}Hardware2Program")
    assert [(dict(h.attrib), children(h)) for h in h2ps] == [
        (
            {"Id": HP24, "MediumTypes": "MT-0 MT-1"},
            [
                ("ApplicationProgramRef", {"RefId": "M-0083_A-1234-01-0000"}),
                ("ApplicationProgramRef", {"RefId": "M-0083_A-2006-01-0000"}),
                (
                    "RegistrationInfo",
                    {
                        "RegistrationStatus": "Registered",
                        "RegistrationNumber": "2011/5",
                        "OriginalRegistrationNumber": "2005/12",
                        "RegistrationDate": "2011-02-03",
                    },
                ),
            ],
        ),
        # a program assignment without program
        (
            {"Id": f"{H24}_HP"},
            [("RegistrationInfo", {"RegistrationStatus": "Registered"})],
        ),
    ]
    ip = findall(_hardware(files)["M-0083_H-IP.201-0"], ".//{}Hardware2Program")
    assert children(ip[0])[-1] == (
        "RegistrationInfo",
        {"RegistrationStatus": "Unregistered"},
    )
    rts = _hardware(files, "M-0069/Hardware.xml")["M-0069_H-R1-1"]
    # the Hardware2Program of this manufacturer is unregistered
    info = find(rts, ".//{}Hardware2Program/{}RegistrationInfo")
    assert dict(info.attrib) == {
        "RegistrationStatus": "Unregistered",
        "RegistrationNumber": "2010/3",
    }


@pytest.mark.xfail(
    reason="the converter checks the product's own RegistrationStatus (never set): products "
    "of M-0069 stay registered",
    strict=True,
)
def test_products_of_m0069_stay_registered() -> None:
    files, _warnings, _errors = run(devices_tables())
    rts = _hardware(files, "M-0069/Hardware.xml")["M-0069_H-R1-1"]
    info = find(rts, ".//{}Product/{}RegistrationInfo")
    assert info.get("RegistrationStatus") == "Registered"


def _sections(el: ET.Element) -> list[Any]:
    out: list[Any] = []
    for c in el:
        if c.tag.endswith("CatalogSection"):
            out.append((c.get("Id"), c.get("Name"), _sections(c)))
        else:
            out.append(c.get("Id"))
    return out


def test_catalog() -> None:
    files, _warnings, _errors = run(devices_tables())
    root = xml(files, "M-0083/Catalog.xml")
    item71 = f"{HP24}_CI-GeneratedOrderNumber.2D31-2"
    assert _sections(find(root, ".//{}Catalog")) == [
        (
            "M-0083_CS-L",
            "Lighting",
            [
                (
                    "M-0083_CS-L-Generated.2D11",
                    "Section 11",
                    [
                        ("M-0083_CS-L-Generated.2D11-Deep", "Deep EN", [item71]),
                        f"{HP24}_CI-Second-4",
                    ],
                ),
                "M-0083_H-IP.201-0_HP-2008-01-0000_CI-IP.2D1-5",
                "M-0083_H-D1-2_HP-1234-01-0000_CI-D.2D1-1",
            ],
        ),
        # sections without devices
        (
            "M-0083_CS-Empty",
            "Section 13",
            [("M-0083_CS-Empty-Empty.20child", "Section 14", [])],
        ),
    ]
    items = {i.get("Id"): dict(i.attrib) for i in every(root, "CatalogItem")}
    assert items[item71] == {
        "Id": item71,
        "Name": "Device 71",
        "Number": "2",
        "VisibleDescription": "second",
        "ProductRefId": P31,
        "Hardware2ProgramRefId": HP24,
        "DefaultLanguage": "en-US",
    }
    deep = find(root, ".//{}CatalogSection[@Id='M-0083_CS-L-Generated.2D11-Deep']")
    assert deep.get("VisibleDescription") == "deep section"
    assert translations(root, "de-DE") == {
        "M-0083_CS-L": {"Name": "Licht"},
        "M-0083_CS-L-Generated.2D11-Deep": {"VisibleDescription": "tief"},
        item71: {"Name": "Gerät 71", "VisibleDescription": "zweites"},
    }
    assert translations(root, "en-US") == {
        "M-0083_CS-L-Generated.2D11-Deep": {"Name": "Deep EN"}
    }
    rts = xml(files, "M-0069/Catalog.xml")
    assert _sections(find(rts, ".//{}Catalog")) == [
        ("M-0069_CS-RTS", "Section 16", ["M-0069_H-R1-1_HP-2009-01-0000_CI-R.2D1-1"])
    ]


def _original(value: int, serial: str = "S", manufacturer: int = 131) -> Tables:
    t = base()
    t["hw_product"][0].update(
        {
            "ORIGINAL_MANUFACTURER_ID": value,
            "PRODUCT_SERIAL_NUMBER": serial,
            "MANUFACTURER_ID": manufacturer,
        }
    )
    return t


def _hardware_ids(tables: Tables) -> tuple[list[str], list[str]]:
    files, _warnings, errors = run(tables)
    names = [n for n in files if n.endswith("Hardware.xml")]
    ids = [
        h.get("Id") or ""
        for n in names
        for h in findall(xml(files, n), ".//{}Hardware/{}Hardware")
    ]
    return ids, errors


def test_original_manufacturer_rules() -> None:
    assert _hardware_ids(_original(0x41))[0] == ["M-0083_H-S-2-O0041"]
    assert any(
        "OriginalManufacturer of Hardware S" in e
        for e in _hardware_ids(_original(0x41))[1]
    )
    assert _hardware_ids(_original(0xFA))[0] == ["M-0083_H-S-2"]
    assert _hardware_ids(_original(0x3C, manufacturer=126))[0] == ["M-007E_H-S-2"]
    assert _hardware_ids(_original(0x44, "9 // TG052"))[0] == [
        "M-0083_H-9.20.2F.2F.20TG052-2-O00C9"
    ]
    with pytest.raises(ConversionError):
        run(_original(0x77))


def test_unknown_medium_type_stops_the_conversion() -> None:
    t = base()
    t["product_to_program_to_mt"][0]["MEDIUM_TYPE_NUMBER"] = 7
    with pytest.raises(ConversionError):
        run(t)


def test_too_big_hardware_version_is_reported() -> None:
    # the dump column holds 16-bit numbers; a bigger value gives version 0
    t = base()
    t["hw_product"][0]["PRODUCT_VERSION_NUMBER"] = 40000
    ids, _errors = _hardware_ids(t)
    _files, warnings, _errors = run(t)
    assert ids == ["M-0083_H-D1-0"]
    assert warnings == ["HardwareVersionNumberTooBig: 40000"]

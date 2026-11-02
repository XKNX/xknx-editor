"""Converting a product database (schema 6.3) to the unsigned XML files of a product package."""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from xknxeditor.prod.vd import baggage as vd_baggage
from xknxeditor.prod.vd.convert import convert, convert_with_report
from xknxeditor.prod.vd.database import Database

_SAMPLES = Path(r"C:\Austausch\AB-SmartHouse\XKNX\zReferences")
_NS = "{http://knx.org/xml/project/14}"

MASTER = b"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/23">
  <MasterData>
    <DatapointTypes>
      <DatapointType Id="DPT-1" Number="1" SizeInBit="1">
        <DatapointSubtypes>
          <DatapointSubtype Id="DPST-1-1" Number="1" />
        </DatapointSubtypes>
      </DatapointType>
      <DatapointType Id="DPT-5" Number="5" SizeInBit="8" />
    </DatapointTypes>
    <MediumTypes><MediumType Id="MT-0" Number="0" Name="TP" /></MediumTypes>
    <MaskVersions>
      <MaskVersion Id="MV-0012" MaskVersion="18" ManagementModel="Bcu1" MediumTypeRefId="MT-0" />
      <MaskVersion Id="MV-0701" MaskVersion="1793" ManagementModel="BimM112" MediumTypeRefId="MT-0">
        <HawkConfigurationData><Features><Feature Name="FirstAppObjectIdx" Value="5" /></Features></HawkConfigurationData>
      </MaskVersion>
    </MaskVersions>
    <Manufacturers>
      <Manufacturer Id="M-0083" KnxManufacturerId="131" Name="Test" DefaultLanguage="en-US" />
    </Manufacturers>
  </MasterData>
</KNX>"""

Rows = list[dict[str, Any]]


def _db(tables: dict[str, Rows]) -> Database:
    db = Database((6 << 16) | 3)
    for name, rows in tables.items():
        columns: list[str] = []
        for r in rows:
            for c in r:
                if c.upper() not in columns:
                    columns.append(c.upper())
        table = db.add_table(name, columns)
        for r in rows:
            table.append({k.upper(): v for k, v in r.items()})
    return db


def _base() -> dict[str, Rows]:
    """A database with one device: catalog entry, hardware, program, virtual device."""
    return {
        "manufacturer": [{"MANUFACTURER_ID": 131, "MANUFACTURER_NAME": "Test"}],
        "functional_entity": [
            {
                "FUNCTIONAL_ENTITY_ID": 10,
                "MANUFACTURER_ID": 131,
                "FUNCTIONAL_ENTITY_NAME": "Lighting",
                "FUNCTIONAL_ENTITY_NUMB": "L 1",
                "FUNCTIONAL_ENTITY_DESCRIPTION": None,
                "FUN_FUNCTIONAL_ENTITY_ID": None,
            },
            {
                "FUNCTIONAL_ENTITY_ID": 11,
                "MANUFACTURER_ID": 131,
                "FUNCTIONAL_ENTITY_NAME": "Dimmers",
                "FUNCTIONAL_ENTITY_NUMB": "D",
                "FUNCTIONAL_ENTITY_DESCRIPTION": "dim",
                "FUN_FUNCTIONAL_ENTITY_ID": 10,
            },
        ],
        "hw_product": [
            {
                "PRODUCT_ID": 20,
                "MANUFACTURER_ID": 131,
                "PRODUCT_NAME": "Dimmer HW",
                "PRODUCT_VERSION_NUMBER": 2,
                "COMPONENT_ATTRIBUTES": 10,
                "BUS_CURRENT": 10,
                "PRODUCT_SERIAL_NUMBER": "D/1",
                "ORIGINAL_MANUFACTURER_ID": None,
                "PRODUCT_HANDLING": None,
            }
        ],
        "catalog_entry": [
            {
                "CATALOG_ENTRY_ID": 30,
                "PRODUCT_ID": 20,
                "MANUFACTURER_ID": 131,
                "ORDER_NUMBER": "D-1",
                "ENTRY_NAME": "Dimmer",
                "ENTRY_WIDTH_IN_MODULES": "2",
                "ENTRY_WIDTH_IN_MILLIMETERS": None,
                "DIN_FLAG": 1,
                "SERIES": "S1",
                "ENTRY_STATUS_CODE": 10,
                "REGISTRATION_DATE": "2010-05-04",
            }
        ],
        "application_program": [
            {
                "PROGRAM_ID": 40,
                "MASK_ID": 50,
                "PROGRAM_NAME": "Dimmer app",
                "PROGRAM_VERSION": "1.2",
                "LINKABLE": 0,
                "DEVICE_TYPE": 0x1234,
                "PEI_TYPE": 0,
                "ADDRESS_TAB_SIZE": 9,
                "ASSOCTAB_ADDRESS": 0x4100,
                "ASSOCTAB_SIZE": 5,
                "COMMSTAB_ADDRESS": 0x4200,
                "MANUFACTURER_ID": 131,
                "DYNAMIC_MANAGEMENT": 1,
                "PROGRAM_TYPE": 0,
                "PROGRAM_STYLE": 1,
            }
        ],
        "mask": [{"MASK_ID": 50, "MASK_VERSION": 0x701}],
        "product_to_program": [
            {
                "PROD2PROG_ID": 60,
                "PRODUCT_ID": 20,
                "PROGRAM_ID": 40,
                "PEI_PROGRAM_ID": None,
                "PROD2PROG_STATUS_CODE": 10,
                "REGISTRATION_NUMBER": 77,
                "REGISTRATION_YEAR": 110,
                "REGISTRATION_DATE": "2010-05-04",
            }
        ],
        "product_to_program_to_mt": [
            {"PROD2PROG2MT_ID": 61, "PROD2PROG_ID": 60, "MEDIUM_TYPE_NUMBER": 0}
        ],
        "virtual_device": [
            {
                "VIRTUAL_DEVICE_ID": 70,
                "CATALOG_ENTRY_ID": 30,
                "PROGRAM_ID": 40,
                "VIRTUAL_DEVICE_NAME": "Dimmer 1-fold",
                "VIRTUAL_DEVICE_DESCRIPTION": None,
                "FUNCTIONAL_ENTITY_ID": 11,
                "PEI_PROGRAM_ID": None,
                "VIRTUAL_DEVICE_NUMBER": 1,
            }
        ],
        "ete_language": [
            {"LANGUAGE_ID": 1031, "LANGUAGE_NAME": "German", "DATABASE_LANGUAGE": 0},
            {"LANGUAGE_ID": 1033, "LANGUAGE_NAME": "English", "DATABASE_LANGUAGE": 1},
        ],
        "text_attribute": [
            _text(1, 1031, 30, 10, "Beleuchtung"),
            _text(2, 1033, 30, 10, "Lighting EN"),
            _text(3, 1031, 30, 11, "Dimmer"),
            _text(4, 1033, 1, 30, "Dimmer EN"),
            _text(5, 1031, 3, 30, "Serie"),
            _text(6, 1031, 80, 40, "Dimmer Applikation"),
            _text(7, 1033, 41, 70, None),
        ],
    }


def _text(
    ident: int, lcid: int, column: int, entity: int, text: str | None
) -> dict[str, Any]:
    return {
        "TEXT_ATTRIBUTE_ID": ident,
        "LANGUAGE_ID": lcid,
        "COLUMN_ID": column,
        "ENTITY_ID": entity,
        "TEXT_ATTRIBUTE_TEXT": text,
    }


def _xml(files: dict[str, bytes], name: str) -> ET.Element:
    data = files[name]
    assert data.startswith(b'<?xml version="1.0"?>\r\n<KNX xmlns:xsi=')
    return ET.fromstring(data)


def _find(root: ET.Element, path: str) -> ET.Element:
    el = root.find(path.replace("{}", _NS))
    assert el is not None, path
    return el


def _all(root: ET.Element, tag: str) -> list[ET.Element]:
    return list(root.iter(_NS + tag))


APP = "M-0083_A-1234-01-0000"


def test_file_names() -> None:
    files = convert(_db(_base()), MASTER)
    assert sorted(files) == [
        "M-0083/Catalog.xml",
        "M-0083/Hardware.xml",
        f"M-0083/{APP}.xml",
    ]


def test_catalog_sections_items_and_translations() -> None:
    root = _xml(convert(_db(_base()), MASTER), "M-0083/Catalog.xml")
    top = _find(root, ".//{}Catalog/{}CatalogSection")
    assert top.attrib == {
        "Id": "M-0083_CS-L.201",
        "Name": "Lighting EN",
        "Number": "L 1",
        "VisibleDescription": "",
        "DefaultLanguage": "en-US",
    }
    child = _find(top, "{}CatalogSection")
    assert child.get("Id") == "M-0083_CS-L.201-D"
    assert child.get("VisibleDescription") == "dim"
    item = _find(child, "{}CatalogItem")
    h2p = "M-0083_H-D.2F1-2_HP-1234-01-0000"
    assert item.attrib == {
        "Id": f"{h2p}_CI-D.2D1-1",
        "Name": "Dimmer 1-fold",
        "Number": "1",
        "ProductRefId": "M-0083_H-D.2F1-2_P-D.2D1",
        "Hardware2ProgramRefId": h2p,
        "DefaultLanguage": "en-US",
    }
    languages = [lang.get("Identifier") for lang in _all(root, "Language")]
    assert languages == ["de-DE", "en-US"]
    texts = {(t.get("AttributeName"), t.get("Text")) for t in _all(root, "Translation")}
    assert ("Name", "Beleuchtung") in texts
    assert ("Name", "Dimmer") in texts


def test_hardware_products_and_hardware2program() -> None:
    root = _xml(convert(_db(_base()), MASTER), "M-0083/Hardware.xml")
    hw = _find(root, ".//{}Hardware/{}Hardware")
    assert hw.attrib == {
        "Id": "M-0083_H-D.2F1-2",
        "Name": "Dimmer HW",
        "SerialNumber": "D/1",
        "VersionNumber": "2",
        "BusCurrent": "10",
        "HasIndividualAddress": "true",
        "HasApplicationProgram": "true",
    }
    product = _find(hw, "{}Products/{}Product")
    assert product.get("Text") == "Dimmer EN"
    assert product.get("WidthInMillimeter") == "35"
    assert product.get("IsRailMounted") == "true"
    attribute = _find(product, "{}Attributes/{}Attribute")
    assert attribute.attrib == {
        "Id": "M-0083_H-D.2F1-2_P-D.2D1_AT-Series",
        "Name": "Series",
        "Value": "S1",
    }
    info = _find(product, "{}RegistrationInfo")
    assert info.attrib == {
        "RegistrationStatus": "Registered",
        "RegistrationDate": "2010-05-04",
    }
    h2p = _find(hw, "{}Hardware2Programs/{}Hardware2Program")
    assert h2p.get("Id") == "M-0083_H-D.2F1-2_HP-1234-01-0000"
    assert h2p.get("MediumTypes") == "MT-0"
    assert _find(h2p, "{}ApplicationProgramRef").get("RefId") == APP
    assert _find(h2p, "{}RegistrationInfo").attrib == {
        "RegistrationStatus": "Registered",
        "RegistrationNumber": "2010/77",
        "RegistrationDate": "2010-05-04",
    }
    # the series translation goes to the product attribute
    element_ids = [e.get("RefId") for e in _all(root, "TranslationElement")]
    assert "M-0083_H-D.2F1-2_P-D.2D1_AT-Series" in element_ids


def _with_parameters() -> dict[str, Rows]:
    t = _base()
    t["parameter_type"] = [
        _ptype(100, 1, "t_number", 8, minimum=1, maximum=100),
        _ptype(101, 4, "t_enum", 2),
        _ptype(102, 3, "t_text", 80),
        _ptype(103, 0, "t_page", 0),
    ]
    t["parameter_list_of_values"] = [
        {
            "PARAMETER_TYPE_ID": 101,
            "REAL_VALUE": 1,
            "DISPLAYED_VALUE": "On",
            "DISPLAY_ORDER": 1,
            "PARAMETER_VALUE_ID": 110,
        },
        {
            "PARAMETER_TYPE_ID": 101,
            "REAL_VALUE": 0,
            "DISPLAYED_VALUE": "Off",
            "DISPLAY_ORDER": 0,
            "PARAMETER_VALUE_ID": 111,
        },
    ]
    t["parameter"] = [
        _param(200, 0, "Page", 103, space=0, order=1),
        _param(201, 1, "Mode", 101, space=2, address=0x4400, bit=0, default=1, order=2),
        _param(
            202,
            2,
            "Level",
            100,
            space=2,
            address=0x4401,
            bit=0,
            default=None,
            order=3,
            parent=201,
            value="1",
        ),
        _param(
            203,
            3,
            "Name",
            102,
            space=2,
            address=0x4402,
            bit=0,
            order=4,
            text_default="x",
        ),
        _param(
            204,
            4,
            "Level alias",
            100,
            space=2,
            address=0x4401,
            bit=0,
            default=5,
            order=5,
        ),
        _param(
            205,
            5,
            "Level bits",
            101,
            space=2,
            address=0x4401,
            bit=6,
            default=0,
            order=6,
        ),
    ]
    t["text_attribute"] += [
        _text(20, 1031, 10, 201, "Modus"),
        _text(21, 1033, 10, 201, "Mode EN"),
        _text(22, 1031, 11, 110, "Ein"),
        _text(23, 1031, 10, 204, "Pegel"),
        _text(25, 1033, 10, 204, "Alias EN"),
        _text(24, 1031, 10, 202, "Pegel"),
    ]
    return t


def _ptype(ident: int, atomic: int, name: str, size: int, **kw: Any) -> dict[str, Any]:
    return {
        "PARAMETER_TYPE_ID": ident,
        "ATOMIC_TYPE_NUMBER": atomic,
        "PROGRAM_ID": 40,
        "PARAMETER_TYPE_NAME": name,
        "PARAMETER_MINIMUM_VALUE": kw.get("minimum"),
        "PARAMETER_MAXIMUM_VALUE": kw.get("maximum"),
        "PARAMETER_TYPE_DESCRIPTION": None,
        "PARAMETER_TYPE_SIZE": size,
        "PARAMETER_UI_HINT": None,
    }


def _param(ident: int, number: int, name: str, ptype: int, **kw: Any) -> dict[str, Any]:
    return {
        "PROGRAM_ID": 40,
        "PARAMETER_TYPE_ID": ptype,
        "PARAMETER_NUMBER": number,
        "PARAMETER_NAME": name,
        "PARAMETER_LOW_ACCESS": 1,
        "PARAMETER_HIGH_ACCESS": 2,
        "PARENT_PARM_VALUE": kw.get("value"),
        "PARAMETER_DISPLAY_ORDER": kw.get("order"),
        "PARAMETER_ADDRESS": kw.get("address"),
        "PARAMETER_BITOFFSET": kw.get("bit"),
        "PARAMETER_DESCRIPTION": f"{name} text",
        "PARAMETER_ID": ident,
        "PAR_PARAMETER_ID": kw.get("parent"),
        "PARAMETER_DEFAULT_LONG": kw.get("default"),
        "PARAMETER_DEFAULT_STRING": kw.get("text_default"),
        "PATCH_ALWAYS": 0,
        "ADDRESS_SPACE": kw.get("space"),
        "EIB_OBJECT_REF": None,
        "EIB_PROPERTY_ID": None,
    }


def _app(tables: dict[str, Rows]) -> ET.Element:
    return _xml(convert(_db(tables), MASTER), f"M-0083/{APP}.xml")


def test_application_program_attributes() -> None:
    app = _find(_app(_base()), ".//{}ApplicationProgram")
    assert app.attrib == {
        "Id": APP,
        "ApplicationNumber": "4660",
        "ApplicationVersion": "1",
        "ProgramType": "ApplicationProgram",
        "MaskVersion": "MV-0701",
        "Name": "Dimmer app",
        "LoadProcedureStyle": "ProductProcedure",
        "PeiType": "0",
        "HelpFile": "",
        "DefaultLanguage": "en-US",
        "DynamicTableManagement": "true",
        "Linkable": "false",
        "PreEts4Style": "true",
        "ConvertedFromPreEts4Data": "true",
    }
    static = _find(app, "{}Static")
    assert _find(static, "{}AddressTable").attrib == {
        "CodeSegment": f"{APP}_AS-4000",
        "Offset": "0",
        "MaxEntries": "3",
    }
    assert _find(static, "{}AssociationTable").get("MaxEntries") == "2"
    assert _find(static, "{}Options").attrib == {
        "LegacyAllowPartialDownloadIfAp2Mismatch": "true"
    }


def test_parameter_types() -> None:
    types = {t.get("Name"): t for t in _all(_app(_with_parameters()), "ParameterType")}
    number = _find(types["t_number"], "{}TypeNumber")
    assert number.attrib == {
        "SizeInBit": "8",
        "Type": "unsignedInt",
        "minInclusive": "1",
        "maxInclusive": "100",
    }
    restriction = _find(types["t_enum"], "{}TypeRestriction")
    assert restriction.attrib == {"Base": "Value", "SizeInBit": "2"}
    enums = [e.attrib for e in restriction]
    assert enums == [
        {
            "Text": "Off",
            "Value": "0",
            "Id": f"{APP}_PT-t.5Fenum_EN-0",
            "DisplayOrder": "0",
        },
        {
            "Text": "On",
            "Value": "1",
            "Id": f"{APP}_PT-t.5Fenum_EN-1",
            "DisplayOrder": "1",
        },
    ]
    assert _find(types["t_text"], "{}TypeText").attrib == {"SizeInBit": "80"}
    assert types["t_page"].find(_NS + "TypeNone") is not None


def test_parameters_unions_and_refs() -> None:
    root = _app(_with_parameters())
    params = _find(root, ".//{}Parameters")
    tags = [c.tag.removeprefix(_NS) for c in params]
    assert tags == ["Parameter", "Parameter", "Union", "Parameter"]
    page = params[0]
    assert page.get("Id") == f"{APP}_P-0"
    assert page.find(_NS + "Memory") is None
    mode = params[1]
    assert mode.get("Text") == "Mode EN"
    assert mode.get("Value") == "1"
    memory = _find(mode, "{}Memory")
    assert memory.attrib == {
        "CodeSegment": f"{APP}_AS-4400",
        "Offset": "0",
        "BitOffset": "0",
    }
    union = params[2]
    assert union.get("SizeInBit") == "8"
    assert _find(union, "{}Memory").attrib == {
        "CodeSegment": f"{APP}_AS-4400",
        "Offset": "1",
        "BitOffset": "0",
    }
    members = [u.attrib for u in union.findall(_NS + "Parameter")]
    assert [m["Id"] for m in members] == [f"{APP}_UP-2", f"{APP}_UP-5"]
    assert members[0]["DefaultUnionParameter"] == "true"
    assert members[1]["BitOffset"] == "6"
    refs = {r.get("Id"): r.attrib for r in _all(root, "ParameterRef")}
    assert refs[f"{APP}_P-1_R-1"]["DisplayOrder"] == "2"
    # "Level alias" has the memory and type of "Level": one parameter, its own ref texts
    assert refs[f"{APP}_UP-2_R-4"] == {
        "Id": f"{APP}_UP-2_R-4",
        "RefId": f"{APP}_UP-2",
        "Name": "Level alias",
        "Text": "Alias EN",
        "Tag": "4",
        "DisplayOrder": "5",
        "Value": "5",
    }
    segment = _find(root, ".//{}Code/{}AbsoluteSegment[@Address='17408']")
    assert segment.get("Size") == "12"


def test_parameter_translations() -> None:
    root = _app(_with_parameters())
    de = next(
        lang for lang in _all(root, "Language") if lang.get("Identifier") == "de-DE"
    )
    elements = {
        e.get("RefId"): {t.get("AttributeName"): t.get("Text") for t in e}
        for e in de.iter(_NS + "TranslationElement")
    }
    assert elements[f"{APP}_P-1"] == {"Text": "Modus"}
    assert elements[f"{APP}_PT-t.5Fenum_EN-1"] == {"Text": "Ein"}
    assert elements[APP] == {"Name": "Dimmer Applikation"}
    assert elements[f"{APP}_UP-2"] == {"Text": "Pegel"}
    # the same text as the parameter's is not repeated for the second ref, another one is
    assert f"{APP}_UP-2_R-4" not in elements
    en = next(
        lang for lang in _all(root, "Language") if lang.get("Identifier") == "en-US"
    )
    refs = [e.get("RefId") for e in en.iter(_NS + "TranslationElement")]
    assert f"{APP}_UP-2_R-4" in refs


def test_dynamic_channel_blocks_and_conditions() -> None:
    root = _app(_with_parameters())
    channel = _find(root, ".//{}Dynamic/{}Channel")
    assert channel.attrib == {
        "Name": "Generic",
        "Text": "",
        "Number": "0",
        "Id": f"{APP}_CH-0",
    }
    block = _find(channel, "{}ParameterBlock")
    assert block.get("ParamRefId") == f"{APP}_P-0_R-0"
    children = [
        (c.tag.removeprefix(_NS), c.get("RefId") or c.get("ParamRefId")) for c in block
    ]
    assert children[0] == ("ParameterRefRef", f"{APP}_P-1_R-1")
    choose = next(c for c in block if c.tag == _NS + "choose")
    assert choose.get("ParamRefId") == f"{APP}_P-1_R-1"
    when = _find(choose, "{}when")
    assert when.get("test") == "1"
    assert [w.get("RefId") for w in when] == [f"{APP}_UP-2_R-2"]


def test_com_objects_and_refs() -> None:
    t = _with_parameters()
    t["communication_object"] = [
        _co(300, 0, 1, "Switch", 0, dpt=(1, 1)),
        _co(301, 0, 2, "Switch", 7, parameter=201, value="1"),
        _co(302, 1, 3, "Value", 7, dpt=(5, -1)),
    ]
    t["text_attribute"] += [_text(30, 1031, 20, 300, "Schalten")]
    root = _app(t)
    table = _find(root, ".//{}ComObjectTable")
    assert table.get("CodeSegment") == f"{APP}_AS-4200"
    objects = [o.attrib for o in table]
    assert objects[0]["Id"] == f"{APP}_O-0"
    assert (
        objects[0]["ObjectSize"] == "1 Byte"
    )  # the bigger type of the two rows with number 0
    assert objects[1]["DatapointType"] == "DPT-5"
    refs = [r.attrib for r in _all(root, "ComObjectRef")]
    assert refs[0]["Id"] == f"{APP}_O-0_R-1"
    assert refs[0]["ObjectSize"] == "1 Bit"
    assert refs[1]["Tag"] == "2"
    channel = _find(root, ".//{}Channel")
    direct = [c.get("RefId") for c in channel if c.tag == _NS + "ComObjectRefRef"]
    assert direct == [f"{APP}_O-0_R-1", f"{APP}_O-1_R-3"]
    conditional = [
        e.get("RefId")
        for e in channel.iter(_NS + "ComObjectRefRef")
        if e.get("RefId") not in direct
    ]
    assert conditional == [f"{APP}_O-0_R-2"]


def _co(
    ident: int, number: int, unique: int, name: str, kind: int, **kw: Any
) -> dict[str, Any]:
    dpt = kw.get("dpt")
    return {
        "PROGRAM_ID": 40,
        "OBJECT_NAME": name,
        "OBJECT_FUNCTION": f"{name} function",
        "OBJECT_READENABLED": 0,
        "OBJECT_WRITEENABLED": 1,
        "OBJECT_COMMENABLED": 1,
        "OBJECT_TRANSENABLED": 0,
        "PARENT_PARAMETER_VALUE": kw.get("value"),
        "OBJECT_ID": ident,
        "PARAMETER_ID": kw.get("parameter"),
        "OBJECT_NUMBER": number,
        "OBJECT_TYPE": kind,
        "OBJECT_PRIORITY": 3,
        "OBJECT_UPDATEENABLED": 0,
        "OBJECT_UNIQUE_NUMBER": unique,
        "EIB_DATA_TYPE_CODE": dpt[0] if dpt else None,
        "EIB_DATA_SUBTYPE_CODE": dpt[1] if dpt and dpt[1] >= 0 else None,
        "OBJECT_READONINITENABLED": 0,
    }


def test_load_procedures_and_code_segments() -> None:
    t = _base()
    segment = bytes(
        [0x03, 0x00, 0x00, 0x40, 0x00, 0x40, 0x03, 0x00, 0x01, 0x00]
    ) + bytes(6)
    t["s19_block"] = [
        _block(1, bytes([0x0F]) + bytes(15)),
        _block(2, bytes([0x04]) + bytes(15)),
        _block(3, segment, data=b"\x01\x02\x03\x04", mask=b"\x01\x00"),
        _block(4, bytes([0xF0, 0x13, 0x00, 0x01, 0x00, 0x02, 0x00, 0x00]) + bytes(8)),
        _block(5, bytes([0x0E]) + bytes(15), proc=1),
    ]
    root = _app(t)
    procedure = _find(root, ".//{}LoadProcedures/{}LoadProcedure")
    assert [(c.tag.removeprefix(_NS), dict(c.attrib)) for c in procedure] == [
        ("LdCtrlDisconnect", {}),
        ("LdCtrlUnload", {"LsmIdx": "0"}),
        (
            "LdCtrlAbsSegment",
            {
                "LsmIdx": "0",
                "SegType": "0",
                "Address": "16384",
                "Size": "4",
                "Access": "0",
                "MemType": "1",
                "SegFlags": "0",
            },
        ),
        ("LdCtrlTaskSegment", {"ObjType": "1", "Address": "0"}),
        ("LdCtrlConnect", {"AppliesTo": "par"}),
    ]
    segment_el = _find(root, ".//{}Code/{}AbsoluteSegment")
    assert segment_el.get("Id") == f"{APP}_AS-4000"
    assert _find(segment_el, "{}Data").text == "AQIDBA=="
    assert _find(segment_el, "{}Mask").text == "/wAAAA=="


def _block(number: int, record: bytes, **kw: Any) -> dict[str, Any]:
    return {
        "BLOCK_ID": 1000 + number,
        "BLOCK_NUMBER": number,
        "PROGRAM_ID": 40,
        "BLOCK_DATA": kw.get("data"),
        "BLOCK_MASK": kw.get("mask"),
        "RECORD": record,
        "MERGE_ID": None,
        "PROC_MASK": kw.get("proc", 0),
    }


def test_baggage_files(monkeypatch: pytest.MonkeyPatch) -> None:
    # The file time is local time; summer time in central Europe in this test.
    monkeypatch.setattr(vd_baggage, "utc_offset", lambda _: dt.timedelta(hours=2))
    t = _base()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("some/dir/readme.txt", b"hello")
    t["baggage"] = [
        {
            "ID": 900,
            "MANUFACTURERID": 131,
            "LOCALFILEDIR": "docs\\",
            "LOCALFILENAME": "readme.txt",
            "LOCALFILEATTRIBUTES": 1,
            "LOCALFILETIME": "2014-07-01 12:00:00.000",
            "LOCALFILEVERSIONHI": None,
            "LOCALFILEVERSIONLO": None,
            "COMPRESSION": "Z",
            "DATA": buffer.getvalue(),
        }
    ]
    t["applicationprogrambaggage"] = [
        {"ID": 901, "APPLICATIONPROGRAMID": 40, "BAGGAGEID": 900}
    ]
    files = convert(_db(t), MASTER)
    assert files["M-0083/Baggages/docs/readme.txt"] == b"hello"
    root = _xml(files, "M-0083/Baggages.xml")
    baggage = _find(root, ".//{}Baggage")
    assert baggage.attrib == {
        "TargetPath": "docs",
        "Name": "readme.txt",
        "Id": "M-0083_BG-docs-readme.2Etxt",
    }
    assert _find(baggage, "{}FileInfo").attrib == {
        "TimeInfo": "2014-07-01T10:00:00Z",
        "ReadOnly": "true",
    }
    app = _xml(files, f"M-0083/{APP}.xml")
    assert (
        _find(app, ".//{}Extension/{}Baggage").get("RefId")
        == "M-0083_BG-docs-readme.2Etxt"
    )


def test_unknown_manufacturer_is_reported() -> None:
    t = _base()
    t["hw_product"][0]["MANUFACTURER_ID"] = 99
    files, _warnings, errors = convert_with_report(_db(t), MASTER)
    assert "M-0083/Hardware.xml" not in files
    assert any("UnknownManufacturerId" in e for e in errors)


def test_sample_n148() -> None:
    path = _SAMPLES / "N148_12_700201.vd5"
    if not path.exists():
        pytest.skip("sample not available")
    from xknxeditor.prod.vd import read_dump
    from xknxeditor.prod.vd.upgrade import upgrade

    master = Path(r"C:\ProgramData\KNX\XML\project-14\knx_master.xml")
    if not master.exists():
        pytest.skip("master data not available")
    db = Database.from_dump(read_dump(path))
    upgrade(db)
    files = convert(db, master.read_bytes())
    assert sorted(files) == [
        "M-0001/Baggages.xml",
        "M-0001/Baggages/700201/Siemens_N14812.inf",
        "M-0001/Catalog.xml",
        "M-0001/Hardware.xml",
        "M-0001/M-0001_A-7002-01-0000.xml",
    ]
    app = _xml(files, "M-0001/M-0001_A-7002-01-0000.xml")
    assert (
        _find(app, ".//{}ApplicationProgram").get("MaskVersion")
        == "MV-0300-01000001000000000000"
    )


def test_parameter_calculation_and_program_description() -> None:
    t = _with_parameters()
    for row in t["parameter"]:
        row["CALCULATIONID"] = None
        row["CALCULATIONSET"] = None
        row["ALIASNAME"] = None
    t["parameter"][2].update(
        {"CALCULATIONID": 500, "CALCULATIONSET": "r", "ALIASNAME": "Raw"}
    )
    t["parameter"][1].update(
        {"CALCULATIONID": 500, "CALCULATIONSET": "L", "ALIASNAME": "Ui"}
    )
    t["parametercalculation"] = [
        {
            "ID": 500,
            "PROGRAMID": 40,
            "NUMBER": 3,
            "NAME": "Scale",
            "LANGUAGE": "VBScript",
            "LRTRANSFORMATION": "Raw = Ui".encode("cp1252").hex(),
            "RLTRANSFORMATION": "Ui = Raw",
        }
    ]
    t["program_description"] = [
        {"PROGRAM_ID": 40, "LANGUAGE_ID": 1033, "DISPLAY_ORDER": 1, "TEXT": "second"},
        {"PROGRAM_ID": 40, "LANGUAGE_ID": 1033, "DISPLAY_ORDER": 0, "TEXT": "first"},
    ]
    root = _app(t)
    pc = _find(root, ".//{}ParameterCalculations/{}ParameterCalculation")
    assert pc.attrib == {"Id": f"{APP}_PC-3", "Language": "VBScript", "Name": "Scale"}
    assert _find(pc, "{}LRTransformation").text == "Raw = Ui"
    assert _find(pc, "{}RLTransformation").text == "Ui = Raw"
    assert _find(pc, "{}LParameters/{}ParameterRefRef").attrib == {
        "RefId": f"{APP}_P-1_R-1",
        "AliasName": "Ui",
    }
    assert _find(pc, "{}RParameters/{}ParameterRefRef").attrib == {
        "RefId": f"{APP}_UP-2_R-2",
        "AliasName": "Raw",
    }
    app = _find(root, ".//{}ApplicationProgram")
    assert app.get("VisibleDescription") == "first\r\nsecond"


def _ex_im(flag: str) -> bytes:
    lines = [
        "EX-IM",
        "N C:/db.vd_",
        "V 6.3",
        "H virtual_device",
        "-----",
        "T 3 catalog_entry",
    ]
    lines += [f"C1 T3 1 4 {flag} CATALOG_ENTRY_ID", "C2 T3 1 4 N PRODUCT_ID"]
    for k, ident in enumerate((35, 30), start=1):
        lines += [f"R {k} T 3 catalog_entry", str(ident), "20"]
    lines += ["XXX", ""]
    return "\r\n".join(lines).encode("cp1252")


@pytest.mark.parametrize(("flag", "order"), [("Y", [30, 35]), ("N", [35, 30])])
def test_selects_follow_the_primary_key(flag: str, order: list[int]) -> None:
    from xknxeditor.prod.vd.context import Context
    from xknxeditor.prod.vd.dump import Dump
    from xknxeditor.prod.vd.master import MasterData

    db = Database.from_dump(Dump(_ex_im(flag)))
    ctx = Context(db, MasterData(MASTER))
    assert [
        r["CATALOG_ENTRY_ID"] for r in ctx.select("catalog_entry", "PRODUCT_ID", 20)
    ] == order

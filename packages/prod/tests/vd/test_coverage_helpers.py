"""Shared builders of the synthetic product databases in the ``test_coverage_*`` tests.

The tables are lists of row dicts (column names in any case). The master data holds what the
conversions need, with the ids and values of the KNX master data.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from xknxeditor.prod.vd.convert import convert_with_report
from xknxeditor.prod.vd.database import Database

NS = "{http://knx.org/xml/project/14}"
APP = "M-0083_A-1234-01-0000"

MASTER = b"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/23">
  <MasterData>
    <DatapointTypes>
      <DatapointType Id="DPT-1" Number="1" SizeInBit="1">
        <DatapointSubtypes>
          <DatapointSubtype Id="DPST-1-1" Number="1" />
          <DatapointSubtype Id="DPST-1-2" Number="2" />
        </DatapointSubtypes>
      </DatapointType>
      <DatapointType Id="DPT-5" Number="5" SizeInBit="8">
        <DatapointSubtypes><DatapointSubtype Id="DPST-5-1" Number="1" /></DatapointSubtypes>
      </DatapointType>
      <DatapointType Id="DPT-9" Number="9" SizeInBit="16" />
    </DatapointTypes>
    <MediumTypes>
      <MediumType Id="MT-0" Number="0" Name="TP" />
      <MediumType Id="MT-1" Number="1" Name="PL" />
      <MediumType Id="MT-5" Number="5" Name="IP" />
    </MediumTypes>
    <MaskVersions>
      <MaskVersion Id="MV-0001" MaskVersion="1" ManagementModel="None" MediumTypeRefId="MT-0" />
      <MaskVersion Id="MV-0012" MaskVersion="18" ManagementModel="Bcu1" MediumTypeRefId="MT-0">
        <HawkConfigurationData />
        <MaskEntries>
          <MaskEntry Id="MV-0012_ME-AllocBuf" Name="AllocBuf" />
          <MaskEntry Id="MV-0012_ME-rolA1" Name="rolA1" />
        </MaskEntries>
      </MaskVersion>
      <MaskVersion Id="MV-0020" MaskVersion="32" ManagementModel="Bcu2" MediumTypeRefId="MT-0">
        <HawkConfigurationData><Features><Feature Name="FirstAppObjectIdx" Value="4" /></Features></HawkConfigurationData>
      </MaskVersion>
      <MaskVersion Id="MV-0300" MaskVersion="768" ManagementModel="PropertyBased" MediumTypeRefId="MT-0" />
      <MaskVersion Id="MV-0300-01000000000000000000" MaskVersion="768" ManagementModel="PropertyBased" MediumTypeRefId="MT-0">
        <HawkConfigurationData><Features><Feature Name="FirstAppObjectIdx" Value="4" /></Features></HawkConfigurationData>
      </MaskVersion>
      <MaskVersion Id="MV-0300-01000001000000000000" MaskVersion="768" ManagementModel="PropertyBased" MediumTypeRefId="MT-0" />
      <MaskVersion Id="MV-0701" MaskVersion="1793" ManagementModel="BimM112" MediumTypeRefId="MT-0">
        <HawkConfigurationData><Features><Feature Name="FirstAppObjectIdx" Value="5" /></Features></HawkConfigurationData>
      </MaskVersion>
      <MaskVersion Id="MV-07B0" MaskVersion="1968" ManagementModel="SystemB" MediumTypeRefId="MT-0">
        <HawkConfigurationData><Features><Feature Name="FirstAppObjectIdx" Value="6" /></Features></HawkConfigurationData>
      </MaskVersion>
      <MaskVersion Id="MV-091A" MaskVersion="2330" ManagementModel="Bcu1" MediumTypeRefId="MT-0" />
      <MaskVersion Id="MV-5705" MaskVersion="22277" ManagementModel="BimM112" MediumTypeRefId="MT-5">
        <HawkConfigurationData><Features><Feature Name="FirstAppObjectIdx" Value="5" /></Features></HawkConfigurationData>
      </MaskVersion>
    </MaskVersions>
    <Manufacturers>
      <Manufacturer Id="M-0001" KnxManufacturerId="1" Name="Siemens" DefaultLanguage="en-US" />
      <Manufacturer Id="M-0002" KnxManufacturerId="2" Name="ABB" DefaultLanguage="en-US" />
      <Manufacturer Id="M-0004" KnxManufacturerId="4" Name="Jung" DefaultLanguage="en-US" />
      <Manufacturer Id="M-0008" KnxManufacturerId="8" Name="Gira" DefaultLanguage="en-US" />
      <Manufacturer Id="M-0069" KnxManufacturerId="105" Name="RTS" DefaultLanguage="en-US" />
      <Manufacturer Id="M-007E" KnxManufacturerId="126" Name="Carrier" DefaultLanguage="en-US" />
      <Manufacturer Id="M-0083" KnxManufacturerId="131" Name="Test" DefaultLanguage="en-US" />
      <Manufacturer Id="M-00C9" KnxManufacturerId="201" Name="Other" DefaultLanguage="de-DE" />
      <Manufacturer Id="M-00FD" KnxManufacturerId="253" Name="HVAC" DefaultLanguage="en-US" />
    </Manufacturers>
  </MasterData>
</KNX>"""

Rows = list[dict[str, Any]]
Tables = dict[str, Rows]


def db(tables: Tables) -> Database:
    """A schema 6.3 database holding ``tables`` (the columns the rows use)."""
    database = Database((6 << 16) | 3)
    for name, rows in tables.items():
        columns: list[str] = []
        for r in rows:
            for c in r:
                if c.upper() not in columns:
                    columns.append(c.upper())
        table = database.add_table(name, columns)
        for r in rows:
            table.append({k.upper(): v for k, v in r.items()})
    return database


def run(tables: Tables) -> tuple[dict[str, bytes], list[str], list[str]]:
    return convert_with_report(db(tables), MASTER)


def xml(files: dict[str, bytes], name: str) -> ET.Element:
    return ET.fromstring(files[name])


def find(root: ET.Element, path: str) -> ET.Element:
    el = root.find(path.replace("{}", NS))
    assert el is not None, path
    return el


def findall(root: ET.Element, path: str) -> list[ET.Element]:
    return root.findall(path.replace("{}", NS))


def every(root: ET.Element, tag: str) -> list[ET.Element]:
    return list(root.iter(NS + tag))


def tag(el: ET.Element) -> str:
    return el.tag.removeprefix(NS)


def children(el: ET.Element) -> list[tuple[str, dict[str, str]]]:
    return [(tag(c), dict(c.attrib)) for c in el]


def translations(root: ET.Element, language: str) -> dict[str, dict[str, str]]:
    """The translated attributes per element id of one language."""
    out: dict[str, dict[str, str]] = {}
    for lang in every(root, "Language"):
        if lang.get("Identifier") != language:
            continue
        for te in lang.iter(NS + "TranslationElement"):
            texts = out.setdefault(te.get("RefId") or "", {})
            for t in te:
                texts[t.get("AttributeName") or ""] = t.get("Text") or ""
    return out


def text(
    ident: int, lcid: int, column: int, entity: int, value: str | None
) -> dict[str, Any]:
    return {
        "TEXT_ATTRIBUTE_ID": ident,
        "LANGUAGE_ID": lcid,
        "COLUMN_ID": column,
        "ENTITY_ID": entity,
        "TEXT_ATTRIBUTE_TEXT": value,
    }


def program(**kw: Any) -> dict[str, Any]:
    """An ``application_program`` row (number 0x1234, version 1, mask 50)."""
    row: dict[str, Any] = {
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
    row.update({k.upper(): v for k, v in kw.items()})
    return row


def base(mask: int = 0x701, **program_columns: Any) -> Tables:
    """One device: catalog section and entry, hardware, program (with ``mask``), virtual device."""
    return {
        "manufacturer": [{"MANUFACTURER_ID": 131, "MANUFACTURER_NAME": "Test"}],
        "functional_entity": [
            {
                "FUNCTIONAL_ENTITY_ID": 10,
                "MANUFACTURER_ID": 131,
                "FUNCTIONAL_ENTITY_NAME": "Lighting",
                "FUNCTIONAL_ENTITY_NUMB": "L",
                "FUNCTIONAL_ENTITY_DESCRIPTION": None,
                "FUN_FUNCTIONAL_ENTITY_ID": None,
            }
        ],
        "hw_product": [
            {
                "PRODUCT_ID": 20,
                "MANUFACTURER_ID": 131,
                "PRODUCT_NAME": "Dimmer HW",
                "PRODUCT_VERSION_NUMBER": 2,
                "COMPONENT_ATTRIBUTES": 10,
                "BUS_CURRENT": 10,
                "PRODUCT_SERIAL_NUMBER": "D1",
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
                "ENTRY_STATUS_CODE": 10,
                "REGISTRATION_DATE": "2010-05-04",
            }
        ],
        "application_program": [program(**program_columns)],
        "mask": [{"MASK_ID": 50, "MASK_VERSION": mask}],
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
                "FUNCTIONAL_ENTITY_ID": 10,
                "PEI_PROGRAM_ID": None,
                "VIRTUAL_DEVICE_NUMBER": 1,
            }
        ],
        "ete_language": [
            {"LANGUAGE_ID": 1031, "LANGUAGE_NAME": "German", "DATABASE_LANGUAGE": 0},
            {"LANGUAGE_ID": 1033, "LANGUAGE_NAME": "English", "DATABASE_LANGUAGE": 1},
        ],
        "text_attribute": [],
        # present (empty) in every dump of the newest schema
        "s19_block": [],
    }


def block(number: int, record: bytes | None, **kw: Any) -> dict[str, Any]:
    """An ``s19_block`` row of program 40."""
    return {
        "BLOCK_ID": 1000 + number,
        "BLOCK_NUMBER": number,
        "PROGRAM_ID": 40,
        "BLOCK_DATA": kw.get("data"),
        "BLOCK_MASK": kw.get("mask"),
        "RECORD": record,
        "MERGE_ID": kw.get("merge"),
        "PROC_MASK": kw.get("proc", 0),
    }


def ptype(
    ident: int, atomic: int | None, name: str, size: int | None, **kw: Any
) -> dict[str, Any]:
    """A ``parameter_type`` row of program 40."""
    return {
        "PARAMETER_TYPE_ID": ident,
        "ATOMIC_TYPE_NUMBER": atomic,
        "PROGRAM_ID": 40,
        "PARAMETER_TYPE_NAME": name,
        "PARAMETER_MINIMUM_VALUE": kw.get("minimum"),
        "PARAMETER_MAXIMUM_VALUE": kw.get("maximum"),
        "PARAMETER_TYPE_DESCRIPTION": kw.get("description"),
        "PARAMETER_TYPE_SIZE": size,
        "PARAMETER_UI_HINT": kw.get("hint"),
    }


def value(
    type_id: int, ident: int, real: int | None, shown: str, order: int | None, **kw: Any
) -> dict[str, Any]:
    """A ``parameter_list_of_values`` row."""
    return {
        "PARAMETER_TYPE_ID": type_id,
        "REAL_VALUE": real,
        "DISPLAYED_VALUE": shown,
        "DISPLAY_ORDER": order,
        "PARAMETER_VALUE_ID": ident,
        "BINARY_VALUE": kw.get("binary"),
    }


def param(
    ident: int, number: int | None, name: str, type_id: int | None, **kw: Any
) -> dict[str, Any]:
    """A ``parameter`` row of program 40 (keywords: space, address, bit, default, text, order,
    parent, value, low, high, patch, object, property)."""
    return {
        "PROGRAM_ID": 40,
        "PARAMETER_TYPE_ID": type_id,
        "PARAMETER_NUMBER": number,
        "PARAMETER_NAME": name,
        "PARAMETER_LOW_ACCESS": kw.get("low", 1),
        "PARAMETER_HIGH_ACCESS": kw.get("high", 2),
        "PARENT_PARM_VALUE": kw.get("value"),
        "PARAMETER_DISPLAY_ORDER": kw.get("order"),
        "PARAMETER_ADDRESS": kw.get("address"),
        "PARAMETER_BITOFFSET": kw.get("bit"),
        "PARAMETER_DESCRIPTION": kw.get("description", f"{name} text"),
        "PARAMETER_ID": ident,
        "PAR_PARAMETER_ID": kw.get("parent"),
        "PARAMETER_DEFAULT_LONG": kw.get("default"),
        "PARAMETER_DEFAULT_STRING": kw.get("text"),
        "PATCH_ALWAYS": kw.get("patch", 0),
        "ADDRESS_SPACE": kw.get("space"),
        "EIB_OBJECT_REF": kw.get("object"),
        "EIB_PROPERTY_ID": kw.get("property"),
    }


def com_object(
    ident: int, number: int | None, unique: int, name: str, kind: int, **kw: Any
) -> dict[str, Any]:
    """A ``communication_object`` row of program 40."""
    dpt = kw.get("dpt")
    return {
        "PROGRAM_ID": 40,
        "OBJECT_NAME": name,
        "OBJECT_FUNCTION": kw.get("function", f"{name} function"),
        "OBJECT_READENABLED": kw.get("read", 0),
        "OBJECT_WRITEENABLED": kw.get("write", 1),
        "OBJECT_COMMENABLED": kw.get("comm", 1),
        "OBJECT_TRANSENABLED": kw.get("trans", 0),
        "PARENT_PARAMETER_VALUE": kw.get("value"),
        "OBJECT_ID": ident,
        "PARAMETER_ID": kw.get("parameter"),
        "OBJECT_NUMBER": number,
        "OBJECT_TYPE": kind,
        "OBJECT_PRIORITY": kw.get("priority", 3),
        "OBJECT_UPDATEENABLED": kw.get("update", 0),
        "OBJECT_UNIQUE_NUMBER": unique,
        "EIB_DATA_TYPE_CODE": dpt[0] if dpt else None,
        "EIB_DATA_SUBTYPE_CODE": dpt[1] if dpt and dpt[1] >= 0 else None,
        "OBJECT_READONINITENABLED": kw.get("init", 0),
    }

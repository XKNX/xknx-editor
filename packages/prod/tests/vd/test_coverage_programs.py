"""Program variants: masks, default segments, options, fixups, extensions, calculations."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import pytest

from .test_coverage_helpers import (
    Tables,
    base,
    children,
    every,
    find,
    findall,
    param,
    program,
    ptype,
    run,
    tag,
    text,
    translations,
    xml,
)


def _hw(ident: int, serial: str, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "PRODUCT_ID": ident,
        "MANUFACTURER_ID": 131,
        "PRODUCT_NAME": f"HW {serial}",
        "PRODUCT_VERSION_NUMBER": 1,
        "COMPONENT_ATTRIBUTES": 10,
        "BUS_CURRENT": None,
        "PRODUCT_SERIAL_NUMBER": serial,
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


def programs_tables() -> Tables:
    t = base()
    t["mask"] += [
        {"MASK_ID": 51, "MASK_VERSION": 0x12},
        {"MASK_ID": 52, "MASK_VERSION": 0x300},
        {"MASK_ID": 53, "MASK_VERSION": 0x91A},
        {"MASK_ID": 54, "MASK_VERSION": 0x20},
        {"MASK_ID": 55, "MASK_VERSION": 0x5705},
    ]
    t["application_program"] += [
        program(
            program_id=41,
            device_type=0x2001,
            original_manufacturer_id=4,
            minetsversion=0x03000064,
            help_file_name="help.chm",
            context_id=7,
            pei_type=None,
            linkable=1,
        ),
        program(program_id=42, device_type=0x2002, mask_id=None, program_style=0),
        program(
            program_id=43,
            device_type=0x2003,
            mask_id=51,
            program_style=0,
            eeprom_data=bytes(range(30)),
            assoctab_address=0x50,
            commstab_address=0x120,
            minetsversion=0x04000000,
        ),
        program(
            program_id=44, device_type=0x2004, mask_id=52, minetsversion=0x03000066
        ),
        program(
            program_id=45, device_type=0x2005, mask_id=53, minetsversion=0x03000001
        ),
        program(
            program_id=46, device_type=0x2006, program_type=1, minetsversion=0x05000000
        ),
        program(
            program_id=47,
            device_type=0x2007,
            mask_id=54,
            manufacturer_id=2,
            program_style=2,
        ),
        program(program_id=48, device_type=0x2008, mask_id=55),
        # the same program id twice: the better registered one stays
        program(
            program_id=49,
            device_type=0x2009,
            program_version="1.0",
            program_name="older",
        ),
        program(
            program_id=56, device_type=0x2009, program_version="1", program_name="newer"
        ),
        program(
            program_id=57,
            device_type=0x1234,
            program_version="1.5",
            program_name="worse",
        ),
    ]
    t["hw_product"] += [
        _hw(21, "S300", BCU_TYPE_NUMBER=3),
        _hw(22, "Coupler"),
        _hw(23, "Pei"),
    ]
    t["product_to_program"] += [
        _p2p(62, 21, 44),
        _p2p(63, 22, 45),
        _p2p(64, 23, 41, PEI_PROGRAM_ID=46),
        _p2p(65, 23, 56, REGISTRATION_NUMBER=5, REGISTRATION_YEAR=111),
    ]
    t["text_attribute"] = [
        text(1, 1031, 80, 41, "Programm"),
        text(2, 1033, 80, 41, "Program EN"),
    ]
    t["help_file"] = [
        {
            "HELP_FILE_ID": 1,
            "LANGUAGE_ID": 1033,
            "ENTITY_ID": 41,
            "HELP_FILE_NAME": "help_en.chm",
        },
        {
            "HELP_FILE_ID": 2,
            "LANGUAGE_ID": 1031,
            "ENTITY_ID": 41,
            "HELP_FILE_NAME": "hilfe.chm",
        },
        {
            "HELP_FILE_ID": 3,
            "LANGUAGE_ID": 1040,
            "ENTITY_ID": 41,
            "HELP_FILE_NAME": None,
        },
        {
            "HELP_FILE_ID": 4,
            "LANGUAGE_ID": 9999,
            "ENTITY_ID": 41,
            "HELP_FILE_NAME": "x.chm",
        },
    ]
    t["program_description"] = [
        {
            "PROGRAM_DESCRIPTION_ID": 2,
            "PROGRAM_ID": 41,
            "LANGUAGE_ID": 1031,
            "DISPLAY_ORDER": 0,
            "TEXT": "Zeile",
        },
        {
            "PROGRAM_DESCRIPTION_ID": 3,
            "PROGRAM_ID": 41,
            "LANGUAGE_ID": 9999,
            "DISPLAY_ORDER": 0,
            "TEXT": "unknown",
        },
        {
            "PROGRAM_DESCRIPTION_ID": 4,
            "PROGRAM_ID": 41,
            "LANGUAGE_ID": 1036,
            "DISPLAY_ORDER": 0,
            "TEXT": "",
        },
    ]
    t["program_plugin"] = [
        {
            "PROGRAM_PLUGIN_ID": 1,
            "PROGRAM_ID": 41,
            "PLUGIN_TYPE": 1,
            "PLUGIN_CLSID": "{15053d1a-12de-462a-aee9-e967a27eff5b}",
        },
        {
            "PROGRAM_PLUGIN_ID": 2,
            "PROGRAM_ID": 41,
            "PLUGIN_TYPE": 2,
            "PLUGIN_CLSID": "{11111111-2222-3333-4444-555555555555}",
        },
        {
            "PROGRAM_PLUGIN_ID": 3,
            "PROGRAM_ID": 41,
            "PLUGIN_TYPE": 3,
            "PLUGIN_CLSID": "{dd53ace6-6d8e-4715-b7f8-5850ef2dbf28}",
        },
    ]
    t["applicationprogramattributes"] = [
        {
            "ID": 1,
            "APPLICATIONPROGRAMID": 43,
            "ATTRIBUTENAME": "HawkCompatibilityFlags",
            "ATTRIBUTEVALUE": "2047",
        },
        {
            "ID": 2,
            "APPLICATIONPROGRAMID": 43,
            "ATTRIBUTENAME": "DomCompatibilityFlags",
            "ATTRIBUTEVALUE": "7",
        },
        {
            "ID": 3,
            "APPLICATIONPROGRAMID": 43,
            "ATTRIBUTENAME": "Other",
            "ATTRIBUTEVALUE": "1",
        },
        {
            "ID": 4,
            "APPLICATIONPROGRAMID": 47,
            "ATTRIBUTENAME": "DomCompatibilityFlags",
            "ATTRIBUTEVALUE": "2",
        },
    ]
    t["address_fixup"] = [
        {
            "FIXUP_ID": 1,
            "PROGRAM_ID": 43,
            "FIXUP_TYPE": 0,
            "FIXUP_NAME": "rolA1",
            "FIXUP_ADDRESS": 0x110,
        },
        {
            "FIXUP_ID": 2,
            "PROGRAM_ID": 43,
            "FIXUP_TYPE": 0,
            "FIXUP_NAME": "AllocBuf",
            "FIXUP_ADDRESS": 0x112,
        },
        {
            "FIXUP_ID": 3,
            "PROGRAM_ID": 43,
            "FIXUP_TYPE": 0,
            "FIXUP_NAME": "rolA1",
            "FIXUP_ADDRESS": 0x115,
        },
        {
            "FIXUP_ID": 4,
            "PROGRAM_ID": 43,
            "FIXUP_TYPE": 0,
            "FIXUP_NAME": "unknown",
            "FIXUP_ADDRESS": 0x118,
        },
        {
            "FIXUP_ID": 5,
            "PROGRAM_ID": 43,
            "FIXUP_TYPE": 0,
            "FIXUP_NAME": "rolA1",
            "FIXUP_ADDRESS": 0x5000,
        },
    ]
    t["parameter_type"] = [
        ptype(100, 1, "t_u8", 8),
        {**ptype(101, 1, "t_u8", 8), "PROGRAM_ID": 47},
    ]
    t["parameter"] = [
        param(200, 0, "A", 100, space=2, address=0x4400, bit=0, order=1),
        {
            **param(201, 0, "B", 101, space=2, address=0x4400, bit=0, order=1),
            "PROGRAM_ID": 47,
        },
        {
            **param(202, 1, "C", 101, space=2, address=0x4401, bit=0, order=2),
            "PROGRAM_ID": 47,
        },
        {
            **param(203, 2, "D", 101, space=2, address=0x4402, bit=0, order=3),
            "PROGRAM_ID": 47,
        },
    ]
    for row in t["parameter"]:
        row.update({"CALCULATIONID": None, "CALCULATIONSET": None, "ALIASNAME": None})
    t["parameter"][1].update(
        {"CALCULATIONID": 501, "CALCULATIONSET": "l", "ALIASNAME": "x"}
    )
    t["parameter"][2].update({"CALCULATIONID": 501, "CALCULATIONSET": "R"})
    t["parameter"][0].update({"CALCULATIONID": 501, "CALCULATIONSET": "L"})
    t["parametercalculation"] = [
        {
            "ID": 501,
            "PROGRAMID": 47,
            "NUMBER": 1,
            "NAME": None,
            "LANGUAGE": "javascript",
            "LRTRANSFORMATION": "x = y;",
            "RLTRANSFORMATION": "y = x;".encode("cp1252").hex().upper(),
        },
        {
            "ID": 502,
            "PROGRAMID": 47,
            "NUMBER": 2,
            "NAME": "Empty",
            "LANGUAGE": "Basic",
            "LRTRANSFORMATION": "",
            "RLTRANSFORMATION": "z",
        },
        {
            "ID": 503,
            "PROGRAMID": 47,
            "NUMBER": 3,
            "NAME": "No language",
            "LANGUAGE": None,
            "LRTRANSFORMATION": None,
            "RLTRANSFORMATION": None,
        },
    ]
    return t


def null_language_tables() -> Tables:
    t = programs_tables()
    t["program_description"].insert(
        0,
        {
            "PROGRAM_DESCRIPTION_ID": 1,
            "PROGRAM_ID": 41,
            "LANGUAGE_ID": None,
            "DISPLAY_ORDER": 0,
            "TEXT": "lost",
        },
    )
    return t


def calculation_set_tables() -> Tables:
    t = programs_tables()
    t["parameter"][3].update({"CALCULATIONID": 501, "CALCULATIONSET": "Q"})
    return t


def no_texts_tables() -> Tables:
    t = programs_tables()
    t["text_attribute"] = []
    return t


def french_tables() -> Tables:
    t = programs_tables()
    t["program_description"][-1]["TEXT"] = "Ligne"
    return t


def no_plugin_tables() -> Tables:
    t = programs_tables()
    t["program_plugin"][0]["PLUGIN_CLSID"] = "{22222222-2222-3333-4444-555555555555}"
    return t


def _apps(tables: Tables) -> tuple[dict[str, ET.Element], list[str], list[str]]:
    files, warnings, errors = run(tables)
    apps: dict[str, ET.Element] = {}
    for name in files:
        if "_A-" in name:
            app = find(xml(files, name), ".//{}ApplicationProgram")
            apps[app.get("Id") or ""] = app
    return apps, warnings, errors


def _attrs(app: ET.Element) -> dict[str, str]:
    """The program's attributes besides those every converted program has."""
    common = {
        "ApplicationVersion": "1",
        "DefaultLanguage": "en-US",
        "DynamicTableManagement": "true",
        "PreEts4Style": "true",
        "ConvertedFromPreEts4Data": "true",
        "PeiType": "0",
        "HelpFile": "",
        "Linkable": "false",
        "Name": "Dimmer app",
        "ProgramType": "ApplicationProgram",
        "LoadProcedureStyle": "DefaultProcedure",
    }
    return {
        k: v
        for k, v in app.attrib.items()
        if common.get(k) != v and k not in ("Id", "ApplicationNumber")
    }


def _static(app: ET.Element, name: str) -> dict[str, str]:
    return dict(find(app, "{}Static/{}" + name).attrib)


A01 = "M-0083_A-2001-01-0000-O0004"


def test_program_attributes() -> None:
    apps, _warnings, _errors = _apps(programs_tables())
    assert sorted(apps) == [
        "M-0002_A-2007-01-0000",
        "M-0083_A-1234-01-0000",
        A01,
        "M-0083_A-2002-01-0000",
        "M-0083_A-2003-01-0000",
        "M-0083_A-2004-01-0000",
        "M-0083_A-2005-01-0000",
        "M-0083_A-2006-01-0000",
        "M-0083_A-2008-01-0000",
        "M-0083_A-2009-01-0000",
    ]
    attrs = {k[9:13]: _attrs(app) for k, app in apps.items()}
    assert attrs == {
        "1234": {"MaskVersion": "MV-0701"},
        "2001": {
            "MaskVersion": "MV-0701",
            "Name": "Program EN",
            "HelpTopic": "7",
            "HelpFile": "help_en.chm",
            "Linkable": "true",
            "MinEtsVersion": "3.0d",
            "OriginalManufacturer": "M-0004",
        },
        "2002": {"MaskVersion": "MV-0001", "Broken": "true"},
        "2003": {"MaskVersion": "MV-0012", "MinEtsVersion": "4.0"},
        "2004": {
            "MaskVersion": "MV-0300-01000000000000000000",
            "MinEtsVersion": "3.0f",
        },
        "2005": {
            "MaskVersion": "MV-091A",
            "MinEtsVersion": "3.0",
            "IPConfig": "Custom",
        },
        "2006": {
            "MaskVersion": "MV-0701",
            "MinEtsVersion": "3.0",
            "ProgramType": "PeiProgram",
        },
        "2007": {"MaskVersion": "MV-0020"},
        "2008": {"MaskVersion": "MV-5705", "IPConfig": "Custom"},
        # of two programs with one id the better registered one is kept
        "2009": {"MaskVersion": "MV-0701", "Name": "newer"},
    }


def test_program_tables_and_default_segments() -> None:
    apps, warnings, errors = _apps(programs_tables())
    bcu1 = apps["M-0083_A-2003-01-0000"]
    code = find(bcu1, ".//{}Code/{}AbsoluteSegment")
    assert dict(code.attrib) == {
        "Id": "M-0083_A-2003-01-0000_AS-0100",
        "Address": "256",
        "Size": "256",
    }
    # the EEPROM image: byte 22 set to 1, the mask clears bytes 1-3 and 23-24
    assert [c.text for c in code] == [
        "AAECAwQFBgcICQoLDA0ODxAREhMUFQEXGBkaGxwd",
        "/wAAAP////////////////////////8AAP//////",
    ]
    assert _static(bcu1, "AddressTable") == {
        "CodeSegment": "M-0083_A-2003-01-0000_AS-0100",
        "Offset": "22",
        "MaxEntries": "3",
    }
    # the association table below the segment has no code segment
    assert _static(bcu1, "AssociationTable") == {"MaxEntries": "2"}
    assert _static(bcu1, "ComObjectTable") == {
        "CodeSegment": "M-0083_A-2003-01-0000_AS-0100",
        "Offset": "32",
    }
    fixups = findall(bcu1, ".//{}FixupList/{}Fixup")
    assert [(f.get("FunctionRef"), [o.text for o in f]) for f in fixups] == [
        ("MV-0012_ME-AllocBuf", ["18"]),
        ("MV-0012_ME-rolA1", ["16", "21"]),
    ]
    dummy = apps["M-0083_A-2002-01-0000"]
    assert children(find(dummy, ".//{}Code")) == [
        (
            "AbsoluteSegment",
            {"Id": "M-0083_A-2002-01-0000_AS-0000", "Address": "0", "Size": "65536"},
        )
    ]
    assert _static(dummy, "AssociationTable")["Offset"] == "16640"
    coupler = apps["M-0083_A-2005-01-0000"]
    assert [tag for tag, _ in children(find(coupler, "{}Static"))] == [
        "Code",
        "Options",
    ]
    pei = apps["M-0083_A-2006-01-0000"]
    assert children(find(pei, ".//{}Code")) == []
    bcu2 = apps["M-0002_A-2007-01-0000"]
    assert children(find(bcu2, ".//{}Code"))[0][1]["Size"] == "880"
    assert [w.split(":")[0] for w in warnings] == ["AppPrgHasNoMaskVersion"]
    assert [e.split(":")[0] for e in errors] == [
        "UnknownApplicationProgramAttributeName",
        "NoAbsoluteSegmentForFixup",
        "UnknownParameterCalculcationLanguage",
    ]


def test_options_and_extensions() -> None:
    apps, _warnings, _errors = _apps(programs_tables())
    flags = _static(apps["M-0083_A-2003-01-0000"], "Options")
    assert flags == {
        name: "true"
        for name in (
            "PreferPartialDownloadIfApplicationLoaded",
            "EasyCtrlModeModeStyleEmptyGroupComTables",
            "SetObjectTableLengthAlwaysToOne",
            "LegacyNoPartialDownload",
            "LegacyNoMemoryVerifyMode",
            "LegacyNoOptimisticWrite",
            "LegacyDoNotReportPropertyWriteErrors",
            "LegacyNoBackgroundDownload",
            "LegacyDoNotCheckManufacturerId",
            "LegacyAlwaysReloadAppIfCoVisibilityChanged",
            "LegacyNeverReloadAppIfCoVisibilityChanged",
            "LegacyDoNotSupportUndoDelete",
            "LegacyAllowPartialDownloadIfAp2Mismatch",
            "LegacyKeepObjectTableGaps",
        )
    }
    assert _static(apps["M-0002_A-2007-01-0000"], "Options") == {
        "PartialDownloadOnlyVisibleParameters": "true",
        "LegacyAlwaysReloadAppIfCoVisibilityChanged": "true",
    }
    system300 = apps["M-0083_A-2004-01-0000"]
    assert _static(system300, "Extension") == {
        "EtsDownloadPlugin": "{2C37063B-67BC-4CAD-9BCA-3BF516DA7995}"
    }
    extension = _static(apps[A01], "Extension")
    assert extension["EtsUiPlugin"] == "{11111111-2222-3333-4444-555555555555}"
    assert extension["EtsDataHandler"] == "{DD53ACE6-6D8E-4715-B7F8-5850EF2DBF28}"
    options = _static(apps[A01], "Options")
    assert {k: options[k] for k in options if k not in _REPLACED_PLUGIN_OPTIONS} == {
        "PreferPartialDownloadIfApplicationLoaded": "true",
        "ParameterByteOrder": "LittleEndian",
        "LegacyAllowPartialDownloadIfAp2Mismatch": "true",
        "LegacyProxyCommunicationObjects": "true",
    }


_REPLACED_PLUGIN_OPTIONS = (
    "PromptBeforeFullDownload",
    "LegacyPatchManufacturerIdInTaskSegment",
)


@pytest.mark.xfail(
    reason="the converter replaces the byte swapper download plugins 15053D1A-... and "
    "A81BCB76-...: no EtsDownloadPlugin, options PromptBeforeFullDownload and "
    "LegacyPatchManufacturerIdInTaskSegment",
    strict=True,
)
def test_byte_swapper_plugin_is_replaced_by_options() -> None:
    apps, _warnings, _errors = _apps(programs_tables())
    assert "EtsDownloadPlugin" not in _static(apps[A01], "Extension")
    options = _static(apps[A01], "Options")
    assert {k: options.get(k) for k in _REPLACED_PLUGIN_OPTIONS} == {
        "PromptBeforeFullDownload": "true",
        "LegacyPatchManufacturerIdInTaskSegment": "true",
    }


def test_other_download_plugin_stays() -> None:
    apps, _warnings, _errors = _apps(no_plugin_tables())
    assert _static(apps[A01], "Extension")["EtsDownloadPlugin"] == (
        "{22222222-2222-3333-4444-555555555555}"
    )
    assert "ParameterByteOrder" not in _static(apps[A01], "Options")


def test_program_translations() -> None:
    files, _warnings, _errors = run(french_tables())
    root = xml(files, f"M-0083/{A01}.xml")
    assert translations(root, "de-DE") == {
        A01: {
            "Name": "Programm",
            "VisibleDescription": "Zeile",
            "HelpFile": "hilfe.chm",
        }
    }
    assert translations(root, "en-US") == {
        A01: {"Name": "Program EN", "HelpFile": "help_en.chm"}
    }
    assert translations(root, "fr-FR") == {A01: {"VisibleDescription": "Ligne"}}


def test_description_without_language_is_reported() -> None:
    _files, warnings, _errors = run(null_language_tables())
    assert [w.split(":")[0] for w in warnings] == [
        "NoLanguageIdForTranslation",
        "AppPrgHasNoMaskVersion",
    ]


@pytest.mark.xfail(
    reason="without any text_attribute rows the converter writes no translations (and no "
    "HelpFile from help_file) for program descriptions and help files",
    strict=True,
)
def test_no_translations_without_text_attributes() -> None:
    files, _warnings, _errors = run(no_texts_tables())
    root = xml(files, f"M-0083/{A01}.xml")
    assert every(root, "Languages") == []
    assert find(root, ".//{}ApplicationProgram").get("HelpFile") == "help.chm"


def test_parameter_calculation() -> None:
    apps, _warnings, _errors = _apps(programs_tables())
    app = apps["M-0002_A-2007-01-0000"]
    calculations = findall(app, ".//{}ParameterCalculations/{}ParameterCalculation")
    # only the calculation with scripts in a known language and both parameter sides
    assert len(calculations) == 1
    pc = calculations[0]
    assert {k: v for k, v in pc.attrib.items() if k != "Name"} == {
        "Id": "M-0002_A-2007-01-0000_PC-1",
        "Language": "JavaScript",
    }
    assert [(tag(c), c.text if not len(c) else children(c)) for c in pc] == [
        ("RLTransformation", "y = x;"),
        ("LRTransformation", "x = y;"),
        (
            "LParameters",
            [
                (
                    "ParameterRefRef",
                    {"RefId": "M-0002_A-2007-01-0000_P-0_R-0", "AliasName": "x"},
                )
            ],
        ),
        (
            "RParameters",
            [("ParameterRefRef", {"RefId": "M-0002_A-2007-01-0000_P-1_R-1"})],
        ),
    ]


@pytest.mark.xfail(
    reason='the converter writes Name="" for a calculation whose name is NULL',
    strict=True,
)
def test_parameter_calculation_without_name() -> None:
    apps, _warnings, _errors = _apps(programs_tables())
    pc = find(apps["M-0002_A-2007-01-0000"], ".//{}ParameterCalculation")
    assert pc.get("Name") == ""


@pytest.mark.xfail(
    reason="a calculation set other than L/R makes the converter fail the whole program "
    "(ApplicationProgramConversionFailed, no file)",
    strict=True,
)
def test_illegal_calculation_set_fails_the_program() -> None:
    files, _warnings, errors = run(calculation_set_tables())
    assert "M-0002/M-0002_A-2007-01-0000.xml" not in files
    assert any(e.startswith("ApplicationProgramConversionFailed") for e in errors)


def test_illegal_calculation_set_is_reported() -> None:
    _files, _warnings, errors = run(calculation_set_tables())
    assert any(e.startswith("IllegalParameterCalculationSet") for e in errors)

"""Every kind of 16-byte load record as ``LdCtrl*`` element of a converted program."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from xknxeditor.prod.vd.loadctl import LoadControls

from .test_coverage_helpers import (
    APP,
    Tables,
    base,
    block,
    children,
    find,
    findall,
    run,
    tag,
    xml,
)


def _r(*data: int) -> bytes:
    return bytes(data).ljust(16, b"\0")


# standard records: (record, proc mask)
STANDARD: list[tuple[bytes, int]] = [
    (_r(0x0F), 0),
    (_r(0x21), 0),
    (_r(0x02), 1),
    (_r(0x14), 2),
    (_r(0x03, 0x00, 0x00, 0x40, 0x00, 0x40, 0x0F, 0x32, 0x01, 0x00), 0),
    (_r(0x13, 0x01, 0x00, 0x00, 0x60, 0x00, 0x60, 0x0F, 0x33, 0x12, 0x01), 3),
    (_r(0x03, 0x02, 0x00, 0x41, 0x23), 128),
    (_r(0x03, 0x03, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06), 129),
    (_r(0x03, 0x04, 0x00, 0x12, 0x34, 0x07), 130),
    (_r(0x03, 0x05, 0x00, 0x10, 0x01, 0x20, 0x02, 0x30, 0x03, 0x40, 0x04), 131),
    (_r(0x03, 0x0A, 0x00, 0x00, 0x80), 0),
    (_r(0x23, 0x0B, 0x00, 0x00, 0x00, 0x01, 0x00, 0x02, 0xAA), 0),
    (_r(0x05, 0x07, 0x0B, 0x12, 0x03, 0x04, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10), 0),
    (_r(0x06, 0x00, 0x0C, 0x00, 0x01, 0x01, 0xAB), 0),
    (_r(0x16, 0x00, 0x0C, 0x00, 0x01, 0x01, 0xCD), 0),
    (_r(0x07, 0x03, 0x0D, 0x00, 0x02, 0x03, 0xEE), 1),
    (_r(0x08, 0x40, 0x00, 0x40, 0x0F), 0),
    (_r(0x19, 0x00, 0x10, 0x00, 0x13), 0),
    (_r(0x28, 0x00, 0x20, 0x00, 0x2F), 0),
    (_r(0x38, 0x10, 0x00, 0x10, 0xFF, 0x12), 0),
    (_r(0x0A, 0x40, 0x10, 0x40, 0x11, 0x55, 0x66), 0),
    (_r(0x3A, 0x41, 0x00, 0x41, 0x01, 0x77, 0x88), 0),
    (_r(0x78, 0x00, 0x10, 0x00, 0x1F, 0x12, 0x04), 0),
    (_r(0x79, 0x00, 0x00, 0x00, 0x03, 0x00, 0x02), 0),
    (_r(0x7A, 0x00, 0x04, 0x00, 0x05, 0x00, 0x03, 0x00, 0x99, 0x98), 0),
    (_r(0x0B, 0x40, 0x00, 0x40, 0x01, 0x11, 0x22), 0),
    (_r(0x3B, 0x40, 0x00, 0x40, 0x00, 0x33), 0),
    (_r(0x7B, 0x00, 0x02, 0x00, 0x03, 0x10, 0x06, 0x00, 0x44, 0x45), 0),
    (_r(0x0C), 0),
    (_r(0x0D, 0x01, 0xF4), 0),
    (_r(0x0E), 1),
]

# extended records (first byte 0xF0)
EXTENDED: list[tuple[bytes, int]] = [
    (_r(0xF0, 0x01, 0x01), 0),
    (_r(0xF0, 0x03, 0x00), 0),
    (_r(0xF0, 0x05, 0x02), 1),
    (_r(0xF0, 0x0C, 0x01), 0),
    (_r(0xF0, 0x02, 0x00), 0),
    (_r(0xF0, 0x02, 0x01), 0),
    (_r(0xF0, 0x04, 0x00, 0x40, 0x00, 0x40, 0xFF), 0),
    (_r(0xF0, 0x04, 0x01, 0x00, 0x10, 0x00, 0x1F), 0),
    (_r(0xF0, 0x04, 0x02, 0x00, 0x20, 0x00, 0x3F), 0),
    (_r(0xF0, 0x04, 0x03, 0x00, 0x00, 0x00, 0xFF, 0x12), 0),
    (_r(0xF0, 0x04, 0x04, 0x02, 0x0D, 0x12, 0x03, 0x04), 0),
    (_r(0xF0, 0x04, 0x05, 0x02, 0x0D, 0x00, 0x01, 0x01), 0),
    (_r(0xF0, 0x04, 0x06, 0x00, 0x0B, 0x02, 0x0E, 0x21, 0x05, 0x06), 0),
    (_r(0xF0, 0x04, 0x07, 0x00, 0x10, 0x00, 0x2F, 0x10, 0x04), 0),
    (
        _r(
            0xF0,
            0x06,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x05,
            0x00,
            0x00,
            0x00,
            0x07,
        ),
        0,
    ),
    (
        _r(
            0xF0,
            0x06,
            0x03,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x02,
        ),
        0,
    ),
    (_r(0xF0, 0x07, 0x04, 0x03, 0x34), 0),
    (_r(0xF0, 0x07, 0x05, 0x01, 0x35), 0),
    (_r(0xF0, 0x07, 0x06, 0x00, 0x0B, 0x02, 0x36), 0),
    (_r(0xF0, 0x09, 0x04, 0x03, 0x34, 0x02, 0xA1, 0xA2), 0),
    (_r(0xF0, 0x09, 0x06, 0x00, 0x0B, 0x00, 0x36, 0x03, 0x00, 0xB1, 0xB2, 0xB3), 0),
    (_r(0xF0, 0x09, 0x05, 0x00, 0x37, 0x00), 0),
    (_r(0xF0, 0x08, 0x04, 0x03, 0x38), 0),
    (_r(0xF0, 0x08, 0x05, 0x02, 0x39), 0),
    (_r(0xF0, 0x08, 0x06, 0x00, 0x0C, 0x01, 0x3A), 0),
    (_r(0xF0, 0x0D, 0x00, 0x00, 0x01, 0x02), 0),
    (_r(0xF0, 0x0F, 0x04, 0x03, 0x40, 0x02, 0x00, 0x05, 0x32, 0x01), 0),
    (_r(0xF0, 0x0F, 0x05, 0x01, 0x41, 0x11, 0x00, 0x01, 0x10, 0x00), 0),
    (_r(0xF0, 0x0F, 0x06, 0x01, 0x9A, 0x00, 0x42, 0x24, 0x00, 0x0A, 0x31, 0x01), 0),
    (_r(0xF0, 0x0F, 0x06, 0x01, 0x9A, 0x02, 0x43, 0x2F, 0x00, 0x01, 0x00, 0x00), 0),
    (_r(0xF0, 0x10), 0),
    (_r(0xF0, 0x11, 0x00, 0x0B, 0x00), 0),
    (_r(0xF0, 0x12, 0x00, 0x0B, 0x02), 1),
    (_r(0xF0, 0x14, 0x01, 0x9A, 0x00), 0),
    (
        _r(
            0xF0, 0x13, 0x00, 0x0B, 0x00, 0x00, 0x40, 0x00, 0x40, 0x0F, 0x32, 0x01, 0x00
        ),
        0,
    ),
    (
        _r(
            0xF0, 0x13, 0x00, 0x0B, 0x01, 0x01, 0x50, 0x00, 0x50, 0x0F, 0x33, 0x02, 0x01
        ),
        0,
    ),
    (_r(0xF0, 0x13, 0x00, 0x0B, 0x00, 0x02, 0x41, 0x00), 0),
    (_r(0xF0, 0x13, 0x00, 0x0B, 0x00, 0x03, 0x00, 0x01, 0x00, 0x02, 0x00, 0x03), 0),
    (_r(0xF0, 0x13, 0x00, 0x0B, 0x00, 0x04, 0x00, 0x05, 0x06), 0),
    (
        _r(
            0xF0,
            0x13,
            0x00,
            0x0B,
            0x00,
            0x05,
            0x00,
            0x01,
            0x00,
            0x02,
            0x00,
            0x03,
            0x00,
            0x04,
        ),
        0,
    ),
    (_r(0xF0, 0x13, 0x00, 0x0B, 0x00, 0x0A, 0x00, 0xFE), 0),
    (_r(0xF0, 0x13, 0x00, 0x0B, 0x00, 0x0B, 0x00, 0x00, 0x00, 0x10, 0x01, 0xFF), 0),
    (_r(0xF0, 0x15, 0x00, 0x0B, 0x00, 0x05, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10), 0),
    (_r(0xF0, 0x17, 0x00, 0x0B, 0x02, 0x06, 0xA0), 0),
    (_r(0xF0, 0x16, 0x00, 0x0B, 0x00, 0x07, 0x12, 0x03, 0x04, 0xBB), 0),
    (_r(0xF0, 0x18, 0x00, 0x0B, 0x00, 0x08, 0x00, 0x01, 0x01, 0xCC), 0),
    (_r(0xF0, 0x19, 0x00, 0x0B, 0x00, 0x09, 0xDD), 0),
    (_r(0xF0, 0x1A, 0x00, 0x0B, 0x01, 0x0A, 0x00, 0x02, 0x05, 0xEE), 0),
    (_r(0xF0, 0x1B, 0x01, 0x02), 0),
]

# records the converter cannot map: (record or None, proc mask)
INVALID: list[tuple[bytes | None, int]] = [
    (_r(0x00), 0),
    (_r(0x03, 0x06), 0),
    (_r(0x48, 0x40, 0x00, 0x40, 0x01), 0),
    (_r(0x4B, 0x40, 0x00, 0x40, 0x01), 0),
    (_r(0xF0, 0x04, 0x08), 0),
    (_r(0xF0, 0x07, 0x01), 0),
    (_r(0xF0, 0x08, 0x02), 0),
    (_r(0xF0, 0x0A), 0),
    (_r(0xF0, 0x0E), 0),
    (_r(0xF0, 0x0F, 0x03), 0),
    (_r(0xF0, 0x13, 0x00, 0x0B, 0x00, 0x06), 0),
    (_r(0xF0, 0x7F), 0),
    (_r(0x0E), 7),
    (None, 0),
    (bytes(15), 0),
    (_r(0xFF), 0),
]


def standard_tables() -> Tables:
    t = base()
    t["s19_block"] = [
        block(k, rec, proc=proc) for k, (rec, proc) in enumerate(STANDARD, 1)
    ]
    # segment data and mask of the absolute and the relative segment
    t["s19_block"][4]["BLOCK_DATA"] = b"\x01\x02\x03\x04"
    t["s19_block"][4]["BLOCK_MASK"] = b"\x01\x00"
    t["s19_block"][11]["BLOCK_DATA"] = b"\x05\x06"
    t["s19_block"][11]["BLOCK_MASK"] = b"\x00\x02"
    return t


def extended_tables() -> Tables:
    t = base()
    t["s19_block"] = [
        block(k, rec, proc=proc) for k, (rec, proc) in enumerate(EXTENDED, 1)
    ]
    return t


def invalid_tables() -> Tables:
    t = base()
    t["s19_block"] = [
        block(k, rec, proc=proc) for k, (rec, proc) in enumerate(INVALID, 1)
    ]
    # a block whose load record the schema upgrade cannot build stays without record
    t["s19_block"][13].update({"CONTROL_CODE": 0x83, "SEGMENT_TYPE": 6})
    return t


def merged_tables() -> Tables:
    t = base(0x20, PROGRAM_STYLE=0)
    t["s19_block"] = [
        block(1, _r(0x0F), merge=2),
        block(2, _r(0x21), merge=2),
        block(3, _r(0xF0, 0x0B, 0x02), merge=1, proc=1),
        block(4, _r(0x0E), merge=1),
    ]
    return t


def _program(tables: Tables) -> tuple[ET.Element, list[str], list[str]]:
    files, warnings, errors = run(tables)
    return (
        find(xml(files, f"M-0083/{APP}.xml"), ".//{}ApplicationProgram"),
        warnings,
        errors,
    )


def _procedures(app: ET.Element) -> list[list[tuple[str, dict[str, str]]]]:
    return [children(p) for p in findall(app, ".//{}LoadProcedures/{}LoadProcedure")]


def test_standard_records() -> None:
    app, warnings, errors = _program(standard_tables())
    assert (warnings, errors) == ([], [])
    assert _procedures(app) == [
        [
            ("LdCtrlDisconnect", {}),
            ("LdCtrlLoad", {"LsmIdx": "2"}),
            ("LdCtrlLoadCompleted", {"AppliesTo": "par", "LsmIdx": "0"}),
            ("LdCtrlUnload", {"AppliesTo": "grp", "LsmIdx": "1"}),
            (
                "LdCtrlAbsSegment",
                {
                    "LsmIdx": "0",
                    "SegType": "0",
                    "Address": "16384",
                    "Size": "16",
                    "Access": "50",
                    "MemType": "1",
                    "SegFlags": "0",
                },
            ),
            (
                "LdCtrlAbsSegment",
                {
                    "AppliesTo": "par,grp",
                    "LsmIdx": "1",
                    "SegType": "1",
                    "Address": "96",
                    "Size": "1",
                    "Access": "15",
                    "MemType": "3",
                    "SegFlags": "18",
                },
            ),
            (
                "LdCtrlTaskSegment",
                {"AppliesTo": "full", "LsmIdx": "0", "Address": "16675"},
            ),
            (
                "LdCtrlTaskPtr",
                {
                    "AppliesTo": "full,par",
                    "LsmIdx": "0",
                    "InitPtr": "258",
                    "SavePtr": "772",
                    "SerialPtr": "1286",
                },
            ),
            (
                "LdCtrlTaskCtrl1",
                {
                    "AppliesTo": "full,grp",
                    "LsmIdx": "0",
                    "Address": "4660",
                    "Count": "7",
                },
            ),
            (
                "LdCtrlTaskCtrl2",
                {
                    "AppliesTo": "all",
                    "LsmIdx": "0",
                    "Callback": "4097",
                    "Address": "8194",
                    "Seg0": "12291",
                    "Seg1": "16388",
                },
            ),
            ("LdCtrlMaxLength", {"LsmIdx": "0", "Size": "128"}),
            (
                "LdCtrlRelSegment",
                {"LsmIdx": "2", "Size": "256", "Mode": "2", "Fill": "170"},
            ),
            (
                "LdCtrlWriteProp",
                {
                    "ObjIdx": "7",
                    "PropId": "11",
                    "StartElement": "515",
                    "Count": "260",
                    "Verify": "false",
                    "InlineData": "0102030405060708090A",
                },
            ),
            (
                "LdCtrlWriteProp",
                {
                    "ObjIdx": "0",
                    "PropId": "12",
                    "Verify": "true",
                    "InlineData": "AB000000000000000000",
                },
            ),
            # only the record 06 itself verifies, not 16 (another load state machine)
            (
                "LdCtrlWriteProp",
                {
                    "ObjIdx": "0",
                    "PropId": "12",
                    "Verify": "false",
                    "InlineData": "CD000000000000000000",
                },
            ),
            (
                "LdCtrlCompareProp",
                {
                    "AppliesTo": "par",
                    "InlineData": "EE000000000000000000",
                    "ObjIdx": "3",
                    "PropId": "13",
                    "StartElement": "2",
                    "Count": "3",
                },
            ),
            ("LdCtrlWriteMem", {"Address": "16384", "Size": "16", "Verify": "false"}),
            (
                "LdCtrlWriteMem",
                {
                    "AddressSpace": "LcSlave",
                    "Address": "16",
                    "Size": "4",
                    "Verify": "true",
                },
            ),
            (
                "LdCtrlWriteMem",
                {
                    "AddressSpace": "LcFilter",
                    "Address": "32",
                    "Size": "16",
                    "Verify": "false",
                },
            ),
            (
                "LdCtrlWriteMem",
                {
                    "AddressSpace": "User",
                    "Address": "69632",
                    "Size": "65792",
                    "Verify": "false",
                },
            ),
            (
                "LdCtrlWriteMem",
                {
                    "Address": "16400",
                    "Size": "2",
                    "Verify": "false",
                    "InlineData": "5566000000000000000000",
                },
            ),
            (
                "LdCtrlWriteMem",
                {
                    "AddressSpace": "User",
                    "Address": "16640",
                    "Size": "2",
                    "Verify": "false",
                    "InlineData": "7788000000000000000000",
                },
            ),
            (
                "LdCtrlWriteRelMem",
                {"ObjIdx": "4", "Offset": "65552", "Size": "65552", "Verify": "false"},
            ),
            (
                "LdCtrlWriteRelMem",
                {"ObjIdx": "2", "Offset": "0", "Size": "4", "Verify": "true"},
            ),
            (
                "LdCtrlWriteRelMem",
                {
                    "ObjIdx": "3",
                    "Offset": "4",
                    "Size": "2",
                    "Verify": "false",
                    "InlineData": "9998000000000000",
                },
            ),
            (
                "LdCtrlCompareMem",
                {
                    "InlineData": "1122000000000000000000",
                    "Address": "16384",
                    "Size": "2",
                },
            ),
            (
                "LdCtrlCompareMem",
                {
                    "InlineData": "3300000000000000000000",
                    "AddressSpace": "User",
                    "Address": "16384",
                    "Size": "1",
                },
            ),
            (
                "LdCtrlCompareRelMem",
                {
                    "InlineData": "4445000000000000",
                    "ObjIdx": "6",
                    "Offset": "65538",
                    "Size": "4294901762",
                },
            ),
            ("LdCtrlRestart", {}),
            ("LdCtrlDelay", {"MilliSeconds": "500"}),
            ("LdCtrlConnect", {"AppliesTo": "par"}),
        ]
    ]
    assert app.get("LoadProcedureStyle") == "ProductProcedure"
    assert "DownloadInfoIncomplete" not in app.attrib


def test_segments_of_the_load_records() -> None:
    app, _warnings, _errors = _program(standard_tables())
    code = find(app, ".//{}Code")
    assert children(code) == [
        ("AbsoluteSegment", {"Id": f"{APP}_AS-4000", "Address": "16384", "Size": "16"}),
        ("AbsoluteSegment", {"Id": f"{APP}_AS-0060", "Address": "96", "Size": "1"}),
        ("AbsoluteSegment", {"Id": f"{APP}_AS-4100", "Address": "16640", "Size": "5"}),
        ("AbsoluteSegment", {"Id": f"{APP}_AS-4200", "Address": "16896", "Size": "3"}),
        (
            "RelativeSegment",
            {
                "Id": f"{APP}_RS-02-00000",
                "Offset": "0",
                "Size": "256",
                "LoadStateMachine": "2",
            },
        ),
    ]
    # the mask is 0xFF per set byte, padded to the data length
    assert [(tag(c), c.text) for c in code[0]] == [
        ("Data", "AQIDBA=="),
        ("Mask", "/wAAAA=="),
    ]
    assert [(tag(c), c.text) for c in code[4]] == [("Data", "BQY="), ("Mask", "AP8=")]


EXTENDED_EXPECTED: list[tuple[str, dict[str, str]]] = [
    ("LdCtrlSetControlVariable", {"Name": "EnableSegmentWrite", "Value": "true"}),
    (
        "LdCtrlSetControlVariable",
        {"Name": "EnableVerifyOnWriteDirect", "Value": "false"},
    ),
    (
        "LdCtrlSetControlVariable",
        {"AppliesTo": "par", "Name": "EnableOptimisticWrite", "Value": "true"},
    ),
    ("LdCtrlSetControlVariable", {"Name": "EnableMemoryAutoVerify", "Value": "true"}),
    ("LdCtrlClearLCFilterTable", {}),
    ("LdCtrlClearLCFilterTable", {"UseFunctionProp": "true"}),
    ("LdCtrlLoadImageMem", {"Address": "16384", "Size": "256"}),
    ("LdCtrlLoadImageMem", {"AddressSpace": "LcSlave", "Address": "16", "Size": "16"}),
    ("LdCtrlLoadImageMem", {"AddressSpace": "LcFilter", "Address": "32", "Size": "32"}),
    (
        "LdCtrlLoadImageMem",
        {"AddressSpace": "User", "Address": "65536", "Size": "65792"},
    ),
    (
        "LdCtrlLoadImageProp",
        {"ObjIdx": "2", "PropId": "13", "Count": "260", "StartElement": "515"},
    ),
    ("LdCtrlLoadImageProp", {"ObjIdx": "7", "PropId": "13"}),
    (
        "LdCtrlLoadImageProp",
        {
            "ObjType": "11",
            "Occurrence": "2",
            "PropId": "14",
            "Count": "518",
            "StartElement": "261",
        },
    ),
    ("LdCtrlLoadImageRelMem", {"ObjIdx": "4", "Offset": "65552", "Size": "4294901792"}),
    ("LdCtrlMapError", {"OriginalError": "5", "MappedError": "7"}),
    (
        "LdCtrlMapError",
        {"LdCtrlFilter": "3", "OriginalError": "256", "MappedError": "2"},
    ),
    ("LdCtrlInvokeFunctionProp", {"ObjIdx": "3", "PropId": "52"}),
    ("LdCtrlInvokeFunctionProp", {"ObjIdx": "6", "PropId": "53"}),
    ("LdCtrlInvokeFunctionProp", {"ObjType": "11", "Occurrence": "2", "PropId": "54"}),
    ("LdCtrlInvokeFunctionProp", {"ObjIdx": "3", "PropId": "52", "InlineData": "A1A2"}),
    (
        "LdCtrlInvokeFunctionProp",
        {"ObjType": "11", "PropId": "54", "InlineData": "00B1B2"},
    ),
    ("LdCtrlInvokeFunctionProp", {"ObjIdx": "5", "PropId": "55"}),
    ("LdCtrlReadFunctionProp", {"ObjIdx": "3", "PropId": "56"}),
    ("LdCtrlReadFunctionProp", {"ObjIdx": "7", "PropId": "57"}),
    ("LdCtrlReadFunctionProp", {"ObjType": "12", "Occurrence": "1", "PropId": "58"}),
    ("LdCtrlProgressText", {}),
    (
        "LdCtrlDeclarePropDesc",
        {
            "ObjIdx": "3",
            "PropId": "64",
            "PropType": "PDT_UNSIGNED_CHAR",
            "MaxElements": "5",
            "ReadAccess": "3",
            "WriteAccess": "2",
            "Writable": "true",
        },
    ),
    (
        "LdCtrlDeclarePropDesc",
        {
            "ObjIdx": "6",
            "PropId": "65",
            "PropType": "PDT_GENERIC_01",
            "MaxElements": "1",
            "ReadAccess": "1",
            "WriteAccess": "0",
            "Writable": "false",
        },
    ),
    (
        "LdCtrlDeclarePropDesc",
        {
            "ObjType": "410",
            "PropId": "66",
            "PropType": "PDT_GENERIC_20",
            "MaxElements": "10",
            "ReadAccess": "3",
            "WriteAccess": "1",
            "Writable": "true",
        },
    ),
    (
        "LdCtrlDeclarePropDesc",
        {
            "ObjType": "410",
            "Occurrence": "2",
            "PropId": "67",
            "PropType": "PDT_UTF-8",
            "MaxElements": "1",
            "ReadAccess": "0",
            "WriteAccess": "0",
            "Writable": "false",
        },
    ),
    ("LdCtrlClearCachedObjectTypes", {}),
    ("LdCtrlLoad", {"ObjType": "11"}),
    ("LdCtrlLoadCompleted", {"AppliesTo": "par", "ObjType": "11", "Occurrence": "2"}),
    ("LdCtrlUnload", {"ObjType": "410"}),
    (
        "LdCtrlAbsSegment",
        {
            "ObjType": "11",
            "SegType": "0",
            "Address": "16384",
            "Size": "16",
            "Access": "50",
            "MemType": "1",
            "SegFlags": "0",
        },
    ),
    (
        "LdCtrlAbsSegment",
        {
            "ObjType": "11",
            "Occurrence": "1",
            "SegType": "1",
            "Address": "20480",
            "Size": "16",
            "Access": "51",
            "MemType": "2",
            "SegFlags": "1",
        },
    ),
    ("LdCtrlTaskSegment", {"ObjType": "11", "Address": "16640"}),
    (
        "LdCtrlTaskPtr",
        {"ObjType": "11", "InitPtr": "1", "SavePtr": "2", "SerialPtr": "3"},
    ),
    ("LdCtrlTaskCtrl1", {"ObjType": "11", "Address": "5", "Count": "6"}),
    (
        "LdCtrlTaskCtrl2",
        {"ObjType": "11", "Callback": "1", "Address": "2", "Seg0": "3", "Seg1": "4"},
    ),
    ("LdCtrlMaxLength", {"ObjType": "11", "Size": "254"}),
    ("LdCtrlRelSegment", {"ObjType": "11", "Size": "16", "Mode": "1", "Fill": "255"}),
    (
        "LdCtrlWriteProp",
        {
            "ObjType": "11",
            "PropId": "5",
            "Verify": "false",
            "InlineData": "0102030405060708090A",
        },
    ),
    (
        "LdCtrlWriteProp",
        {
            "ObjType": "11",
            "Occurrence": "2",
            "PropId": "6",
            "Verify": "true",
            "InlineData": "A0000000000000000000",
        },
    ),
    (
        "LdCtrlWriteProp",
        {
            "ObjType": "11",
            "PropId": "7",
            "StartElement": "515",
            "Count": "260",
            "Verify": "false",
            "InlineData": "BB000000000000",
        },
    ),
    (
        "LdCtrlWriteProp",
        {
            "ObjType": "11",
            "PropId": "8",
            "Verify": "true",
            "InlineData": "CC000000000000",
        },
    ),
    (
        "LdCtrlCompareProp",
        {"InlineData": "DD000000000000000000", "ObjType": "11", "PropId": "9"},
    ),
    (
        "LdCtrlCompareProp",
        {
            "InlineData": "EE000000000000",
            "ObjType": "11",
            "Occurrence": "1",
            "PropId": "10",
            "StartElement": "2",
            "Count": "5",
        },
    ),
    ("LdCtrlMasterReset", {"EraseCode": "1", "ChannelNumber": "2"}),
]

_PROGRESS_TEXT = 25
_UTF8 = 29


def test_extended_records() -> None:
    app, warnings, errors = _program(extended_tables())
    assert (warnings, errors) == ([], [])
    [procedure] = _procedures(app)
    assert procedure == EXTENDED_EXPECTED


def test_progress_text_has_no_text_id() -> None:
    # the converter never marks the text id as set
    app, _warnings, _errors = _program(extended_tables())
    assert _procedures(app)[0][_PROGRESS_TEXT] == ("LdCtrlProgressText", {})
    assert LoadControls(6).process(_r(0xF0, 0x0D, 0x12, 0x34), "auto") is not None


def test_property_type_utf8_xml_name() -> None:
    # property data type 47 is written with its XML name
    app, _warnings, _errors = _program(extended_tables())
    assert _procedures(app)[0][_UTF8][1]["PropType"] == "PDT_UTF-8"


def test_invalid_records() -> None:
    app, warnings, errors = _program(invalid_tables())
    # only the connect with the illegal proc mask (read as "auto") is left
    assert _procedures(app) == [[("LdCtrlConnect", {})]]
    assert app.get("DownloadInfoIncomplete") == "true"
    assert [w.split(":")[0] for w in warnings] == [
        "ApplicationProgramHasIncompleteDownloadInfo"
    ]
    assert [e.removeprefix(f"{APP}: ").split(":")[0] for e in errors] == [
        "S19IllegalLoadControlProperty",
        "S19IllegalLoadControlProperty",
        "S19IllegalLoadControlProperty",
        "S19IllegalLoadControlProperty",
        "S19IllegalLoadControlProperty",
        "S19IllegalLoadControlProperty",
        "S19UnmappedControl",
        "S19IllegalLoadControlProperty",
        "S19IllegalLoadControlCommand",
        "S19IllegalProcMaskValue",
        "S19EntryWithNullRecord",
        "S19RecordIsNot16Bytes",
    ]


def test_merged_procedures() -> None:
    app, _warnings, _errors = _program(merged_tables())
    procedures = findall(app, ".//{}LoadProcedures/{}LoadProcedure")
    assert [p.attrib for p in procedures] == [{"MergeId": "1"}, {"MergeId": "2"}]
    assert _procedures(app) == [
        [("LdCtrlMerge", {"MergeId": "2"}), ("LdCtrlConnect", {})],
        [("LdCtrlDisconnect", {}), ("LdCtrlLoad", {"LsmIdx": "2"})],
    ]
    assert app.get("LoadProcedureStyle") == "MergedProcedure"


def test_proc_types_and_object_index_of_kind_5() -> None:
    lc = LoadControls(6)
    assert [lc.proc_type(v) for v in (0, 1, 2, 3, 128, 129, 130, 131)] == [
        "auto",
        "par",
        "grp",
        "par,grp",
        "full",
        "full,par",
        "full,grp",
        "all",
    ]
    assert lc.errors == []
    # the first application object index is added to object indexes of kind 5 (in a byte)
    el = lc.process(
        _r(0xF0, 0x0F, 0x05, 0xFF, 0x41, 0x30, 0x00, 0x01, 0x10, 0x00), "auto"
    )
    assert el is not None
    assert el.attrs["ObjIdx"] == "5"
    assert el.attrs["PropType"] == "PDT_VERSION"

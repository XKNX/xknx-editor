"""Parameter types, parameters in every address space, unions and parameter refs."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from .test_coverage_helpers import (
    APP,
    Tables,
    base,
    children,
    every,
    find,
    findall,
    param,
    ptype,
    run,
    tag,
    text,
    translations,
    value,
    xml,
)


def types_tables() -> Tables:
    t = base()
    t["parameter_type"] = [
        ptype(100, 1, "t_u8", 8, hint=""),
        ptype(101, 2, "t_s8", 8, minimum=-10, maximum=10, hint="slider"),
        ptype(102, 2, "t_s16", 16, hint="Other"),
        ptype(103, 3, "t_text", 80, description="free text"),
        ptype(104, 4, "t_enum", 2),
        ptype(105, 5, "t_binary", 16),
        ptype(106, 4, "t_sizeless", 0),
        ptype(107, 4, "t_empty_download", 4),
        ptype(108, 4, "t_empty_page", 4),
        ptype(109, 0, "t_void", 8),
        ptype(110, 1, "t_u8", 8, minimum=-1, maximum=-2, description="again"),
        ptype(111, 4, "t_enum", 2),
        ptype(112, None, "t_untyped", None),
        ptype(113, 1, "t_u4", 4, minimum=0, maximum=15),
    ]
    t["parameter_list_of_values"] = [
        value(104, 120, 1, "One", 2),
        value(104, 121, 0, "Zero", None),
        value(105, 122, 1, "Short", 0, binary=b"\x01"),
        value(105, 123, 2, "Long", 1, binary=b"\x01\x02\x03"),
        value(105, 124, None, "Null", 2),
        value(106, 125, 5, "Five", 0),
        value(106, 126, 2, "Two", 1),
        value(111, 127, 1, "One again", 0),
        value(111, 128, 3, "Three", 1),
    ]
    t["parameter"] = [
        param(200, 0, "Page", None, space=0, order=1),
        param(201, 1, "Unsigned", 100, space=2, address=0x4400, bit=0, order=2),
        param(
            202, 2, "Signed", 101, space=2, address=0x4401, bit=0, default=-3, order=3
        ),
        param(203, 3, "Wide", 102, space=2, address=0x4402, bit=0, order=4),
        param(204, 4, "Text", 103, space=2, address=0x4404, bit=0, text="abc", order=5),
        param(205, 5, "Enum", 104, space=2, address=0x4414, bit=0, order=6),
        param(
            206, 6, "Binary", 105, space=2, address=0x4415, bit=0, default=2, order=7
        ),
        param(207, 7, "Sizeless", 106, space=2, address=0x4417, bit=0, order=8),
        param(208, 8, "Empty", 107, space=2, address=0x4418, bit=0, order=9),
        param(209, 9, "Empty page", 108, space=1, order=10),
        param(210, 10, "Void", 109, space=2, address=0x4419, bit=0, order=11),
        param(211, 11, "Untyped", 112, space=1, order=12),
        param(212, None, "No number", 100, space=1, order=13),
        param(213, 13, "No type", None, space=1, order=14),
        param(
            214, 14, "Nibble", 113, space=2, address=0x441A, bit=4, default=20, order=15
        ),
    ]
    t["text_attribute"] = [
        text(1, 1033, 10, 201, "Unsigned EN"),
        text(2, 1031, 10, 201, "Vorzeichenlos"),
        text(3, 1031, 11, 120, "Eins"),
        text(4, 1031, 11, 999, "Orphan"),
    ]
    return t


def spaces_tables() -> Tables:
    t = base()
    t["parameter_type"] = [
        ptype(100, 1, "t_u8", 8),
        ptype(101, 1, "t_u4", 4),
        ptype(102, 1, "t_u16", 16),
    ]
    t["parameter"] = [
        param(200, 0, "Page", None, space=0, order=1),
        # without an address space: the address decides
        param(201, 1, "Implicit none", 100, address=0, bit=0, order=2),
        param(202, 2, "Implicit memory", 100, address=0x4600, bit=0, order=3),
        param(203, 3, "Implicit page", None, order=4),
        param(204, 4, "No bit offset", 100, space=2, address=0x4601, order=5),
        param(205, 5, "User memory", 100, space=3, address=0x10000, bit=0, order=6),
        param(206, 6, "User memory 2", 101, space=3, address=0x10001, bit=4, order=7),
        param(
            207,
            7,
            "Property",
            100,
            space=4,
            object=3,
            property=51,
            address=0,
            bit=0,
            order=8,
        ),
        param(
            208,
            8,
            "Property part",
            101,
            space=4,
            object=3,
            property=51,
            address=0,
            bit=4,
            order=9,
        ),
        param(
            209,
            9,
            "App property",
            100,
            space=5,
            object=1,
            property=52,
            address=1,
            bit=0,
            order=10,
        ),
        param(
            210,
            10,
            "Big property id",
            100,
            space=4,
            object=2,
            property=300,
            address=0,
            bit=0,
            order=11,
        ),
        param(
            211,
            11,
            "Typed property",
            102,
            space=11,
            object=0x9A,
            property=53,
            address=0x02000004,
            bit=0,
            order=12,
        ),
        param(
            212,
            12,
            "Typed property part",
            100,
            space=11,
            object=0x9A,
            property=53,
            address=0x02000005,
            bit=0,
            order=13,
        ),
        param(213, 13, "Relative", 100, space=12, address=4, bit=0, order=14),
        param(214, 14, "Relative 2", 102, space=12, address=6, bit=0, order=15),
        param(215, 15, "Relative far", 100, space=12, address=40, bit=0, order=16),
        param(216, 16, "Unknown space", 100, space=7, address=1, bit=0, order=17),
        param(
            217, 17, "Read only", 100, space=2, address=0x4602, bit=0, high=1, order=18
        ),
        param(218, 18, "Hidden", 100, space=2, address=0x4603, bit=0, high=0, order=19),
        param(
            219,
            19,
            "No access",
            100,
            space=2,
            address=0x4604,
            bit=0,
            high=None,
            order=20,
        ),
        param(
            220,
            20,
            "Patched",
            100,
            space=2,
            address=0x4605,
            bit=0,
            default=7,
            patch=1,
            order=21,
        ),
        # the same memory and type as 202: one parameter, a second ref with its own values
        param(
            221,
            21,
            "Alias",
            100,
            space=2,
            address=0x4600,
            bit=0,
            default=9,
            high=1,
            order=22,
        ),
        param(222, 22, "Far memory", 100, space=2, address=0x4700, bit=0, order=23),
    ]
    return t


def _app(tables: Tables) -> tuple[ET.Element, list[str], list[str]]:
    files, warnings, errors = run(tables)
    root = xml(files, f"M-0083/{APP}.xml")
    return root, warnings, errors


Item = tuple[str, dict[str, str], list[tuple[str, dict[str, str]]]]


def _parameters(root: ET.Element) -> list[Item]:
    return [
        (tag(p), dict(p.attrib), children(p))
        for p in find(root, ".//{}Static/{}Parameters")
    ]


def _types(root: ET.Element) -> dict[str, tuple[dict[str, str], list[Item]]]:
    out: dict[str, tuple[dict[str, str], list[Item]]] = {}
    for pt in every(root, "ParameterType"):
        out[pt.get("Name") or ""] = (
            dict(pt.attrib),
            [(tag(c), dict(c.attrib), children(c)) for c in pt],
        )
    return out


def _number(size: int, kind: str, low: int, high: int, **extra: str) -> list[Item]:
    attrs = {
        "SizeInBit": str(size),
        "Type": kind,
        "minInclusive": str(low),
        "maxInclusive": str(high),
    }
    return [("TypeNumber", attrs | extra, [])]


def _memory(
    offset: int, bit: int = 0, segment: str = "AS-4000"
) -> list[tuple[str, dict[str, str]]]:
    return [
        (
            "Memory",
            {
                "CodeSegment": f"{APP}_{segment}",
                "Offset": str(offset),
                "BitOffset": str(bit),
            },
        )
    ]


def _p(
    number: int,
    name: str,
    type_name: str,
    value: str,
    item: list[tuple[str, dict[str, str]]] | None = None,
    **extra: str,
) -> Item:
    attrs = {
        "Id": f"{APP}_P-{number}",
        "Name": name,
        "ParameterType": f"{APP}_PT-{type_name}",
        "Text": extra.pop("text", f"{name} text"),
    }
    attrs.update(extra)
    attrs["Value"] = value
    return ("Parameter", attrs, item or [])


def test_parameter_types() -> None:
    root, warnings, errors = _app(types_tables())
    types = _types(root)
    assert list(types) == [
        "t_u8",
        "t_s8",
        "t_s16",
        "t_text",
        "t_enum",
        "t_binary",
        "t_sizeless",
        "t_empty_download",
        "t_empty_page",
        "t_void",
        "t_untyped",
        "t_u4",
        "_dummy",
    ]
    # a second row of a type name redefines the type (minimum -1 read as unsigned)
    assert types["t_u8"][1] == _number(8, "unsignedInt", 4294967295, 4294967294)
    assert types["t_s8"][1] == _number(8, "signedInt", -10, 10, UIHint="Slider")
    assert types["t_s16"][1] == _number(16, "signedInt", -32768, 32767)
    assert types["t_text"] == (
        {
            "Id": f"{APP}_PT-t.5Ftext",
            "Name": "t_text",
            "InternalDescription": "free text",
        },
        [("TypeText", {"SizeInBit": "80"}, [])],
    )
    enum = f"{APP}_PT-t.5Fenum"
    # the enumerations of the second definition replace those of the same value
    assert types["t_enum"][1] == [
        (
            "TypeRestriction",
            {"Base": "Value", "SizeInBit": "2"},
            [
                ("Enumeration", {"Text": "Zero", "Value": "0", "Id": f"{enum}_EN-0"}),
                (
                    "Enumeration",
                    {
                        "Text": "One again",
                        "Value": "1",
                        "Id": f"{enum}_EN-1",
                        "DisplayOrder": "0",
                    },
                ),
                (
                    "Enumeration",
                    {
                        "Text": "Three",
                        "Value": "3",
                        "Id": f"{enum}_EN-3",
                        "DisplayOrder": "1",
                    },
                ),
            ],
        )
    ]
    binary = f"{APP}_PT-t.5Fbinary"
    # binary values are cut or padded to the type size
    assert types["t_binary"][1] == [
        (
            "TypeRestriction",
            {"Base": "BinaryValue", "SizeInBit": "16"},
            [
                (
                    "Enumeration",
                    {
                        "Text": "Short",
                        "Value": "1",
                        "Id": f"{binary}_EN-1",
                        "DisplayOrder": "0",
                        "BinaryValue": "AQA=",
                    },
                ),
                (
                    "Enumeration",
                    {
                        "Text": "Long",
                        "Value": "2",
                        "Id": f"{binary}_EN-2",
                        "DisplayOrder": "1",
                        "BinaryValue": "AQI=",
                    },
                ),
                (
                    "Enumeration",
                    {
                        "Text": "Null",
                        "Value": "0",
                        "Id": f"{binary}_EN-0",
                        "DisplayOrder": "2",
                        "BinaryValue": "AAA=",
                    },
                ),
            ],
        )
    ]
    # a restriction without size gets the bit length of its highest value
    assert types["t_sizeless"][1][0][1] == {"Base": "Value", "SizeInBit": "3"}
    assert types["t_empty_download"][1] == [
        ("TypeRestriction", {"Base": "Value", "SizeInBit": "4"}, [])
    ]
    for name in ("t_empty_page", "t_void", "t_untyped", "_dummy"):
        assert types[name][1] == [("TypeNone", {}, [])]
    app = find(root, ".//{}ApplicationProgram")
    assert app.get("Broken") == "true"
    assert [w.split(":")[0] for w in warnings] == [
        "UnexpectedDownloadParameterTypeSize"
    ]
    assert [e.split(":")[0] for e in errors] == [
        "DownloadParameterWithEmptyParameterTypeRestriction"
    ]


def test_parameters_of_the_types() -> None:
    root, _warnings, _errors = _app(types_tables())
    parameters = [p for p in _parameters(root) if p[1]["Id"] != f"{APP}_P-10"]
    assert parameters == [
        _p(0, "Page", ".5Fdummy", ""),
        # the first free number for a parameter without number
        _p(12, "No number", "t.5Fu8", "4294967295"),
        _p(9, "Empty page", "t.5Fempty.5Fpage", ""),
        _p(11, "Untyped", "t.5Funtyped", ""),
        _p(13, "No type", ".5Fdummy", ""),
        _p(1, "Unsigned", "t.5Fu8", "4294967295", _memory(1024), text="Unsigned EN"),
        _p(2, "Signed", "t.5Fs8", "-3", _memory(1025)),
        _p(3, "Wide", "t.5Fs16", "-32768", _memory(1026)),
        _p(4, "Text", "t.5Ftext", "abc", _memory(1028)),
        _p(5, "Enum", "t.5Fenum", "0", _memory(1044)),
        _p(6, "Binary", "t.5Fbinary", "2", _memory(1045)),
        _p(7, "Sizeless", "t.5Fsizeless", "5", _memory(1047)),
        _p(8, "Empty", "t.5Fempty.5Fdownload", "", _memory(1048)),
        _p(14, "Nibble", "t.5Fu4", "20", _memory(1050, 4)),
    ]
    assert translations(root, "de-DE") == {
        f"{APP}_PT-t.5Fenum_EN-1": {"Text": "Eins"},
        f"{APP}_P-1": {"Text": "Vorzeichenlos"},
    }
    assert translations(root, "en-US") == {f"{APP}_P-1": {"Text": "Unsigned EN"}}


def test_memory_parameter_of_void_type_keeps_its_type() -> None:
    # outside the exception list the converter only warns
    root, warnings, _errors = _app(types_tables())
    void = next(p for p in _parameters(root) if p[1]["Id"] == f"{APP}_P-10")
    assert void == _p(10, "Void", "t.5Fvoid", "", _memory(1049))
    assert "DummyParameterTypeNumber8" not in _types(root)
    assert warnings == [
        "UnexpectedDownloadParameterTypeSize: Param # 10 `Void' has address and void type"
        " of size 8"
    ]


def _siemens(tables: Tables, device_type: int) -> Tables:
    """``tables`` as a program of manufacturer M-0001 with ``device_type``."""
    for rows in tables.values():
        for r in rows:
            for column in ("MANUFACTURER_ID", "manufacturer_id"):
                if r.get(column) == 131:
                    r[column] = 1
    tables["manufacturer"][0]["MANUFACTURER_NAME"] = "Siemens"
    # (the converter reads the program version of M-0001 as an integer)
    tables["application_program"][0].update(
        DEVICE_TYPE=device_type, PROGRAM_VERSION="1"
    )
    return tables


def _void_tables(device_type: int, name: str) -> Tables:
    t = _siemens(base(), device_type)
    t["parameter_type"] = [
        ptype(100, 1, "t_u8", 8),
        ptype(101, 0, "t_void", 8),
        # a second sized definition of the same void type
        ptype(102, 0, "t_void", 16),
    ]
    t["parameter"] = [
        param(200, 0, "Page", None, space=0, order=1),
        param(201, 1, name, 101, space=2, address=0x4400, bit=0, order=2),
        param(202, 2, "Unsigned", 100, space=2, address=0x4402, bit=0, order=3),
    ]
    return t


@pytest.mark.parametrize(
    ("device_type", "name", "expected"),
    [
        # in the exception list: the last sized definition decides the replacement
        (32778, "Text_", "DummyParameterTypeNumber16"),
        (32515, "Value", "DummyParameterTypeNumber16"),
        # this program's "Text_" parameter gets a type without value and no memory
        (32515, "Text_", "DummyParameterTypeNone"),
    ],
)
def test_void_type_of_the_exception_list(
    device_type: int, name: str, expected: str
) -> None:
    files, warnings, _errors = run(_void_tables(device_type, name))
    app = f"M-0001_A-{device_type:04X}-01-0000"
    root = xml(files, f"M-0001/{app}.xml")
    assert warnings == []
    [void] = [p for p in _parameters(root) if p[1]["Id"] == f"{app}_P-1"]
    types = _types(root)
    assert void[1]["ParameterType"] == types[expected][0]["Id"]
    if expected == "DummyParameterTypeNone":
        assert void[2] == []
        assert "DummyParameterTypeNumber8" not in types
    else:
        assert void[1]["Value"] == "-32768"
        assert void[2][0][0] == "Memory"
        # both definitions created their replacement type
        assert "DummyParameterTypeNumber8" in types


@pytest.mark.parametrize(
    ("version", "converted"), [("1.2", False), (" 3 ", True), ("+1", True)]
)
def test_program_version_of_m0001_is_an_integer(version: str, converted: bool) -> None:
    t = _siemens(base(), 0x1234)
    t["application_program"][0]["PROGRAM_VERSION"] = version
    files, _warnings, errors = run(t)
    assert any("_A-1234-" in name for name in files) == converted
    assert any(e.startswith("ApplicationProgramConversionFailed") for e in errors) != (
        converted
    )


def test_second_type_definition_keeps_the_description() -> None:
    root, _warnings, _errors = _app(types_tables())
    assert _types(root)["t_u8"][0] == {"Id": f"{APP}_PT-t.5Fu8", "Name": "t_u8"}


def _property(**attrs: str) -> list[tuple[str, dict[str, str]]]:
    return [("Property", attrs)]


def _up(
    number: int, name: str, type_name: str, offset: int, bit: int, **extra: str
) -> tuple[str, dict[str, str]]:
    attrs = {
        "Id": f"{APP}_UP-{number}",
        "Name": name,
        "ParameterType": f"{APP}_PT-{type_name}",
        "Offset": str(offset),
        "BitOffset": str(bit),
        "Text": f"{name} text",
        "Value": "0",
    }
    attrs.update(extra)
    return ("Parameter", attrs)


def test_parameter_spaces() -> None:
    root, warnings, errors = _app(spaces_tables())
    u8 = "t.5Fu8"
    assert _parameters(root) == [
        _p(3, "Implicit page", ".5Fdummy", ""),
        _p(1, "Implicit none", u8, "0"),
        _p(2, "Implicit memory", u8, "0", _memory(1536)),
        _p(0, "Page", ".5Fdummy", ""),
        _p(4, "No bit offset", u8, "0", _memory(1537)),
        _p(17, "Read only", u8, "0", _memory(1538), Access="Read"),
        _p(18, "Hidden", u8, "0", _memory(1539), Access="None"),
        _p(19, "No access", u8, "0", _memory(1540), Access="None"),
        (
            "Parameter",
            {"LegacyPatchAlways": "true", **_p(20, "Patched", u8, "7")[1]},
            _memory(1541),
        ),
        _p(22, "Far memory", u8, "0", _memory(1792)),
        _p(5, "User memory", u8, "0", _memory(0, 0, "AS-U10000")),
        _p(6, "User memory 2", "t.5Fu4", "0", _memory(1, 4, "AS-U10000")),
        # the property id is a byte
        _p(
            10,
            "Big property id",
            u8,
            "0",
            _property(ObjectIndex="2", PropertyId="44", Offset="0", BitOffset="0"),
        ),
        (
            "Union",
            {"SizeInBit": "8"},
            [
                (
                    "Property",
                    {
                        "ObjectIndex": "3",
                        "PropertyId": "51",
                        "Offset": "0",
                        "BitOffset": "0",
                    },
                ),
                _up(7, "Property", u8, 0, 0, DefaultUnionParameter="true"),
                _up(8, "Property part", "t.5Fu4", 0, 4),
            ],
        ),
        # space 5 counts objects from the first application object (5 for mask 0701)
        _p(
            9,
            "App property",
            u8,
            "0",
            _property(ObjectIndex="6", PropertyId="52", Offset="1", BitOffset="0"),
        ),
        _p(16, "Unknown space", u8, "0"),
        (
            "Union",
            {"SizeInBit": "16"},
            [
                (
                    "Property",
                    {
                        "ObjectType": "154",
                        "Occurrence": "2",
                        "PropertyId": "53",
                        "Offset": "4",
                        "BitOffset": "0",
                    },
                ),
                _up(
                    11, "Typed property", "t.5Fu16", 0, 0, DefaultUnionParameter="true"
                ),
                _up(12, "Typed property part", u8, 1, 0),
            ],
        ),
        _p(13, "Relative", u8, "0", _memory(0, 0, "RS-00-00004")),
        _p(14, "Relative 2", "t.5Fu16", "0", _memory(2, 0, "RS-00-00004")),
        _p(15, "Relative far", u8, "0", _memory(0, 0, "RS-00-00028")),
    ]
    assert children(find(root, ".//{}Code")) == [
        (
            "AbsoluteSegment",
            {"Id": f"{APP}_AS-4000", "Address": "16384", "Size": "30208"},
        ),
        (
            "AbsoluteSegment",
            {
                "Id": f"{APP}_AS-U10000",
                "Address": "65536",
                "Size": "2",
                "UserMemory": "true",
            },
        ),
        (
            "RelativeSegment",
            {
                "Id": f"{APP}_RS-00-00004",
                "Offset": "4",
                "Size": "5",
                "LoadStateMachine": "0",
            },
        ),
        (
            "RelativeSegment",
            {
                "Id": f"{APP}_RS-00-00028",
                "Offset": "40",
                "Size": "1",
                "LoadStateMachine": "0",
            },
        ),
    ]
    refs = {r.get("Id"): dict(r.attrib) for r in every(root, "ParameterRef")}
    assert refs[f"{APP}_P-2_R-21"] == {
        "Id": f"{APP}_P-2_R-21",
        "RefId": f"{APP}_P-2",
        "Name": "Alias",
        "Text": "Alias text",
        "Tag": "21",
        "DisplayOrder": "22",
        "Access": "Read",
        "Value": "9",
    }
    assert refs[f"{APP}_UP-7_R-7"]["RefId"] == f"{APP}_UP-7"
    assert [w.split(":")[0] for w in warnings] == [
        "MemoryParameterHasNoBitOffsetInformation"
    ]
    assert [e.split(":")[0] for e in errors] == ["EibPropertyIdOutOfRange"]
    # the parameters after a page belong to it, by display order
    blocks = findall(root, ".//{}Channel/{}ParameterBlock")
    assert [(b.get("Id"), len(b)) for b in blocks] == [
        (f"{APP}_PB-0", 2),
        (f"{APP}_PB-3", 19),
    ]

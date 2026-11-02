"""Plugins and baggages the converter treats specially: the optimized download plugins (Hager),
the PowerTool version check, baggages of another manufacturer, programs that fail."""

from __future__ import annotations

import base64
import hashlib
import io
import random
import zipfile
import zlib
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from xknxeditor.prod.loader import load
from xknxeditor.prod.vd import application
from xknxeditor.prod.vd.convert import convert_with_report
from xknxeditor.prod.vd.deflate import deflate
from xknxeditor.prod.vd.optimized import ex_factory_image, replace_load_controls
from xknxeditor.prod.vd.signing import sign

from .test_coverage_helpers import (
    Tables,
    base,
    block,
    children,
    db,
    every,
    find,
    findall,
    program,
    run,
    xml,
)
from .test_coverage_programs import abb_tables

# a complete master data file (the loader validates it), from a package in the repository
_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "gira_2gang_button_interface.knxprod"
)


def _bag(
    ident: int, folder: str, name: str, data: bytes | None = None, **kw: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ID": ident,
        "MANUFACTURERID": 131,
        "LOCALFILEDIR": folder,
        "LOCALFILENAME": name,
        "LOCALFILETIME": "2014-07-01",
        "DATA": name.encode() if data is None else data,
    }
    row.update({k.upper(): v for k, v in kw.items()})
    return row


def _uses(*pairs: tuple[int, int]) -> list[dict[str, Any]]:
    return [
        {"ID": i, "APPLICATIONPROGRAMID": p, "BAGGAGEID": b}
        for i, (p, b) in enumerate(pairs, start=1)
    ]


def _h2p(ident: int, program_id: int) -> list[dict[str, Any]]:
    return [
        {
            "PROD2PROG_ID": ident,
            "PRODUCT_ID": 20,
            "PROGRAM_ID": program_id,
            "PEI_PROGRAM_ID": None,
            "PROD2PROG_STATUS_CODE": 10,
            "REGISTRATION_NUMBER": 77,
            "REGISTRATION_YEAR": 110,
            "REGISTRATION_DATE": "2010-05-04",
        }
    ]


def _invoke(
    number: int, program_id: int, obj: int, data: int, proc: int
) -> dict[str, Any]:
    """An extended load record calling function property 52 of object ``obj`` with one byte."""
    record = bytes([0xF0, 0x09, 0x04, obj, 52, 1, data]).ljust(16, b"\0")
    row = block(number, record, proc=proc)
    row["PROGRAM_ID"] = program_id
    row["BLOCK_ID"] = 1000 + program_id * 10 + number
    return row


def default_data(*segments: str, parameter_crc: str = "1A2B") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        f'<DefaultData ParameterCRC="{parameter_crc}" ObjectTableCRC="3C4D">'
        + "".join(segments)
        + "</DefaultData>"
    ).encode()


IMAGE_DATA = default_data(
    '<Segment AddressSpace="StandardMemory" Offset="4400"><Data>01 02 03 FF</Data></Segment>',
    '<Segment AddressSpace="RelativeMemory" ObjectIdx="4" Offset="10">'
    "<Data>00 11 22</Data></Segment>",
    '<Segment AddressSpace="AppProperty" ObjectIdx="3" PropertyId="51">'
    "<Data>AB CD</Data></Segment>",
)


def memory(size: int, seed: int) -> bytes:
    """Memory contents: runs of a few values between short random stretches."""
    rnd = random.Random(seed)
    out = bytearray()
    while len(out) < size:
        if rnd.random() < 0.5:
            out += bytes([rnd.randrange(8)]) * rnd.randrange(1, 300)
        else:
            out += rnd.randbytes(rnd.randrange(1, 30))
    return bytes(out[:size])


def large_image_data() -> bytes:
    """A segment of 100000 bytes: the compressor ends its first block early."""
    values = " ".join(f"{b:02X}" for b in memory(100000, 3))
    return default_data(
        f'<Segment AddressSpace="StandardMemory" Offset="0x4000"><Data>{values}</Data>'
        "</Segment>"
    )


HAGER = "{000B8A16-FD82-4319-88A9-29EBBAF186DE}"


def hager_tables(data: bytes = IMAGE_DATA) -> Tables:
    """Programs with the optimized download plugin: 0x1234 (mask 0701) and 0x1235 (07B0) with
    their data, 0x1236 (0701) whose first plugin baggage is the installer."""
    t = base()
    t["mask"].append({"MASK_ID": 51, "MASK_VERSION": 0x7B0})
    t["application_program"] += [
        program(program_id=41, device_type=0x1235, mask_id=51),
        program(program_id=42, device_type=0x1236),
    ]
    t["product_to_program"] += _h2p(62, 41) + _h2p(63, 42)
    t["s19_block"] = [
        _invoke(1, 40, 5, 0x04, 130),
        _invoke(2, 40, 5, 0x10, 129),
        # other data or procedure: kept
        _invoke(3, 40, 5, 0x04, 129),
        _invoke(4, 40, 6, 0x10, 129),
        _invoke(1, 41, 6, 0x04, 130),
        _invoke(2, 41, 6, 0x10, 129),
        _invoke(3, 41, 5, 0x10, 129),
        _invoke(1, 42, 5, 0x04, 130),
    ]
    t["program_plugin"] = [
        {"PROGRAM_PLUGIN_ID": i, "PROGRAM_ID": p, "PLUGIN_TYPE": 1, "PLUGIN_CLSID": c}
        for i, (p, c) in enumerate(
            [
                (40, HAGER),
                (41, "{8ef0b7d0-df45-49a4-9e92-001ee739b968}"),
                (42, "{EE07B2F0-8307-4D7E-B51C-7FD982981BA3}"),
            ],
            start=1,
        )
    ]
    t["baggage"] = [
        _bag(900, "OptimizeDL", "Data_1234v01_a.xml", data),
        _bag(901, "OptimizeDL", "Data_1235v01_b.xml", data),
        _bag(902, "OptimizeDownload.Installer", "setup.txt"),
        _bag(903, "docs", "keep.txt"),
    ]
    t["applicationprogrambaggage"] = _uses(
        (40, 900), (40, 902), (40, 903), (41, 901), (42, 902), (42, 900)
    )
    return t


POWER_TOOL = "{2BD47AB3-253C-47F1-A82C-55328D479B09}"


def _version(major: int, minor: int, build: int, revision: int) -> dict[str, int]:
    return {
        "LOCALFILEVERSIONHI": major * 65536 + minor,
        "LOCALFILEVERSIONLO": build * 65536 + revision,
    }


def power_tool_tables() -> Tables:
    """Programs with a PowerTool plugin (or another one) and tool baggages."""
    t = base()
    numbers = [0x1234, 0x1235, 0x1236, 0x1237, 0x1238]
    t["application_program"] = [
        program(program_id=40 + i, device_type=n) for i, n in enumerate(numbers)
    ]
    for i in range(1, len(numbers)):
        t["product_to_program"] += _h2p(60 + i, 40 + i)
    plugins = [
        POWER_TOOL,
        POWER_TOOL,
        "{11111111-2222-3333-4444-555555555555}",
        POWER_TOOL,
        "{86f09bde-5f13-4e08-8b5c-1c6f85adad90}",
    ]
    t["program_plugin"] = [
        {
            "PROGRAM_PLUGIN_ID": i,
            "PROGRAM_ID": 40 + i,
            "PLUGIN_TYPE": 2,
            "PLUGIN_CLSID": c,
        }
        for i, c in enumerate(plugins)
    ]
    t["baggage"] = [
        _bag(900, "toolb", "aetoolb.dll", **_version(1, 1, 53, 9)),
        _bag(901, "ToolB", "BETOOLB.DLL", **_version(1, 1, 54, 0)),
        _bag(902, "other", "aetoolb.dll", **_version(1, 0, 0, 0)),
        _bag(903, "toolb", "cetoolb.dll", **_version(1, 0, 0, 0)),
    ]
    # 0x1234: an old tool; 0x1235: a new one; 0x1236: an old tool, another plugin; 0x1237:
    # other folder or name; 0x1238: the other PowerTool plugin with the old tool
    t["applicationprogrambaggage"] = _uses(
        (40, 900), (41, 901), (42, 900), (43, 902), (43, 903), (44, 900)
    )
    return t


def hager_error_tables(case: str) -> Tables:
    """The optimized download tables with one program the converter cannot handle: another
    mask (``mask``), no plugin baggage (``no_baggage``), data it cannot read (``bad_data``) or
    data of another manufacturer (``foreign_data``)."""
    t = hager_tables()
    if case == "mask":
        t["mask"].append({"MASK_ID": 52, "MASK_VERSION": 0x12})
        t["application_program"][2]["MASK_ID"] = 52
    elif case == "no_baggage":
        t["applicationprogrambaggage"] = [
            u for u in t["applicationprogrambaggage"] if u["APPLICATIONPROGRAMID"] != 41
        ]
    elif case == "bad_data":
        t["baggage"][1]["DATA"] = b"<Other />"
    else:
        t["baggage"][1]["MANUFACTURERID"] = 4
    return t


def _jung(t: Tables) -> Tables:
    for rows in t.values():
        for r in rows:
            for column in ("MANUFACTURER_ID", "MANUFACTURERID"):
                if r.get(column) == 131:
                    r[column] = 4
    return t


def foreign_baggage_tables(case: str) -> Tables:
    """The program M-0004_A-C00A-02-0000-O0001 with baggages of M-0001 (which the converter
    moves to M-0004): ``move`` one used by it alone, ``other`` with another folder of M-0001
    left, ``stay`` one M-0001's program 0x1111 uses too (no folder), ``stay_folder`` the same in
    a folder, ``exists`` one that M-0004 already has, ``nested`` one in a sub folder,
    ``elsewhere`` one of M-0002."""
    t = _jung(base())
    t["application_program"][0].update(
        DEVICE_TYPE=0xC00A, PROGRAM_VERSION="2.1", ORIGINAL_MANUFACTURER_ID=1
    )
    t["application_program"].append(
        program(
            program_id=41, device_type=0x1111, manufacturer_id=1, program_version="1"
        )
    )
    t["manufacturer"].append({"MANUFACTURER_ID": 1, "MANUFACTURER_NAME": "Siemens"})
    t["baggage"] = [
        _bag(900, "docs", "a.dll", MANUFACTURERID=1),
        _bag(901, "other", "b.txt", MANUFACTURERID=1),
        _bag(902, "", "c.txt", MANUFACTURERID=1),
        _bag(903, "docs2", "d.txt", MANUFACTURERID=1),
        _bag(904, "docs", "a.dll", MANUFACTURERID=4, DATA=b"own"),
        _bag(905, "docs\\sub", "e.txt", MANUFACTURERID=1),
        _bag(906, "docs", "f.txt", MANUFACTURERID=2),
    ]
    uses = {
        "move": [(40, 900)],
        "other": [(40, 900), (41, 901)],
        "stay": [(40, 902), (41, 902)],
        "stay_folder": [(40, 903), (41, 903)],
        "exists": [(40, 900)],
        "nested": [(40, 905)],
        "elsewhere": [(40, 906)],
    }[case]
    used = {b for _p, b in uses}
    if case == "exists":
        used.add(904)
    t["baggage"] = [b for b in t["baggage"] if b["ID"] in used]
    t["applicationprogrambaggage"] = _uses(*uses)
    return t


def hager_shared_tables() -> Tables:
    """The optimized download tables with a program of another manufacturer (M-0004) that
    refers to the plugin's data of 0x1234 too."""
    t = hager_tables()
    t["application_program"].append(
        program(program_id=43, device_type=0x2222, manufacturer_id=4)
    )
    t["applicationprogrambaggage"] += _uses((43, 900), (43, 903))
    for i, use in enumerate(t["applicationprogrambaggage"], start=1):
        use["ID"] = i
    return t


def abb_shared_tables() -> Tables:
    """The ABB plugin tables with the program that keeps its plugins (0xA058) moved to M-0008
    (original manufacturer M-0002): it still refers to a baggage the others no longer use."""
    t = abb_tables()
    t["application_program"][1].update(MANUFACTURER_ID=8, ORIGINAL_MANUFACTURER_ID=2)
    return t


# ---- tests (expected values: the output of the converter for these tables)


def _app(files: dict[str, bytes], app_id: str) -> ET.Element:
    return find(xml(files, f"{app_id[:6]}/{app_id}.xml"), ".//{}ApplicationProgram")


def _procedures(app: ET.Element) -> list[list[tuple[str, dict[str, str]]]]:
    return [children(p) for p in findall(app, ".//{}LoadProcedures/{}LoadProcedure")]


def _reset(code: str, applies: str) -> tuple[str, dict[str, str]]:
    return (
        "LdCtrlMasterReset",
        {"AppliesTo": applies, "EraseCode": code, "ChannelNumber": "0"},
    )


def _call(obj: str, data: str) -> tuple[str, dict[str, str]]:
    return (
        "LdCtrlInvokeFunctionProp",
        {"AppliesTo": "full,par", "ObjIdx": obj, "PropId": "52", "InlineData": data},
    )


def _h2ps(files: dict[str, bytes]) -> dict[str, dict[str, str]]:
    root = xml(files, "M-0083/Hardware.xml")
    return {h.get("Id") or "": dict(h.attrib) for h in every(root, "Hardware2Program")}


def test_optimized_download_load_controls() -> None:
    files, _warnings, errors = run(hager_tables())
    assert errors == []
    # the calls of function property 52 for the plugin become master resets; mask 0701
    # calls object 5, mask 07B0 object 6
    assert _procedures(_app(files, "M-0083_A-1234-01-0000")) == [
        [
            _reset("6", "full,grp"),
            _reset("5", "full,par"),
            _call("5", "04"),
            _call("6", "10"),
        ]
    ]
    assert _procedures(_app(files, "M-0083_A-1235-01-0000")) == [
        [_reset("6", "full,grp"), _reset("5", "full,par"), _call("5", "10")]
    ]
    assert _procedures(_app(files, "M-0083_A-1236-01-0000")) == [
        [_reset("6", "full,grp")]
    ]


def test_optimized_download_plugin_and_baggages_go() -> None:
    files, _warnings, _errors = run(hager_tables())
    first = _app(files, "M-0083_A-1234-01-0000")
    assert children(find(first, "{}Static/{}Extension")) == [
        ("Baggage", {"RefId": "M-0083_BG-docs-keep.2Etxt"})
    ]
    for app_id in ("M-0083_A-1235-01-0000", "M-0083_A-1236-01-0000"):
        assert findall(_app(files, app_id), "{}Static/{}Extension") == []
    # no program uses the plugin's baggages anymore
    baggages = xml(files, "M-0083/Baggages.xml")
    assert [b.get("Id") for b in every(baggages, "Baggage")] == [
        "M-0083_BG-docs-keep.2Etxt"
    ]
    assert sorted(n for n in files if "/Baggages/" in n) == [
        "M-0083/Baggages/docs/keep.txt"
    ]


IMAGE_CHECKSUMS = "AQABAAMAAAABAAAABQAAAAAANQAAAAAABAAAABorPE0="
IMAGE = "Y2RgZGBmYACSUODCwMAC4jMyMf/nYQBx2BkEgDREkaASK5htDFbLBBJbfRYA"


def test_optimized_download_data_becomes_the_ex_factory_image() -> None:
    files, _warnings, _errors = run(hager_tables())
    h2ps = _h2ps(files)
    for h2p in ("M-0083_H-D1-2_HP-1234-01-0000", "M-0083_H-D1-2_HP-1235-01-0000"):
        assert h2ps[h2p]["CheckSums"] == IMAGE_CHECKSUMS
        assert h2ps[h2p]["LoadedImage"] == IMAGE
    # the data is looked for next to the program's first plugin baggage (the installer)
    assert "LoadedImage" not in h2ps["M-0083_H-D1-2_HP-1236-01-0000"]


def test_large_ex_factory_image() -> None:
    # (byte for byte as the converter compresses it; zlib ends the first block elsewhere)
    files, _warnings, _errors = run(hager_tables(large_image_data()))
    image = _h2ps(files)["M-0083_H-D1-2_HP-1234-01-0000"]["LoadedImage"]
    assert len(image) == 15588
    assert hashlib.sha256(image.encode()).hexdigest() == (
        "44856ab0287497e372f4df2b97eee46e7349eb252e13cc2c219bf9000faad8f8"
    )


def test_ex_factory_image_layout() -> None:
    checksums, image = ex_factory_image(IMAGE_DATA)
    assert base64.b64encode(checksums).decode() == IMAGE_CHECKSUMS
    assert zlib.decompressobj(-15).decompress(image) == bytes.fromhex(
        "01000100 03000000"
        "01000000 0000 0000 00440000 04000000 01 010203FF"
        "0C000000 0400 0700 10000000 03000000 01 001122"
        "05000000 0300 3300 00000000 02000000 01 ABCD"
    )
    # no CRCs and offsets are 0, hexadecimal numbers may have a prefix
    checksums, image = ex_factory_image(
        b'<DefaultData><Segment AddressSpace="StandardMemory"><Data>0x1F</Data></Segment>'
        b"</DefaultData>"
    )
    assert checksums.endswith(b"\4\0\0\0\0\0\0\0")
    assert zlib.decompressobj(-15).decompress(image)[8:] == bytes.fromhex(
        "01000000 0000 0000 00000000 01000000 01 1F"
    )
    # a root in a namespace is read, segments in it are not
    _checksums, image = ex_factory_image(
        b'<DefaultData xmlns="urn:x"><Segment AddressSpace="StandardMemory"><Data>01</Data>'
        b"</Segment></DefaultData>"
    )
    assert zlib.decompressobj(-15).decompress(image) == bytes.fromhex(
        "01000100 00000000"
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<Other />", "Not the expected XML format"),
        (
            b'<DefaultData><Segment AddressSpace="Eeprom" /></DefaultData>',
            "Unsupported",
        ),
        (b"<DefaultData><Segment /></DefaultData>", "Unsupported AddressSpace"),
        (
            b'<DefaultData><Segment AddressSpace="StandardMemory" /></DefaultData>',
            "Data",
        ),
        (
            b'<DefaultData><Segment AddressSpace="StandardMemory"><Data>01  02</Data>'
            b"</Segment></DefaultData>",
            "hexadecimal",
        ),
        (
            b'<DefaultData><Segment AddressSpace="StandardMemory"><Data>100</Data>'
            b"</Segment></DefaultData>",
            "hexadecimal",
        ),
        (b'<DefaultData ParameterCRC="12345" />', "hexadecimal"),
        (
            b'<DefaultData><Segment AddressSpace="AppProperty" ObjectIdx="x"><Data>01</Data>'
            b"</Segment></DefaultData>",
            "Not a number",
        ),
    ],
)
def test_unreadable_default_data(data: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ex_factory_image(data)


def test_optimized_download_on_another_mask_fails_the_program() -> None:
    files, _warnings, errors = run(hager_error_tables("mask"))
    assert "M-0083/M-0083_A-1236-01-0000.xml" not in files
    assert [e.split(":")[0] for e in errors] == [
        "ApplicationProgramConversionFailed",
        "OptimizedDownloadDataFailed",
    ]
    assert "Unexpected mask version while replacing load controls" in errors[0]
    # the converter stops the whole conversion there; the other programs keep their images
    assert _h2ps(files)["M-0083_H-D1-2_HP-1234-01-0000"]["LoadedImage"] == IMAGE


@pytest.mark.parametrize(
    ("case", "message"),
    [
        (
            "no_baggage",
            "application program extension was not patched M-0083_A-1235-01-0000",
        ),
        ("bad_data", "Not the expected XML format"),
        (
            "foreign_data",
            "Baggage M-0004_BG-OptimizeDL-Data.5F1235v01.5Fb.2Exml cannot be found",
        ),
    ],
)
def test_optimized_download_without_usable_data(case: str, message: str) -> None:
    files, _warnings, errors = run(hager_error_tables(case))
    assert errors == [
        f"OptimizedDownloadDataFailed: no ex-factory image for M-0083_A-1235-01-0000: {message}"
    ]
    h2ps = _h2ps(files)
    assert "LoadedImage" not in h2ps["M-0083_H-D1-2_HP-1235-01-0000"]
    assert h2ps["M-0083_H-D1-2_HP-1234-01-0000"]["LoadedImage"] == IMAGE


def test_failed_program_leaves_a_package_that_loads() -> None:
    # the Hardware2Program of the program that failed (and its catalog item) is left out
    with zipfile.ZipFile(_FIXTURE) as zf:
        master = zf.read("knx_master.xml")
    files, _warnings, _errors = convert_with_report(
        db(hager_error_tables("mask")), master
    )
    signed = sign(files)
    assert "HP-1236" not in signed["M-0083/Hardware.xml"].decode()
    assert "HP-1236" not in signed["M-0083/Catalog.xml"].decode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("knx_master.xml", master)
        for name, data in signed.items():
            zf.writestr(name, data)
    registry = load(buffer.getvalue())
    assert sorted(registry.applications) == [
        "M-0083_A-1234-01-C450",
        "M-0083_A-1235-01-F11B",
    ]
    assert len(registry.hardware) == 1


def test_unexpected_mask_for_load_control_replacement() -> None:
    with pytest.raises(ValueError, match="Unexpected mask version"):
        replace_load_controls([], "MV-0020")


def test_removed_baggage_of_programs_of_another_manufacturer_goes() -> None:
    # only the programs of the baggage's manufacturer count: M-0008's program still refers to
    # the byte swapper baggage the M-0002 programs no longer use, it leaves the package
    files, _warnings, _errors = run(abb_shared_tables())
    baggages = xml(files, "M-0002/Baggages.xml")
    assert [b.get("Id") for b in every(baggages, "Baggage")] == [
        "M-0002_BG-docs-keep.2Etxt"
    ]
    kept = _app(files, "M-0008_A-A058-01-0000-O0002")
    assert [b.get("RefId") for b in every(kept, "Baggage")] == [
        "M-0002_BG-AbbUsU-x.2Edll"
    ]


def test_old_power_tool_breaks_the_program() -> None:
    files, _warnings, errors = run(power_tool_tables())
    assert errors == []
    broken = {
        app_id: _app(files, app_id).get("Broken")
        for app_id in (f"M-0083_A-{n:04X}-01-0000" for n in range(0x1234, 0x1239))
    }
    assert broken == {
        "M-0083_A-1234-01-0000": "true",
        "M-0083_A-1235-01-0000": None,
        "M-0083_A-1236-01-0000": None,
        "M-0083_A-1237-01-0000": None,
        "M-0083_A-1238-01-0000": "true",
    }


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("1.1.54.0", (1, 1, 54, 0)),
        ("1.2", (1, 2, -1, -1)),
        (" 1 . 2 .3", (1, 2, 3, -1)),
    ],
)
def test_dotnet_version(text: str, version: tuple[int, ...]) -> None:
    assert application.dotnet_version(text) == version


@pytest.mark.parametrize("text", ["1", "1.2.3.4.5", "1.x", "1.-2", "1.4294967296"])
def test_invalid_dotnet_version(text: str) -> None:
    with pytest.raises(ValueError):
        application.dotnet_version(text)


def test_power_tool_without_version_or_baggage_is_not_broken() -> None:
    t = power_tool_tables()
    t["baggage"][0].update(LOCALFILEVERSIONHI=0, LOCALFILEVERSIONLO=0)
    t["applicationprogrambaggage"].append(
        {"ID": 99, "APPLICATIONPROGRAMID": 40, "BAGGAGEID": 1}
    )
    files, _warnings, errors = run(t)
    assert errors == ["InvalidBaggageRefId: M-0083_A-1234-01-0000 1"]
    assert _app(files, "M-0083_A-1234-01-0000").get("Broken") is None


C00A = "M-0004_A-C00A-02-0000-O0001"


def _baggage_files(files: dict[str, bytes]) -> dict[str, bytes]:
    return {n: d for n, d in files.items() if "/Baggages/" in n}


def test_foreign_baggage_is_moved() -> None:
    files, _warnings, errors = run(foreign_baggage_tables("move"))
    assert errors == []
    assert [b.get("RefId") for b in every(_app(files, C00A), "Baggage")] == [
        "M-0004_BG-docs-a.2Edll"
    ]
    # M-0001 is left without baggages; M-0004's list was made by the program (no tool info)
    assert "M-0001/Baggages.xml" not in files
    assert _baggage_files(files) == {"M-0004/Baggages/docs/a.dll": b"a.dll"}
    assert b"CreatedBy" not in files["M-0004/Baggages.xml"]
    moved = find(xml(files, "M-0004/Baggages.xml"), ".//{}Baggage")
    assert dict(moved.attrib) == {
        "TargetPath": "docs",
        "Name": "a.dll",
        "Id": "M-0004_BG-docs-a.2Edll",
    }
    assert children(moved) == [("FileInfo", {"TimeInfo": "2014-06-30T22:00:00Z"})]


def test_foreign_baggage_another_program_uses_is_copied() -> None:
    files, _warnings, _errors = run(foreign_baggage_tables("stay"))
    assert [b.get("RefId") for b in every(_app(files, C00A), "Baggage")] == [
        "M-0004_BG--c.2Etxt"
    ]
    assert [
        b.get("RefId") for b in every(_app(files, "M-0001_A-1111-01-0000"), "Baggage")
    ] == ["M-0001_BG--c.2Etxt"]
    assert _baggage_files(files) == {
        "M-0001/Baggages/c.txt": b"c.txt",
        "M-0004/Baggages/c.txt": b"c.txt",
    }


def test_foreign_baggage_the_manufacturer_has() -> None:
    files, _warnings, _errors = run(foreign_baggage_tables("exists"))
    assert [b.get("RefId") for b in every(_app(files, C00A), "Baggage")] == [
        "M-0004_BG-docs-a.2Edll"
    ]
    # its own file stays, M-0001's goes
    assert _baggage_files(files) == {"M-0004/Baggages/docs/a.dll": b"own"}
    assert len(every(xml(files, "M-0004/Baggages.xml"), "Baggage")) == 1
    assert b"CreatedBy" in files["M-0004/Baggages.xml"]


@pytest.mark.parametrize(
    ("case", "message", "files_left"),
    [
        (
            "other",
            "The directory is not empty.",
            {"M-0001/Baggages/other/b.txt", "M-0004/Baggages/docs/a.dll"},
        ),
        (
            "stay_folder",
            "The directory is not empty.",
            {"M-0001/Baggages/docs2/d.txt", "M-0004/Baggages/docs2/d.txt"},
        ),
        # (the parent of the emptied sub folder stays)
        ("nested", "The directory is not empty.", {"M-0004/Baggages/docs/sub/e.txt"}),
        # a baggage of a third manufacturer: there is nothing to move
        (
            "elsewhere",
            "Could not find a part of the path",
            {"M-0002/Baggages/docs/f.txt"},
        ),
    ],
)
def test_foreign_baggage_the_converter_cannot_move_fails_the_program(
    case: str, message: str, files_left: set[str]
) -> None:
    # the converter works on the files written so far: it deletes the old manufacturer's
    # folders, which fails when they still hold folders, after moving or copying the file
    files, _warnings, errors = run(foreign_baggage_tables(case))
    assert f"M-0004/{C00A}.xml" not in files
    assert errors[0].startswith("ApplicationProgramConversionFailed")
    assert message in errors[0]
    assert set(_baggage_files(files)) == files_left


@pytest.mark.parametrize(
    ("name", "digest"),
    [
        (
            "memory_large",
            "578f184108af4c5beca8e310698ebae7c4ac88736e2439c2a2afd8017593c1b8",
        ),
        (
            "memory_small",
            "5ce89518c4b2959adfbe57158d6aa97ece034736269983c79ec9a28b9b3526f8",
        ),
        ("skewed", "543c33c09f5f7f7f73cb3b149bb8e2b4e98cb3e8280ff5ec1e092b8a46bec8bc"),
        ("random", "aab3e289333362c220044644753274fc87149606d9dd73d59670e9c829750f9c"),
        ("zeros", "5de73a93ed1d02e62a25d5f5bb9927bb9563ea9c2ba2dc32d78e45a323bc7b38"),
        ("period", "adeab8722031fe215ccd035039252d08fea8dc7f2f88398db772ab94bd5749c1"),
        ("mixed", "5d18f7ac9a45cb2f83f0fcb793af017a882748029c52d434907e4297c030fea0"),
    ],
)
def test_deflate_matches_the_converter(name: str, digest: str) -> None:
    # SHA-256 of what DotNetZip's DeflateStream writes for these inputs
    data = _DEFLATE_INPUTS[name]()
    compressed = deflate(data)
    assert hashlib.sha256(compressed).hexdigest() == digest
    assert zlib.decompressobj(-15).decompress(compressed) == data


def test_deflate_of_nothing_is_nothing() -> None:
    assert deflate(b"") == b""


def _skewed() -> bytes:
    # (code lengths over 15 bits that the tree building has to shorten)
    rnd = random.Random(9)
    return bytes(rnd.choices(range(20), [2**i for i in range(20)], k=30000))


def _mixed() -> bytes:
    rnd = random.Random(6)
    out = bytearray(rnd.randbytes(300))
    while len(out) < 120000:
        out += rnd.randbytes(2)
        at = rnd.randrange(len(out) - 300, len(out) - 10)
        out += out[at : at + 3]
    return bytes(out)


_DEFLATE_INPUTS = {
    "memory_large": lambda: memory(100000, 3),
    "memory_small": lambda: memory(3000, 1),
    "skewed": _skewed,
    # (stored blocks; more than one piece of input)
    "random": lambda: random.Random(5).randbytes(90000),
    "zeros": lambda: bytes(70000),
    # (a single distance code)
    "period": lambda: b"abcde" * 2000,
    # (blocks that end after a match)
    "mixed": _mixed,
}

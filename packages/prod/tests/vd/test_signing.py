"""Tests of the fingerprints, id patching and hashes of the converted product data."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from xknxeditor.prod.vd import signing
from xknxeditor.prod.vd.signing import sign

from ._samples import SAMPLES as _SAMPLES

_HEAD = (
    '<?xml version="1.0"?>\r\n<KNX xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema" CreatedBy="knxconv" ToolVersion="5.6.241.33672" '
    'xmlns="http://knx.org/xml/project/14">\r\n  <ManufacturerData>\r\n    <Manufacturer RefId="M-00FA">\r\n'
)
_TAIL = "    </Manufacturer>\r\n  </ManufacturerData>\r\n</KNX>"
_A = "M-00FA_A-0001-01-0000"

# A small application program with the elements whose hashing differs: sorted by id (also with
# "ü" and "-" in ids), by content digest, in document order, text content, parameter values by
# parameter type, unions.
APP = (
    _HEAD
    + f"""      <ApplicationPrograms>
        <ApplicationProgram Id="{_A}" ApplicationNumber="1" ApplicationVersion="1" ProgramType="ApplicationProgram" MaskVersion="MV-0012" Name="Test" LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="de-DE" DynamicTableManagement="false" Linkable="false">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_A}_AS-0100" Address="256" Size="4">
                <Data>AAECAw==</Data>
              </AbsoluteSegment>
              <AbsoluteSegment Id="{_A}_AS-0010" Address="16" Size="2" />
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_A}_PT-Zähler" Name="Zähler">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
              <ParameterType Id="{_A}_PT-Zahl" Name="Zahl">
                <TypeFloat Encoding="DPT 9" minInclusive="-10" maxInclusive="1.5E2" />
              </ParameterType>
              <ParameterType Id="{_A}_PT-a-b" Name="ab">
                <TypeRestriction Base="Value" SizeInBit="1">
                  <Enumeration Text="Aus" Value="0" Id="{_A}_PT-a-b_EN-0" />
                  <Enumeration Text="Ein" Value="1" Id="{_A}_PT-a-b_EN-1" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_A}_PT-ab" Name="ab2">
                <TypeNone />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_A}_P-1" Name="P1" ParameterType="{_A}_PT-Zähler" Text="Zähler" Value="7">
                <Memory CodeSegment="{_A}_AS-0100" Offset="1" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_A}_P-2" Name="P2" ParameterType="{_A}_PT-Zahl" Text="Zahl" Value="2.50" />
              <Union SizeInBit="8">
                <Memory CodeSegment="{_A}_AS-0100" Offset="2" BitOffset="0" />
                <Parameter Id="{_A}_P-3" Name="P3" ParameterType="{_A}_PT-a-b" Text="Wahl" Offset="0" BitOffset="0" Value="1" DefaultUnionParameter="true" />
              </Union>
              <Parameter Id="{_A}_P-10" Name="P10" ParameterType="{_A}_PT-ab" Text="Info&#10;Zeile" Value="" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_A}_P-1_R-1" RefId="{_A}_P-1" />
              <ParameterRef Id="{_A}_P-2_R-2" RefId="{_A}_P-2" Value="3" />
              <ParameterRef Id="{_A}_P-3_R-3" RefId="{_A}_P-3" />
              <ParameterRef Id="{_A}_P-10_R-10" RefId="{_A}_P-10" />
            </ParameterRefs>
            <ComObjectTable CodeSegment="{_A}_AS-0010" Offset="0" />
            <ComObjects>
              <ComObject Id="{_A}_O-0" Name="Schalten" Text="Schalten" Number="0" FunctionText="Ein/Aus" ObjectSize="1 Bit" />
            </ComObjects>
            <ComObjectRefs>
              <ComObjectRef Id="{_A}_O-0_R-1" RefId="{_A}_O-0" />
            </ComObjectRefs>
            <AddressTable MaxEntries="10" />
            <AssociationTable CodeSegment="{_A}_AS-0010" Offset="1" MaxEntries="10" />
            <LoadProcedures>
              <LoadProcedure>
                <LdCtrlConnect />
                <LdCtrlRestart />
                <LdCtrlDisconnect />
              </LoadProcedure>
            </LoadProcedures>
            <Extension>
              <Baggage RefId="M-00FA_BG-x-y.2Etxt" />
            </Extension>
            <Options />
          </Static>
          <Dynamic>
            <Channel Name="C" Text="" Number="1" Id="{_A}_CH-1">
              <ParameterBlock Id="{_A}_PB-1" Name="B" Text="Allgemein &amp; mehr">
                <ParameterRefRef RefId="{_A}_P-1_R-1" />
                <choose ParamRefId="{_A}_P-1_R-1">
                  <when test="7">
                    <ParameterRefRef RefId="{_A}_P-2_R-2" />
                    <ComObjectRefRef RefId="{_A}_O-0_R-1" />
                  </when>
                  <when default="true">
                    <ParameterRefRef RefId="{_A}_P-3_R-3" />
                  </when>
                </choose>
              </ParameterBlock>
            </Channel>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
      <Languages>
        <Language Identifier="en-US">
          <TranslationUnit RefId="{_A}">
            <TranslationElement RefId="{_A}_P-1">
              <Translation AttributeName="Text" Text="Counter&#9;tab" />
            </TranslationElement>
          </TranslationUnit>
        </Language>
      </Languages>
"""
    + _TAIL
)

# A second program that no hardware refers to: removed.
APP2 = (
    _HEAD
    + """      <ApplicationPrograms>
        <ApplicationProgram Id="M-00FA_A-0002-01-0000" ApplicationNumber="2" ApplicationVersion="1" ProgramType="ApplicationProgram" MaskVersion="MV-0012" Name="Unused" LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="de-DE" DynamicTableManagement="false" Linkable="false">
          <Static />
        </ApplicationProgram>
      </ApplicationPrograms>
"""
    + _TAIL
)

# A registered device with the program and an accessory whose Hardware2Program has no
# registration (removed with its catalog item and translation).
HARDWARE = (
    _HEAD
    + f"""      <Hardware>
        <Hardware Id="M-00FA_H-1-1" Name="Device&#9;1" SerialNumber="1" VersionNumber="1" BusCurrent="10" HasIndividualAddress="true" HasApplicationProgram="true">
          <Products>
            <Product Id="M-00FA_H-1-1_P-1" Text="Device 1" OrderNumber="1" IsRailMounted="true" DefaultLanguage="de-DE">
              <RegistrationInfo RegistrationStatus="Registered" RegistrationDate="2010-01-01" />
            </Product>
          </Products>
          <Hardware2Programs>
            <Hardware2Program Id="M-00FA_H-1-1_HP-0001-01-0000" MediumTypes="MT-0">
              <ApplicationProgramRef RefId="{_A}" />
              <RegistrationInfo RegistrationStatus="Registered" RegistrationNumber="2010/1" RegistrationDate="2010-01-01" />
            </Hardware2Program>
          </Hardware2Programs>
        </Hardware>
        <Hardware Id="M-00FA_H-2-1" Name="Accessory" SerialNumber="2" VersionNumber="1" IsAccessory="true" HasIndividualAddress="false" HasApplicationProgram="false">
          <Products>
            <Product Id="M-00FA_H-2-1_P-2" Text="Accessory" OrderNumber="2" IsRailMounted="false" DefaultLanguage="de-DE">
              <RegistrationInfo RegistrationStatus="Unregistered" />
            </Product>
          </Products>
          <Hardware2Programs>
            <Hardware2Program Id="M-00FA_H-2-1_HP" MediumTypes="MT-0" />
          </Hardware2Programs>
        </Hardware>
      </Hardware>
"""
    + _TAIL
)

CATALOG = (
    _HEAD
    + """      <Catalog>
        <CatalogSection Id="M-00FA_CS-1" Name="Devices" Number="1" DefaultLanguage="de-DE">
          <CatalogItem Id="M-00FA_H-1-1_HP-0001-01-0000_CI-1-1" Name="Device 1" Number="1" ProductRefId="M-00FA_H-1-1_P-1" Hardware2ProgramRefId="M-00FA_H-1-1_HP-0001-01-0000" DefaultLanguage="de-DE" />
        </CatalogSection>
        <CatalogSection Id="M-00FA_CS-2" Name="Accessories" Number="2" DefaultLanguage="de-DE">
          <CatalogItem Id="M-00FA_H-2-1_HP_CI-2-1" Name="Accessory" Number="1" ProductRefId="M-00FA_H-2-1_P-2" Hardware2ProgramRefId="M-00FA_H-2-1_HP" DefaultLanguage="de-DE" />
        </CatalogSection>
      </Catalog>
      <Languages>
        <Language Identifier="en-US">
          <TranslationUnit RefId="M-00FA_H-1-1_HP-0001-01-0000_CI-1-1">
            <TranslationElement RefId="M-00FA_H-1-1_HP-0001-01-0000_CI-1-1">
              <Translation AttributeName="Name" Text="Device one" />
            </TranslationElement>
          </TranslationUnit>
        </Language>
        <Language Identifier="fr-FR">
          <TranslationUnit RefId="M-00FA_H-2-1_HP_CI-2-1">
            <TranslationElement RefId="M-00FA_H-2-1_HP_CI-2-1">
              <Translation AttributeName="Name" Text="Accessoire" />
            </TranslationElement>
          </TranslationUnit>
        </Language>
      </Languages>
"""
    + _TAIL
)

BAGGAGES = '<?xml version="1.0"?>\r\n<KNX />'


def package() -> dict[str, bytes]:
    return {
        f"M-00FA/{_A}.xml": APP.encode(),
        "M-00FA/M-00FA_A-0002-01-0000.xml": APP2.encode(),
        "M-00FA/Hardware.xml": HARDWARE.encode(),
        "M-00FA/Catalog.xml": CATALOG.encode(),
        "M-00FA/Baggages.xml": BAGGAGES.encode(),
        "M-00FA/Baggages/x/y.txt": b"baggage",
    }


# What the converter's signing step makes of package() (its output without registration
# signatures): SHA-256 of every file.
EXPECTED = {
    "M-00FA/Baggages.xml": "a4ee2966720999e09ee5c6b45b525af3a921e260e6996baeae789ef309b6c96e",
    "M-00FA/Baggages/x/y.txt": "1d6f21ea90c6de24052b833f172d702a79ec68014a5a46d18b536807763164e5",
    "M-00FA/Catalog.xml": "b0b377a93a8187dc61db676a96942b73a6a2ab7e494ae791cc27b6458a074742",
    "M-00FA/Hardware.xml": "49c0b6f2c23c187c32a2be22f0608e82a4bfd68c5760b12e15c5868dd1141256",
    "M-00FA/M-00FA_A-0001-01-A708.xml": "b2bfc1802d5e8bfd711c1e02257d3742f63573a99955e26f56b6fa44b2578607",
}


def _attr(data: bytes, element: str, attr: str) -> list[str]:
    return [
        m.decode()
        for m in re.findall(
            rb"<" + element.encode() + rb" [^>]*?" + attr.encode() + rb'="([^"]*)"',
            data,
        )
    ]


def test_sign_matches_converter() -> None:
    out = sign(package())
    assert {k: hashlib.sha256(v).hexdigest() for k, v in out.items()} == EXPECTED


def test_fingerprints_ids_and_hashes() -> None:
    out = sign(package())
    app = out["M-00FA/M-00FA_A-0001-01-A708.xml"]
    assert _attr(app, "ApplicationProgram", "Hash") == ["p/anEArTuLXao/NLkK01CA=="]
    assert b"-0000" not in app
    assert _attr(app, "TranslationUnit", "RefId") == ["M-00FA_A-0001-01-A708"]
    assert (
        _attr(app, "Parameter", "ParameterType")[0] == "M-00FA_A-0001-01-A708_PT-Zähler"
    )
    hardware = out["M-00FA/Hardware.xml"]
    assert _attr(hardware, "Product", "Hash") == [
        "FFaWijeynHb/YCphDm6h6XkIQ0E=",
        "Ztq6+b2UkPjMy5B+KEh7mSNyIJM=",
    ]
    assert _attr(hardware, "Hardware2Program", "Id") == ["M-00FA_H-1-1_HP-0001-01-A708"]
    assert _attr(hardware, "Hardware2Program", "Hash") == [
        "pSWZJhK9y2bgbyEUZhwbP0S3wDY="
    ]
    assert _attr(hardware, "ApplicationProgramRef", "RefId") == [
        "M-00FA_A-0001-01-A708"
    ]
    catalog = out["M-00FA/Catalog.xml"]
    assert _attr(catalog, "CatalogItem", "Id") == [
        "M-00FA_H-1-1_HP-0001-01-A708_CI-1-1"
    ]
    assert b"fr-FR" not in catalog
    # The section of the removed item keeps an explicit end tag.
    assert (
        b'Number="2" DefaultLanguage="de-DE">\r\n        </CatalogSection>' in catalog
    )


def test_unreferenced_program_is_removed() -> None:
    assert not [k for k in sign(package()) if "_A-0002-" in k]


def test_hash_does_not_depend_on_formatting() -> None:
    files = package()
    flat = re.sub(r">\s+<", "><", APP).replace(
        '<?xml version="1.0"?>', "<?xml version='1.0' encoding='utf-8'?>"
    )
    flat = flat.replace('Name="P1" ParameterType', "ParameterType").replace(
        "<Parameter Id", '<Parameter Name="P1" Id', 1
    )
    files[f"M-00FA/{_A}.xml"] = flat.encode()
    out = sign(files)
    app = out["M-00FA/M-00FA_A-0001-01-A708.xml"]
    assert _attr(app, "ApplicationProgram", "Hash") == ["p/anEArTuLXao/NLkK01CA=="]


def test_relevant_change_changes_fingerprint() -> None:
    files = package()
    files[f"M-00FA/{_A}.xml"] = APP.replace('Value="7"', 'Value="8"').encode()
    files["M-00FA/Hardware.xml"] = HARDWARE.encode()
    out = sign(files)
    assert "M-00FA/M-00FA_A-0001-01-A708.xml" not in out
    assert len([k for k in out if "_A-0001-01-" in k]) == 1


def test_other_files_unchanged_and_no_hardware() -> None:
    files = {
        f"M-00FA/{_A}.xml": APP.encode(),
        "M-00FA/Other.txt": b"x",
        "readme.txt": b"y",
    }
    out = sign(files)
    # Without hardware nothing refers to the program, but nothing cleans either.
    assert sorted(out) == [
        "M-00FA/M-00FA_A-0001-01-A708.xml",
        "M-00FA/Other.txt",
        "readme.txt",
    ]


# Sort keys as the system builds them (de-DE).
@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("a-b", "0e020e0901010101fffe821200"),
        ("a--b", "0e020e0901010101fffe8212fffe821200"),
        ("AB", "0e020e0901011212010100"),
        ("a_b", "0e0207440e090101010100"),
        ("ß", "0e910e910101010100"),
        ("Ä", "0e0201130112010100"),
        (
            "M-0001_A-7002-01-0000_P-1",
            "0e510d030d030d030d1a07440e020d260d030d030d1c0d030d1a0d030d030d030d0307440e7e0d1a0101120202020202120202"
            "020202020202020202120101fffe8212fff88212fff48212fff28212ffec821200",
        ),
    ],
)
def test_sort_key(text: str, key: str) -> None:
    assert signing._sort_key(text).hex() == key


def test_sort_order() -> None:
    ids = ["P-10", "P-2", "P-1", "p-1", "P_1", "Pü", "Pu", "Pv", "+", "/", "="]
    assert sorted(ids, key=signing._sort_key) == [
        "/",
        "+",
        "=",
        "P_1",
        "p-1",
        "P-1",
        "P-10",
        "P-2",
        "Pu",
        "Pü",
        "Pv",
    ]


# A reader ignoring whitespace and an indenting writer: escaping, newlines, mixed content.
PROBE = (
    '<?xml version="1.0"?>\r\n<KNX xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" CreatedBy="x" '
    'xmlns="http://knx.org/xml/project/14">\r\n  <A T="a&#9;b&#10;c&#13;d &lt;&gt;&amp;&quot;\' e" '
    'U="line1\nline2">\r\n    <B>text &lt;&gt;&amp;&quot;\' x&#13;y&#10;z\n2&#9;t</B>\r\n'
    '    <C>mixed<D x="1"/>after<E><F/></E></C>\r\n    <G></G>\r\n    <H><![CDATA[cd <x> ]]></H>\r\n'
    "    <I>   </I>\r\n    <J> a </J><K>\u00e4\u20ac\U0001f600</K>\r\n  </A>\r\n</KNX>"
)
PROBE_OUT = (
    '\ufeff<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<KNX xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" CreatedBy="x" xmlns="http://knx.org/xml/project/14">\r\n'
    '  <A T="a&#x9;b&#xA;c&#xD;d &lt;&gt;&amp;&quot;\' e" U="line1 line2">\r\n'
    "    <B>text &lt;&gt;&amp;\"' x\r\ny\r\nz\r\n2\tt</B>\r\n"
    '    <C>mixed<D x="1" />after<E><F /></E></C>\r\n'
    "    <G />\r\n"
    "    <H><![CDATA[cd <x> ]]></H>\r\n"
    "    <I />\r\n"
    "    <J> a </J>\r\n"
    "    <K>\u00e4\u20ac\U0001f600</K>\r\n"
    "  </A>\r\n"
    "</KNX>"
)


def test_writer() -> None:
    root = signing._parse(PROBE.encode())
    assert signing._serialize(root, raw_tab=False) == PROBE_OUT.encode()
    # A document save writes tabs in attribute values as they are.
    assert (
        signing._serialize(root, raw_tab=True)
        == PROBE_OUT.replace("a&#x9;b", "a\tb").encode()
    )


def test_writer_rejects_invalid_characters() -> None:
    root = signing._parse(b'<KNX a="x"><B>y</B></KNX>')
    root.attrs[0][1] = "\x01"
    with pytest.raises(ValueError, match="Invalid XML character"):
        signing._serialize(root, raw_tab=False)
    root.attrs[0][1] = "x"
    child = next(root.elements("B"))
    child.children[0] = signing._Text("\x02")
    with pytest.raises(ValueError, match="Invalid XML character"):
        signing._serialize(root, raw_tab=True)


@pytest.mark.parametrize(
    ("value", "typ", "packed"),
    [
        (" 5 ", "U8", b"\x05"),
        ("+7", "U16", b"\x07\x00"),
        ("-1", "I32", b"\xff\xff\xff\xff"),
        ("1", "I64", b"\x01" + bytes(7)),
    ],
)
def test_integers(value: str, typ: str, packed: bytes) -> None:
    assert signing._to_int(value, typ) == packed


@pytest.mark.parametrize(
    ("value", "typ"),
    [("256", "U8"), ("-1", "U32"), ("0x1", "U16"), ("1_0", "U16"), ("", "U8")],
)
def test_invalid_integers(value: str, typ: str) -> None:
    with pytest.raises(ValueError):
        signing._to_int(value, typ)


def test_booleans_doubles_dates() -> None:
    assert signing._to_bool(" true") is True
    assert signing._to_bool("0") is False
    with pytest.raises(ValueError):
        signing._to_bool("yes")
    assert signing._to_double("1.5E2") == 150.0
    assert signing._to_double("-INF") == float("-inf")
    assert signing._to_double(".5") == 0.5
    with pytest.raises(ValueError):
        signing._to_double("1_0")
    assert signing._to_oa_date("2010-01-01") == 40179.0
    assert signing._to_oa_date("1899-12-29T12:00:00") == -1.5
    with pytest.raises(ValueError):
        signing._to_oa_date("01.01.2010")


def test_string_length_prefix() -> None:
    assert signing._str("ab") == b"\x02ab"
    assert signing._str("x" * 200)[:2] == b"\xc8\x01"


def test_plugin_baggage_part() -> None:
    assert signing._plugin_present(None, "{0F0F}", "") is False
    assert signing._plugin_present("Plugin.dll", None, None) is True
    assert signing._critical_baggage("M-0001_BG-dir-Plug.2Edll") is True
    assert signing._critical_baggage("M-0001_BG-dir-readme.2Etxt") is False
    with pytest.raises(ValueError):
        signing._critical_baggage("M-0001_X-1")


def test_plugin_extension_uses_baggage_integrity() -> None:
    # A program with a plugin of the newer kind: the integrity of its .dll baggage counts.
    files = package()
    app = APP.replace("<Extension>", '<Extension EtsUiPlugin="Plug">').replace(
        "M-00FA_BG-x-y.2Etxt", "M-00FA_BG-x-p.2Edll"
    )
    files[f"M-00FA/{_A}.xml"] = app.encode()
    files["M-00FA/Baggages.xml"] = (
        b'<?xml version="1.0"?><KNX><ManufacturerData><Manufacturer RefId="M-00FA"><Baggages>'
        b'<Baggage TargetPath="x" Name="p.dll" Id="M-00FA_BG-x-p.2Edll" /></Baggages></Manufacturer>'
        b"</ManufacturerData></KNX>"
    )
    files["M-00FA/Baggages/x/p.dll"] = b"one"
    first = [k for k in sign(files) if "_A-0001-" in k]
    files["M-00FA/Baggages/x/p.dll"] = b"two"
    second = [k for k in sign(files) if "_A-0001-" in k]
    assert first != second


def test_inconsistent_children_are_rejected() -> None:
    dup = APP.replace(
        f'<AbsoluteSegment Id="{_A}_AS-0010"', f'<AbsoluteSegment Id="{_A}_AS-0100"'
    )
    with pytest.raises(ValueError, match="Duplicate child"):
        sign({f"M-00FA/{_A}.xml": dup.encode()})


def test_hardware2program_of_a_missing_program_is_dropped() -> None:
    # a program that failed to convert: its Hardware2Program, catalog item and the item's
    # translations go, products and sections stay (the accessory's go as they are unregistered)
    files = package()
    del files[f"M-00FA/{_A}.xml"]
    out = sign(files)
    hardware = out["M-00FA/Hardware.xml"]
    assert _attr(hardware, "Hardware2Program", "Id") == []
    assert _attr(hardware, "Product", "Id") == ["M-00FA_H-1-1_P-1", "M-00FA_H-2-1_P-2"]
    assert b"<Hardware2Programs" not in hardware
    catalog = out["M-00FA/Catalog.xml"]
    assert _attr(catalog, "CatalogItem", "Id") == []
    assert _attr(catalog, "CatalogSection", "Id") == ["M-00FA_CS-1", "M-00FA_CS-2"]
    assert b"Device one" not in catalog
    assert b"Accessoire" not in catalog
    # (the program no Hardware2Program refers to anymore goes as well)
    assert sorted(out) == [
        "M-00FA/Baggages.xml",
        "M-00FA/Baggages/x/y.txt",
        "M-00FA/Catalog.xml",
        "M-00FA/Hardware.xml",
    ]


_MASTER = Path(r"C:\ProgramData\KNX\XML\project-14\knx_master.xml")


@pytest.mark.parametrize(
    ("pattern", "program"),
    [
        ("N148_12_700201.vd5", "M-0001/M-0001_A-7002-01-65D5.xml"),
        ("1152_KNX_Netzteil.vd4", "M-00A6/M-00A6_A-0003-00-2670.xml"),
        ("SynOhr-*.vd4", "M-00A6/M-00A6_A-0002-0A-3DA5.xml"),
        ("EIBADUMY.VD1", "M-00FA/M-00FA_A-7000-02-D08C.xml"),
        ("ITR512-16A.vd4", "M-0107/M-0107_A-0200-01-4DE5.xml"),
    ],
)
def test_samples(pattern: str, program: str) -> None:
    """The fingerprints of the converted samples (needs the conversion and the samples)."""
    convert = pytest.importorskip("xknxeditor.prod.vd.convert")
    found = sorted(_SAMPLES.glob(pattern)) if _SAMPLES.is_dir() else []
    if not found or not _MASTER.exists():
        pytest.skip(f"{pattern} or the master data is missing")
    from xknxeditor.prod.vd.archive import read_dump
    from xknxeditor.prod.vd.database import Database
    from xknxeditor.prod.vd.upgrade import upgrade

    db = Database.from_dump(read_dump(found[0]))
    upgrade(db)
    out = sign(convert.convert(db, _MASTER.read_bytes()))
    assert program in out

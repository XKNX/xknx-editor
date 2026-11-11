"""Tests of the read-only Siemens Generic Plug-in reader.

The fixtures are small, made-up documents in the plug-in's XML dialect (a "Demo" device with two
channels): no manufacturer product data or project data is used.
"""

from __future__ import annotations

import base64

import pytest
from editor_gui.plugin_blob_readers.siemens_generic import (
    MemoryCheck,
    Row,
    find_sources,
    is_xml_baggage,
    parse_description,
    parse_settings,
    parse_translations,
    pick_language,
    read_view,
)
from editor_gui.plugin_blob_readers.siemens_generic import view as view_module
from editor_gui.plugin_blob_readers.siemens_generic.codec import (
    byte_size,
    decode,
    encode,
)
from editor_gui.plugin_blob_readers.siemens_generic.description import (
    ParameterType,
    TypeKind,
    clean_text,
)

_BASE = 0x4000

DESCRIPTION = f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationProgram Id="id-DEMO_0001_01" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:noNamespaceSchemaLocation="GenericPlugIn_4.xsd">
 <Static>
  <ParameterTypes>
   <ParameterType Id="t_yn" Name="yn"><TypeRestriction Size="8">
     <Enumeration Id="e_no" Name="dm_No" Text="No" Value="0"/>
     <Enumeration Id="e_yes" Name="dm_Yes" Text="Yes" Value="1"/>
   </TypeRestriction></ParameterType>
   <ParameterType Id="t_mode" Name="mode"><TypeRestriction Size="8">
     <Enumeration Id="e_off" Name="dm_Off" Text="Off" Value="0"/>
     <Enumeration Id="e_a" Name="dm_A" Text="Mode A" Value="1"/>
     <Enumeration Id="e_b" Name="dm_B" Text="Mode B" Value="2"/>
   </TypeRestriction></ParameterType>
   <ParameterType Id="t_word" Name="word">
     <TypeNumber Size="16" IntelOrderSwap="true" Type="unsigned"/></ParameterType>
   <ParameterType Id="t_sbyte" Name="sbyte"><TypeNumber Size="8" Type="signed"/></ParameterType>
   <ParameterType Id="t_time" Name="time"><TypeTimeSec Size="16"/></ParameterType>
   <ParameterType Id="t_text" Name="text"><TypeText Size="32"/></ParameterType>
  </ParameterTypes>
  <Parameters>
   <Parameter Id="p_mode" Name="DemoMode" RefId="t_mode" Value="dm_Off" Text="DemoMode">
     <Memory CodeSegment="c" Offset="{_BASE}"/></Parameter>
   <Parameter Id="p_fb" Name="DemoFeedback" RefId="t_yn" Value="dm_No" Text="Feedback\\r\\nobject">
     <Memory CodeSegment="c" Offset="{_BASE + 1}"/></Parameter>
   <Parameter Id="p_delay" Name="DemoDelay" RefId="t_word" Value="300" Text="Delay">
     <Memory CodeSegment="c" Offset="{_BASE + 2}"/></Parameter>
   <Parameter Id="p_offset" Name="DemoOffset" RefId="t_sbyte" Value="0" Text="Offset">
     <Memory CodeSegment="c" Offset="{_BASE + 4}"/></Parameter>
   <Parameter Id="p_derived" Name="DemoDerived" RefId="t_yn" Value="dm_No" Text="Derived">
     <Memory CodeSegment="c" Offset="{_BASE + 5}"/></Parameter>
   <Parameter Id="p_time" Name="DemoTime" RefId="t_time" Value="10" Text="Run time">
     <Memory CodeSegment="c" Offset="{_BASE + 6}"/></Parameter>
   <Parameter Id="p_count" Name="DemoCount" RefId="t_mode" Value="dm_Off" Text="Count">
     <Memory CodeSegment="c" Offset="{_BASE + 8}"/></Parameter>
   <Parameter Id="p_name" Name="DemoName" RefId="t_text" Value="" Text="Name"/>
   <Parameter Id="p_order" Name="Order" RefId="t_text" Value="" Text="Order"/>
  </Parameters>
  <ParameterRefs>
   <ParameterRef Id="r_order" RefId="p_order" Value="DEMO-1" GlobalVariableReference="DeviceOrderNumber"/>
   <ParameterRef Id="r_mode" RefId="p_mode" Value="dm_Off"/>
   <ParameterRef Id="r_fb" RefId="p_fb" Text="Status feedback"/>
   <ParameterRef Id="r_delay" RefId="p_delay" Value="300"/>
   <ParameterRef Id="r_offset" RefId="p_offset"/>
   <ParameterRef Id="r_derived" RefId="p_derived"/>
   <ParameterRef Id="r_time" RefId="p_time"/>
   <ParameterRef Id="r_name" RefId="p_name"/>
   <ParameterRef Id="r_count_a" RefId="p_count" Value="dm_A"/>
   <ParameterRef Id="r_count_b" RefId="p_count" Value="dm_B"/>
  </ParameterRefs>
 </Static>
 <Dynamic>
  <ChannelIndependentBlock>
   <ParameterBlock Id="b_gen" Text="General">
    <choose ParamRefId="r_order">
     <when test="DEMO-1;DEMO-2"><ParameterRefRef RefId="r_mode"/></when>
     <when test="DEMO-9"><ParameterRefRef RefId="r_name"/></when>
    </choose>
    <ParameterSeparator Id="s_timing" Text="Timing"/>
    <ParameterRefRef RefId="r_time"/>
   </ParameterBlock>
  </ChannelIndependentBlock>
  <Channel Id="c1" Number="1">
   <ParameterBlock Id="b_c1" Text="Channel 1">
    <choose ParamRefId="r_mode">
     <when test="dm_A;dm_B"><ParameterRefRef RefId="r_fb"/><ParameterRefRef RefId="r_delay"/></when>
     <when test="dm_B"><ParameterRefRef RefId="r_offset"/></when>
     <when default="true"><ParameterRefRef RefId="r_name"/></when>
    </choose>
    <choose ParamRefId="r_derived">
     <when test="dm_Yes"><ParameterRefRef RefId="r_derived"/></when>
    </choose>
    <choose ParamRefId="r_fb">
     <when test="dm_Yes"><Assign TargetParamRefRef="r_derived" Value="dm_Yes"/></when>
     <when default="true"><Assign TargetParamRefRef="r_derived" SourceParamRefRef="r_fb"/></when>
    </choose>
    <ParameterRefRef RefId="r_count_a"/>
    <choose ParamRefId="r_count_b" SearchForActiveReferenceValue="true">
     <when test="dm_A"><ParameterSeparator Id="s_count" Text="Count is A"/></when>
    </choose>
    <ComObjectRefRef RefId="o_1"/>
   </ParameterBlock>
  </Channel>
  <Channel Id="c2" Number="1">
   <ParameterBlock Id="b_c2" Text="Channel 2"/>
  </Channel>
 </Dynamic>
</ApplicationProgram>
""".encode()

TRANSLATIONS = b"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationProgram Id="id-DEMO_0001_01">
 <Languages>
  <Language LanguageId="en-US">
   <Data RefId="p_mode"><Translation AttributeName="Text" Text="Operating mode"/></Data>
   <Data RefId="b_gen"><Translation AttributeName="Text" Text="General settings"/></Data>
   <Data RefId="p_delay"><Translation AttributeName="ErrorMessage" Text="ignored"/></Data>
  </Language>
  <Language LanguageId="de-DE">
   <Data RefId="b_gen"><Translation AttributeName="Text" Text="Allgemein"/></Data>
   <Data RefId="e_b"><Translation AttributeName="Text" Text="Betriebsart B"/></Data>
  </Language>
 </Languages>
</ApplicationProgram>
"""

# Values as the plug-in stores them; the image holds exactly these (base = lowest offset).
_VALUES = {"r_mode": "dm_B", "r_fb": "dm_Yes", "r_delay": "600", "r_offset": "-2"}
_IMAGE = bytes([2, 1, 0x58, 0x02, 0xFE, 1, 0x00, 0x0A, 1]) + b"\xab" * 7


def settings_blob(
    values: dict[str, str] | None = None,
    image: bytes | None = _IMAGE,
    *,
    name: str = "id-DEMO_0001_01",
    encoding: str = "utf-16-le",
) -> bytes:
    refs = "".join(
        f'<ParameterInstanceRef Id="i_{k}" RefId="{k}"><Value>{v}</Value>'
        "</ParameterInstanceRef>"
        for k, v in (_VALUES if values is None else values).items()
    )
    downloaded = ""
    if image is not None:
        data = base64.b64encode(image).decode()
        downloaded = (
            '<DownloadedSettings Id="V1" LastDownload="2025-01-02T03:04:05.678">'
            f'<MemoryImage Data="{data}"/></DownloadedSettings>'
        )
    xml = (
        '<?xml version="1.0" encoding="utf-16"?><ApplicationProgramInstance>'
        f'<CurrentSettings Id="V1" Name="{name}" LastModified="2025-01-01T00:00:00">'
        f'<ParameterInstanceRefs><ParameterInstanceRef RefId=""/>{refs}'
        f"</ParameterInstanceRefs></CurrentSettings>{downloaded}"
        "</ApplicationProgramInstance>"
    )
    return xml.encode(encoding)


def _sources():
    files = {
        "M-DEMO_BG-1-Demo.2Exml": DESCRIPTION,
        "M-DEMO_BG-1-Texts.2Exml": TRANSLATIONS,
        "M-DEMO_BG-1-Setup.2Emsi": b"not xml",
    }
    sources = find_sources(files, files.get)
    assert sources is not None
    return sources


def _rows(view) -> dict[str, Row]:  # type: ignore[no-untyped-def]
    return {r.ref_id: r for p in view.pages for r in p.rows if not r.separator}


# --- description -----------------------------------------------------------------------------


def test_description_parses_types_parameters_and_tree() -> None:
    description = parse_description(DESCRIPTION)
    assert description is not None
    assert description.id == "id-DEMO_0001_01"
    assert description.memory_base == _BASE
    assert description.types["t_word"].little_endian
    assert description.types["t_sbyte"].signed
    assert description.types["t_time"].kind is TypeKind.TIME_S
    assert description.parameters["p_fb"].text == "Feedback object"
    assert description.parameters["p_name"].offset is None
    assert description.refs["r_order"].global_variable == "DeviceOrderNumber"
    assert description.refs_by_parameter["p_count"] == ["r_count_a", "r_count_b"]
    assert description.texts["s_timing"] == "Timing"


@pytest.mark.parametrize(
    "data",
    [b"not xml", TRANSLATIONS, b"<ApplicationProgram><Static/></ApplicationProgram>"],
)
def test_other_documents_are_no_description(data: bytes) -> None:
    assert parse_description(data) is None


def test_translations_keep_only_texts() -> None:
    texts = parse_translations(TRANSLATIONS)
    assert texts["en-US"] == {"p_mode": "Operating mode", "b_gen": "General settings"}
    assert texts["de-DE"]["e_b"] == "Betriebsart B"
    assert parse_translations(b"<broken") == {}


def test_clean_text_joins_lines() -> None:
    assert clean_text("A\\r\\nB  \n C") == "A B C"


# --- stored settings -------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16", "utf-16-be", "utf-8"])
def test_settings_read_in_every_encoding(encoding: str) -> None:
    settings = parse_settings(settings_blob(encoding=encoding))
    assert settings is not None
    assert settings.values == _VALUES
    assert settings.memory_image == _IMAGE
    assert settings.last_download == "2025-01-02T03:04:05.678"
    assert settings.application_id == "id-DEMO_0001_01"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff\xfe\x00\xd8",  # a lone surrogate: not UTF-16
        "<Other/>".encode("utf-16-le"),
        b"<ApplicationProgramInstance><CurrentSettings",
    ],
)
def test_no_settings(data: bytes) -> None:
    assert parse_settings(data) is None


def test_settings_without_image() -> None:
    settings = parse_settings(settings_blob(image=None))
    assert settings is not None
    assert settings.memory_image is None


# --- codec -----------------------------------------------------------------------------------


def _type(kind: TypeKind, bits: int, **kw: bool) -> ParameterType:
    return ParameterType(id="t", kind=kind, size_bits=bits, **kw)


def test_codec_numbers_and_enums() -> None:
    description = parse_description(DESCRIPTION)
    assert description is not None
    mode = description.types["t_mode"]
    assert encode("dm_B", mode) == b"\x02"
    assert decode(b"\x02", mode) == "dm_B"
    assert decode(b"\x09", mode) == "9"
    assert encode("dm_Unknown", mode) is None
    word = _type(TypeKind.NUMBER, 16, little_endian=True)
    assert encode("600", word) == b"\x58\x02"
    assert decode(b"\x58\x02", word) == "600"
    assert encode("600", _type(TypeKind.TIME_S, 16)) == b"\x02\x58"
    signed = _type(TypeKind.NUMBER, 8, signed=True)
    assert encode("-2", signed) == b"\xfe"
    assert decode(b"\xfe", signed) == "-2"
    assert encode("x", word) is None
    assert encode("70000", word) is None
    assert encode("1", _type(TypeKind.NUMBER, 4)) is None
    assert byte_size(_type(TypeKind.NUMBER, 0)) is None
    assert encode("abc", _type(TypeKind.TEXT, 32)) is None
    assert decode(b"abcd", _type(TypeKind.TEXT, 32)) is None


# --- view ------------------------------------------------------------------------------------


def test_view_shows_active_branches_with_texts_and_checks() -> None:
    sources = _sources()
    view = read_view(sources, settings_blob())
    assert view is not None
    assert [(p.title, p.channel) for p in view.pages] == [
        ("General settings", 0),
        ("Channel 1", 1),
    ]
    general, channel = view.pages
    assert [r.ref_id for r in general.rows] == ["r_mode", "s_timing", "r_time"]
    assert general.rows[1].separator and general.rows[1].label == "Timing"
    # dm_B activates both matching whens, not the default one.
    assert [r.ref_id for r in channel.rows] == [
        "r_fb",
        "r_delay",
        "r_offset",
        "r_derived",
        "r_count_a",
        "s_count",
    ]
    rows = _rows(view)
    mode = rows["r_mode"]
    assert (mode.label, mode.value, mode.value_text) == (
        "Operating mode",
        "dm_B",
        "Mode B",
    )
    assert mode.stored and mode.default_value == "dm_Off"
    assert rows["r_fb"].label == "Status feedback"
    assert rows["r_time"].value_text == "10 s" and not rows["r_time"].stored
    assert rows["r_derived"].assigned and rows["r_derived"].value == "dm_Yes"
    assert all(r.memory is MemoryCheck.MATCH for r in rows.values())
    assert rows["r_delay"].address == _BASE + 2
    assert view.count(MemoryCheck.MATCH) == 7
    assert view.mismatches == []
    assert view.application_matches and view.image_layout_known


def test_view_marks_a_value_the_image_does_not_hold() -> None:
    image = bytearray(_IMAGE)
    image[2:4] = b"\x2c\x01"  # 300 instead of the stored 600
    view = read_view(_sources(), settings_blob(image=bytes(image)))
    assert view is not None
    [(page, row)] = view.mismatches
    assert page.title == "Channel 1"
    assert (row.ref_id, row.value, row.memory_value) == ("r_delay", "600", "300")
    assert view.count(MemoryCheck.MISMATCH) == 1


def test_enum_mismatch_names_the_held_value() -> None:
    image = bytearray(_IMAGE)
    image[0] = 1
    view = read_view(_sources(), settings_blob(image=bytes(image)))
    assert view is not None
    row = _rows(view)["r_mode"]
    assert (row.memory, row.memory_value, row.memory_value_text) == (
        MemoryCheck.MISMATCH,
        "dm_A",
        "Mode A",
    )


def test_unknown_image_layout_marks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    junk = b"\x07" * 16
    # With few checked values a miss is still shown as a mismatch...
    view = read_view(_sources(), settings_blob(image=junk))
    assert view is not None
    assert view.image_layout_known
    assert view.count(MemoryCheck.MISMATCH) == 7
    # ...but when most of enough values miss, the layout is taken as unknown.
    monkeypatch.setattr(view_module, "_MIN_LAYOUT_CHECKED", 2)
    unknown = read_view(_sources(), settings_blob(image=junk))
    assert unknown is not None
    assert not unknown.image_layout_known
    assert unknown.count(MemoryCheck.MISMATCH) == 0
    assert unknown.count(MemoryCheck.UNCHECKED) == 7
    assert unknown.count(MemoryCheck.NOT_IN_MEMORY) == 0


def test_view_without_image_checks_nothing() -> None:
    view = read_view(_sources(), settings_blob(image=None))
    assert view is not None
    assert not view.has_image
    assert {r.memory for r in _rows(view).values()} == {MemoryCheck.UNCHECKED}


def test_parameter_without_memory_and_default_branch() -> None:
    view = read_view(_sources(), settings_blob({"r_mode": "dm_Off"}))
    assert view is not None
    rows = _rows(view)
    assert rows["r_name"].memory is MemoryCheck.NOT_IN_MEMORY
    assert "r_fb" not in rows
    # r_fb defaults to dm_No, so the derived value follows it and stays hidden.
    assert "r_derived" not in rows


def test_global_variable_selects_branch() -> None:
    view = read_view(
        _sources(), settings_blob(), globals_={"DeviceOrderNumber": "DEMO-9"}
    )
    assert view is not None
    assert [r.ref_id for r in view.pages[0].rows] == ["r_name", "s_timing", "r_time"]


def test_language_choice() -> None:
    sources = _sources()
    german = read_view(sources, settings_blob(), language="de-AT")
    assert german is not None
    assert german.pages[0].title == "Allgemein"
    assert _rows(german)["r_mode"].value_text == "Betriebsart B"
    other = read_view(sources, settings_blob(), language="fr-FR")
    assert other is not None and other.pages[0].title == "General settings"
    assert pick_language({}, "de-DE") == {}
    assert pick_language({"de-DE": {"a": "b"}}, "de-DE") == {"a": "b"}


@pytest.mark.parametrize(
    ("name", "matches"),
    [("id-DEMO_0001_01", True), ("DEMO000101", True), ("", True), ("id-OTHER", False)],
)
def test_application_name(name: str, matches: bool) -> None:
    view = read_view(_sources(), settings_blob(name=name))
    assert view is not None
    assert view.application_matches is matches
    assert view.stored_application_id == name


def test_read_view_without_settings() -> None:
    assert read_view(_sources(), b"\x00\x01garbage") is None


# --- finding the files -----------------------------------------------------------------------


def test_find_sources_reads_only_xml_baggages() -> None:
    files = {
        "M-DEMO_BG-1-Setup.2Emsi": b"binary",
        "M-DEMO_BG-1-Texts.2Exml": TRANSLATIONS,
        "M-DEMO_BG-1-Demo.2Exml": DESCRIPTION,
        "M-DEMO_BG-1-Empty.2Exml": b"",
    }
    read: list[str] = []

    def reader(baggage_id: str) -> bytes | None:
        read.append(baggage_id)
        return files.get(baggage_id)

    sources = find_sources(files, reader)
    assert sources is not None
    assert "M-DEMO_BG-1-Setup.2Emsi" not in read
    assert set(sources.translations) == {"en-US", "de-DE"}
    assert find_sources(["M-DEMO_BG-1-Texts.2Exml"], files.get) is None


@pytest.mark.parametrize(
    ("baggage_id", "xml"),
    [
        ("A.2Exml", True),
        ("a.xml", True),
        ("A.2EXML", True),
        ("A.2Emsi", False),
        ("A", False),
    ],
)
def test_is_xml_baggage(baggage_id: str, xml: bool) -> None:
    assert is_xml_baggage(baggage_id) is xml

"""The binding of :mod:`xknxeditor.namespaces.binding`: xsdata's result, or a hand-over to xsdata."""

from __future__ import annotations

from typing import Any

import pytest
from xsdata.exceptions import ParserError

from xknxeditor.namespaces import load_xml, parse_progress
from xknxeditor.namespaces.binding import CHUNK, Binder, Unsupported
from xknxeditor.namespaces.files import v20
from xknxeditor.namespaces.schema import _parser  # pyright: ignore[reportPrivateUsage]

_NS = "http://knx.org/xml/project/20"

# Attributes of many kinds (enumerations, numbers, booleans, base64 and hex octets, a list of
# tokens, a date), simple-typed and any-typed elements, choices, lists and text content.
_DOCUMENT = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="{_NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:schemaLocation="{_NS} knx_project_20.xsd" CreatedBy="test" ToolVersion="6.4">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <Catalog>
        <CatalogSection Id="M-00FA_CS-1" Name="Section" Number="1" DefaultLanguage="de-DE">
          <CatalogItem Id="M-00FA_H-1_HP-1_CI-1" Name="Item" Number="1" VisibleDescription="x"
                       ProductRefId="M-00FA_H-1_P-1" Hardware2ProgramRefId="M-00FA_H-1_HP-1"
                       DefaultLanguage="de-DE"/>
        </CatalogSection>
      </Catalog>
      <ApplicationPrograms>
        <ApplicationProgram Id="M-00FA_A-0001-01-0000" ApplicationNumber="1"
            ApplicationVersion="1" ProgramType="ApplicationProgram" MaskVersion="MV-07B0"
            Name="App" LoadProcedureStyle="MergedProcedure" PeiType="0"
            DefaultLanguage="de-DE" DynamicTableManagement="false" Linkable="false"
            MinEtsVersion="5.0" Hash="AAECAw==" IsSecureEnabled="true"
            LastModified="2026-11-25T10:00:00Z" ReplacesVersions="1 2 3">
          <Static>
            <Code>
              <RelativeSegment Id="M-00FA_A-0001-01-0000_RS-04-00000" Name="Parameters"
                  Offset="0" Size="4" LoadStateMachine="4"/>
            </Code>
            <ParameterTypes>
              <ParameterType Id="M-00FA_A-0001-01-0000_PT-Number" Name="Number">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255"/>
              </ParameterType>
              <ParameterType Id="M-00FA_A-0001-01-0000_PT-None" Name="None">
                <TypeNone/>
              </ParameterType>
              <ParameterType Id="M-00FA_A-0001-01-0000_PT-Float" Name="Float">
                <TypeFloat Encoding="IEEE-754 Single" minInclusive="-1.5" maxInclusive="2.25"/>
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="M-00FA_A-0001-01-0000_P-1" Name="P1"
                  ParameterType="M-00FA_A-0001-01-0000_PT-Number" Text="One" Value="3">
                <Memory CodeSegment="M-00FA_A-0001-01-0000_RS-04-00000" Offset="0"
                    BitOffset="0"/>
              </Parameter>
              <Union SizeInBit="8">
                <Memory CodeSegment="M-00FA_A-0001-01-0000_RS-04-00000" Offset="1"
                    BitOffset="0"/>
                <Parameter Id="M-00FA_A-0001-01-0000_UP-2" Name="P2" Offset="0" BitOffset="0"
                    ParameterType="M-00FA_A-0001-01-0000_PT-Number" Text="Two" Value="4"/>
              </Union>
            </Parameters>
            <LoadProcedures>
              <LoadProcedure MergeId="2">
                <LdCtrlWriteProp ObjIdx="0" PropId="78" Verify="false" InlineData="0102AB"/>
              </LoadProcedure>
            </LoadProcedures>
            <Script>function f() {{ return 1; }}</Script>
          </Static>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
""".encode()


def _xsdata(document: bytes) -> Any:
    return _parser.from_bytes(document, v20.Knx)


def test_the_binding_gives_what_xsdata_gives() -> None:
    bound = Binder().parse(_DOCUMENT, v20.Knx)
    assert bound == _xsdata(_DOCUMENT)
    manufacturer = bound.manufacturer_data.manufacturer[0]
    program = manufacturer.application_programs.application_program[0]
    assert program.application_number == 1
    assert program.is_secure_enabled is True
    assert program.hash == bytes([0, 1, 2, 3])
    assert program.replaces_versions == [1, 2, 3]
    types = program.static.parameter_types.parameter_type
    # An element of any type without content: an empty text.
    assert types[1].choice == ""
    assert types[2].choice.min_inclusive == -1.5
    assert program.static.load_procedures.load_procedure[0].choice[
        0
    ].inline_data == bytes([1, 2, 0xAB])


def test_a_converted_value_is_the_same_after_its_cache_starts_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("xknxeditor.namespaces.binding._CACHE_SIZE", 1)
    assert Binder().parse(_DOCUMENT, v20.Knx) == _xsdata(_DOCUMENT)


@pytest.mark.parametrize(
    "document",
    [
        # An element the class does not have.
        _DOCUMENT.replace(b"<Script>", b"<Unknown/><Script>"),
        # A single element twice.
        _DOCUMENT.replace(b"<Script>", b"<Script>1</Script><Script>"),
        # A required attribute missing.
        _DOCUMENT.replace(b' RefId="M-00FA"', b""),
        # A type given by xsi:type.
        _DOCUMENT.replace(
            b"<TypeNone/>",
            b'<TypeNone xmlns:xs="http://www.w3.org/2001/XMLSchema" xsi:type="xs:string"/>',
        ),
        # Attributes on an element of any type.
        _DOCUMENT.replace(b"<TypeNone/>", b'<TypeNone Some="1"/>'),
        # Text between elements.
        _DOCUMENT.replace(b"</Code>", b"</Code>text"),
        # Another root element.
        _DOCUMENT.replace(b"<KNX ", b"<Other ").replace(b"</KNX>", b"</Other>"),
    ],
    ids=[
        "unknown element",
        "repeated element",
        "missing attribute",
        "xsi:type",
        "attributes of an any-typed element",
        "text between elements",
        "other root",
    ],
)
def test_what_the_binding_does_not_cover_goes_to_xsdata(document: bytes) -> None:
    with pytest.raises(Unsupported):
        Binder().parse(document, v20.Knx)
    try:
        expected: Any = _xsdata(document)
    except Exception as exc:  # what xsdata raises, load_xml raises
        with pytest.raises(type(exc)):
            load_xml(document, "20")
    else:
        assert load_xml(document, "20") == expected


def test_an_unknown_element_is_reported_by_xsdata() -> None:
    with pytest.raises(ParserError, match="Unknown property"):
        load_xml(_DOCUMENT.replace(b"<Script>", b"<Unknown/><Script>"), "20")


def test_progress_hears_the_bytes_read() -> None:
    document = _DOCUMENT.replace(
        b"<Script>", b"<!--" + b"x" * (3 * CHUNK) + b"--><Script>"
    )
    heard: list[int] = []
    with parse_progress(heard.append):
        load_xml(document, "20")
    assert heard == sorted(heard)
    assert heard[-1] == len(document)
    assert len(heard) == -(-len(document) // CHUNK)


def test_progress_hears_a_document_xsdata_parses_from_its_start() -> None:
    padding = b"<!--" + b"x" * (3 * CHUNK) + b"-->"
    document = _DOCUMENT.replace(b"<Script>", padding + b"<Script>").replace(
        b"</Static>", b"</Static>text"
    )
    heard: list[int] = []
    with parse_progress(heard.append):
        load_xml(document, "20")
    # The binding read it before it met the text and gave up; xsdata reads it again.
    restart = next(i for i in range(1, len(heard)) if heard[i] < heard[i - 1])
    assert heard[:restart] == [CHUNK, 2 * CHUNK, 3 * CHUNK, len(document)]
    assert heard[restart:] == sorted(heard[restart:])
    assert heard[-1] == len(document)


def test_no_progress_outside_the_block() -> None:
    heard: list[int] = []
    with parse_progress(heard.append):
        pass
    load_xml(_DOCUMENT, "20")
    assert heard == []

"""Text parameters are encoded the way the application's options say: with its own
``TextParameterEncoding``, the project's ``CodePage`` or the system code page."""

from __future__ import annotations

import locale

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.text_encoding import (
    ansi_code_page,
    project_text_encoding,
    system_code_page,
    text_parameter_encoding,
)

_APP = "M-00FA_A-0004-01-0000"
_TEXT = f"{_APP}_P-1_R-1"


def _program(options: str):  # type: ignore[no-untyped-def]
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="4" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Text"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-T" Name="T"><TypeText SizeInBit="32" /></ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Label" ParameterType="{_APP}_PT-T" Text="Label" Value="">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_TEXT}" RefId="{_APP}_P-1" />
            </ParameterRefs>
            {options}
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="Text"><ParameterRefRef RefId="{_TEXT}" /></ParameterBlock>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""
    return parse_application_xml(xml.encode(), "M-00FA")[0].program


@pytest.fixture(autouse=True)
def german_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """A system set to German: its code page is cp1252."""
    monkeypatch.setattr(locale, "getlocale", lambda *_: ("de_DE", "UTF-8"))


def _encoded(options: str, value: str, project_code_page: str | None = None) -> bytes:
    ui = DynamicUI(_program(options), project_code_page=project_code_page)
    ui.ui()
    ui.set_parameter_ref(_TEXT, value)
    return ui.encode_to_memory()[f"{_APP}_AS-0000"]


def test_the_application_encoding_wins_by_default() -> None:
    options = '<Options TextParameterEncoding="utf-8" />'
    assert _encoded(options, "é", "iso-8859-5") == "é".encode() + b"\x00\x00"


def test_without_an_application_encoding_the_system_code_page() -> None:
    assert _encoded("", "€", "iso-8859-5") == b"\x80\x00\x00\x00"


def test_the_project_code_page_when_the_application_asks_for_it() -> None:
    options = '<Options TextParameterEncodingSelector="UseProjectCodePage" />'
    assert (
        _encoded(options, "Жук", "iso-8859-5") == "Жук".encode("iso-8859-5") + b"\x00"
    )
    # A project with the system language
    assert _encoded(options, "€", None) == b"\x80\x00\x00\x00"


def test_the_windows_code_page_when_the_application_asks_for_it() -> None:
    options = (
        '<Options TextParameterEncoding="utf-8" '
        'TextParameterEncodingSelector="UseWindowsAnsiCodePage" />'
    )
    assert _encoded(options, "€", "iso-8859-5") == b"\x80\x00\x00\x00"


def test_values_read_from_a_device_are_decoded_with_the_same_encoding() -> None:
    options = '<Options TextParameterEncodingSelector="UseProjectCodePage" />'
    ui = DynamicUI(_program(options), project_code_page="iso-8859-7")
    ui.ui()
    data = {f"{_APP}_AS-0000": "Γεια".encode("iso-8859-7")}
    assert ui.decode_memory_parameters(data) == {f"{_APP}_P-1": "Γεια"}


def test_a_text_that_does_not_fit_in_its_encoding_is_refused() -> None:
    options = '<Options TextParameterEncoding="utf-8" />'
    ui = DynamicUI(_program(options))
    ui.ui()
    with pytest.raises(ValueError, match="4"):
        ui.edit_parameter(_TEXT, "ééé")  # 6 bytes in UTF-8


@pytest.mark.parametrize(
    ("tag", "code_page"),
    [
        ("de_DE", "cp1252"),
        ("en-US", "cp1252"),
        ("ru_RU", "cp1251"),
        ("pl_PL", "cp1250"),
        ("el_GR", "cp1253"),
        ("tr_TR", "cp1254"),
        ("lt_LT", "cp1257"),
        ("ja_JP", "cp932"),
        ("zh_CN", "cp936"),
        ("zh_TW", "cp950"),
        ("ko_KR", "cp949"),
    ],
)
def test_the_system_code_page_by_language(tag: str, code_page: str) -> None:
    assert ansi_code_page(tag) == code_page


def test_without_a_locale_the_western_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(locale, "getlocale", lambda *_: (None, None))
    assert system_code_page() == "cp1252"


def test_project_code_pages() -> None:
    assert project_text_encoding("iso-8859-15") == "iso-8859-15"
    assert project_text_encoding("us-ascii") == "ascii"
    assert project_text_encoding("utf-8") == "utf-8"
    assert project_text_encoding(None) == "cp1252"
    assert project_text_encoding("klingon") == "cp1252"


def test_an_application_without_options_uses_the_system_code_page() -> None:
    assert text_parameter_encoding(_program(""), "iso-8859-2") == "cp1252"

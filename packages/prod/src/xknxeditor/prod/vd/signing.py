"""Fingerprints and id patching of the converted product data (the converter's last step).

The converter writes every application program with the placeholder fingerprint ``0000`` in its id
(``M-0001_A-7002-01-0000``) and then replaces it by a fingerprint computed from the program's
registration-relevant content. :func:`sign` does the same for the files the conversion produced:

- **Application programs**: the registration-relevant elements and attributes (table below) are
  written into a canonical binary form; children are sorted by their id, by the digest of their
  content or kept in document order, depending on the element. The MD5 digest of it is the
  ``Hash`` attribute of ``ApplicationProgram``, the hex of its first and last byte the fingerprint.
  The fingerprint replaces the placeholder in the program id, in every id-valued attribute of the
  file and in the file name. The digest depends on attribute values and texts only, not on the
  formatting of the file.
- **Hardware**: a ``Hardware2Program`` id becomes ``<hardware id>_HP`` followed by the version parts
  of its (new) application program ids; ``ApplicationProgramRef`` follows the programs. Products
  and ``Hardware2Program`` elements get a ``Hash`` (SHA-1 of the relevant attributes of the
  hardware and their own, for a ``Hardware2Program`` also of the digests of its programs).
- **Catalog**: catalog item ids are rebuilt from the new ``Hardware2Program`` id, the product part of
  ``ProductRefId`` and ``Number``; their translations follow.
- **Cleaning**: ``Hardware2Program`` elements without registration or registered as
  ``Unregistered`` are removed, with the catalog items (and translations) that refer to them, and
  application programs no hardware refers to any more are dropped.

The files are written again as the converter writes them in this step: application programs by an
indenting XML writer, hardware and catalog by a document save (two-space indentation, CRLF line
breaks, UTF-8 with byte order mark, no line break at the end; the two differ in tabs in attribute
values only). Other files are kept as they are. Registration signatures and the signature files
are not produced.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import re
import struct
import xml.parsers.expat
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknxeditor.prod.errors import check_cancelled

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Iterator

__all__ = ["sign"]

# ---------------------------------------------------------------------------------------------
# A small XML tree: attribute order kept, whitespace-only text dropped (the converter reads its
# files ignoring whitespace), empty-element tags remembered.


class _Text:
    __slots__ = ("cdata", "value")

    def __init__(self, value: str, *, cdata: bool = False) -> None:
        self.value = value
        self.cdata = cdata


class _Element:
    __slots__ = ("attrs", "children", "emptied", "empty", "local")

    def __init__(self, name: str, attrs: list[list[str]]) -> None:
        self.local = name.rpartition(":")[2]
        self.attrs = attrs
        self.children: list[_Element | _Text] = []
        self.empty = False
        # All children removed: a document save still writes a start and an end tag.
        self.emptied = False

    def get(self, name: str) -> str | None:
        for key, value in self.attrs:
            if key == name:
                return value
        return None

    def set(self, name: str, value: str) -> None:
        """Replaces the value in place, or appends the attribute."""
        for attr in self.attrs:
            if attr[0] == name:
                attr[1] = value
                return
        self.attrs.append([name, value])

    def elements(self, local: str | None = None) -> Iterator[_Element]:
        for child in self.children:
            if isinstance(child, _Element) and (local is None or child.local == local):
                yield child

    def path(self, *locals_: str) -> list[_Element]:
        """The descendants reached by a path of child names."""
        found: list[_Element] = [self]
        for local in locals_:
            found = [c for el in found for c in el.elements(local)]
        return found

    def iter(self) -> Iterator[_Element]:
        """This element and all descendants in document order."""
        stack: list[_Element] = [self]
        while stack:
            el = stack.pop()
            yield el
            stack.extend(reversed([c for c in el.children if isinstance(c, _Element)]))

    def remove(self, child: _Element) -> None:
        self.children = [c for c in self.children if c is not child]
        self.emptied = not self.children


_XML_WS = " \t\r\n"


def _parse(data: bytes) -> _Element:
    parser = xml.parsers.expat.ParserCreate()
    parser.ordered_attributes = True
    parser.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack: list[tuple[_Element, int]] = []
    roots: list[_Element] = []
    text: list[str] = []
    cdata = [False]

    def flush() -> None:
        if not text:
            return
        value = "".join(text)
        text.clear()
        if stack and (cdata[0] or value.strip(_XML_WS)):
            stack[-1][0].children.append(_Text(value, cdata=cdata[0]))

    def start(name: str, attrs: list[str]) -> None:
        flush()
        el = _Element(name, [[attrs[i], attrs[i + 1]] for i in range(0, len(attrs), 2)])
        (stack[-1][0].children if stack else roots).append(el)
        stack.append((el, parser.CurrentByteIndex))

    def end(_name: str) -> None:
        flush()
        el, index = stack.pop()
        # An empty-element tag reports its start and its end at the same position.
        el.empty = index == parser.CurrentByteIndex

    def chars(data: str) -> None:
        text.append(data)

    def cdata_start() -> None:
        flush()
        cdata[0] = True

    def cdata_end() -> None:
        text.append("")
        flush()
        cdata[0] = False

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.StartCdataSectionHandler = cdata_start
    parser.EndCdataSectionHandler = cdata_end
    parser.Parse(data, True)
    return roots[0]


# ---------------------------------------------------------------------------------------------
# Serialization as the converter writes the files in this step.

_INVALID_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
_NEWLINES = re.compile("\r\n|\r|\n")


def _check_chars(value: str) -> None:
    if _INVALID_CHARS.search(value):
        raise ValueError(f"Invalid XML character in {value!r}")


def _escape_text(value: str) -> str:
    _check_chars(value)
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _NEWLINES.sub("\r\n", value)


def _escape_attr(value: str, *, raw_tab: bool) -> str:
    _check_chars(value)
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    value = value.replace('"', "&quot;").replace("\n", "&#xA;").replace("\r", "&#xD;")
    return value if raw_tab else value.replace("\t", "&#x9;")


class _Writer:
    """An indenting XML writer (two spaces, CRLF) with its rules for mixed content.

    An element is indented unless text was written in its parent before it, or in an ancestor
    before that one started; an end tag is indented if the element has element content.
    ``raw_tab`` keeps tabs in attribute values (a document save does that, the XML writer escapes
    them).
    """

    def __init__(self, *, raw_tab: bool) -> None:
        self._parts: list[str] = ['\ufeff<?xml version="1.0" encoding="utf-8"?>']
        self._pos = len(self._parts[0])
        self._text_pos = -1
        self._content_pos = -1
        self._level = 0
        self._mixed = False
        self._stack: list[bool] = []
        self._raw_tab = raw_tab

    def _write(self, s: str) -> None:
        self._parts.append(s)
        self._pos += len(s)

    def _indent(self) -> None:
        self._write("\r\n" + "  " * self._level)

    def element(self, el: _Element) -> None:
        if not self._mixed and self._text_pos != self._pos:
            self._indent()
        self._level += 1
        self._stack.append(self._mixed)
        self._write("<" + el.local)
        for name, value in el.attrs:
            self._write(f' {name}="{_escape_attr(value, raw_tab=self._raw_tab)}"')
        # The root element does not inherit the state of the document level.
        self._mixed = False if self._level == 1 else self._stack[-1]
        self._write(">")
        self._content_pos = self._pos
        for child in el.children:
            if isinstance(child, _Element):
                self.element(child)
                continue
            self._mixed = True
            if child.cdata:
                _check_chars(child.value)
                body = "]]]]><![CDATA[>".join(child.value.split("]]>"))
                self._write("<![CDATA[" + _NEWLINES.sub("\r\n", body) + "]]>")
            else:
                self._write(_escape_text(child.value))
            self._text_pos = self._pos
        self._level -= 1
        if self._content_pos == self._pos and el.emptied:
            if not self._mixed:
                self._indent()
            self._write(f"</{el.local}>")
        elif self._content_pos == self._pos:
            self._parts[-1] = self._parts[-1][:-1] + " />"
            self._pos += 2
        else:
            if not self._mixed and self._text_pos != self._pos:
                self._indent()
            self._write(f"</{el.local}>")
        self._mixed = self._stack.pop()

    def result(self) -> bytes:
        return "".join(self._parts).encode("utf-8")


def _serialize(root: _Element, *, raw_tab: bool) -> bytes:
    writer = _Writer(raw_tab=raw_tab)
    writer.element(root)
    return writer.result()


# ---------------------------------------------------------------------------------------------
# Culture-aware string order: the hash sorts children by id or by the digest of their content
# with the converter's default string comparer, and the attribute tables are sorted by name with
# it (ids may hold letters like "ü"). Sort keys as the system builds them: the primary weights of
# all characters, then their diacritic and case weights (2 = none, trailing ones left out), and
# hyphen and apostrophe only as "special" weights with their position. Per character (U+0020 to
# U+007E and U+00A0 to U+024F): primary weights in hex, "/" diacritic weights, ":" case weights;
# "*" marks hyphen and apostrophe.
_NLS_TABLE = (
    "0702,071c,071d,071f,0721,0723,0725,*,0727,072a,072d,0803,072f,*,0733,0735,0d03,0d1a,0d1c,"
    "0d1e,0d20,0d22,0d24,0d26,0d28,0d2a,0737,073a,080e,0812,0814,073c,073e,0e02:12,0e09:12,"
    "0e0a:12,0e1a:12,0e21:12,0e23:12,0e25:12,0e2c:12,0e32:12,0e35:12,0e36:12,0e48:12,0e51:12,"
    "0e70:12,0e7c:12,0e7e:12,0e89:12,0e8a:12,0e91:12,0e99:12,0e9f:12,0ea2:12,0ea4:12,0ea6:12,"
    "0ea7:12,0ea9:12,073f,0741,0742,0743,0744,0748,0e02,0e09,0e0a,0e1a,0e21,0e23,0e25,0e2c,0e32,"
    "0e35,0e36,0e48,0e51,0e70,0e7c,0e7e,0e89,0e8a,0e91,0e99,0e9f,0ea2,0ea4,0ea6,0ea7,0ea9,074a,"
    "074c,074e,0750,0704,0751,0797,0798,0799,079a,0752,0a06,0753,0a07,0e02/03:0e,0818,0a08,,0a09,"
    "0754,0a0a,0817,0d1c:0e,0d1e:0e,0755,0a0b,0a0c,0a0d,0756,0d1a:0e,0e7c/03:0e,081a,0d0d,0d11,"
    "0d15,0757,0e02/0f:12,0e02/0e:12,0e02/12:12,0e02/19:12,0e02/13:12,0e02/1a:12,0e020e21:1212,"
    "0e0a/1c:12,0e21/0f:12,0e21/0e:12,0e21/12:12,0e21/13:12,0e32/0f:12,0e32/0e:12,0e32/12:12,"
    "0e32/13:12,0e1a/68:12,0e70/19:12,0e7c/0f:12,0e7c/0e:12,0e7c/12:12,0e7c/19:12,0e7c/13:12,"
    "081c,0e7c/21:12,0e9f/0f:12,0e9f/0e:12,0e9f/12:12,0e9f/13:12,0ea7/0e:12,0e990e2c:1212,"
    "0e910e91,0e02/0f,0e02/0e,0e02/12,0e02/19,0e02/13,0e02/1a,0e020e21,0e0a/1c,0e21/0f,0e21/0e,"
    "0e21/12,0e21/13,0e32/0f,0e32/0e,0e32/12,0e32/13,0e1a/68,0e70/19,0e7c/0f,0e7c/0e,0e7c/12,"
    "0e7c/19,0e7c/13,081d,0e7c/21,0e9f/0f,0e9f/0e,0e9f/12,0e9f/13,0ea7/0e,0e990e2c,0ea7/13,"
    "0e02/17:12,0e02/17,0e02/15:12,0e02/15,0e02/1b:12,0e02/1b,0e0a/0e:12,0e0a/0e,0e0a/12:12,"
    "0e0a/12,0e0a/10:12,0e0a/10,0e0a/14:12,0e0a/14,0e1a/14:12,0e1a/14,0e1a/1e:12,0e1a/1e,"
    "0e21/17:12,0e21/17,0e21/15:12,0e21/15,0e21/10:12,0e21/10,0e21/1b:12,0e21/1b,0e21/14:12,"
    "0e21/14,0e25/12:12,0e25/12,0e25/15:12,0e25/15,0e25/10:12,0e25/10,0e25/1c:12,0e25/1c,"
    "0e2c/12:12,0e2c/12,0e2c/1e:12,0e2c/1e,0e32/19:12,0e32/19,0e32/17:12,0e32/17,0e32/15:12,"
    "0e32/15,0e32/1b:12,0e32/1b,0e32/10:12,0e32/04,0e320e35:1212,0e320e35,0e35/12:12,0e35/12,"
    "0e36/1c:12,0e36/1c,0e36/04,0e48/0e:12,0e48/0e,0e48/1c:12,0e48/1c,0e48/14:12,0e48/14,"
    "0e48/11:12,0e48/11,0e48/1f:12,0e48/1f,0e70/0e:12,0e70/0e,0e70/1c:12,0e70/1c,0e70/14:12,"
    "0e70/14,0e70/48,0e74:12,0e74,0e7c/17:12,0e7c/17,0e7c/15:12,0e7c/15,0e7c/1d:12,0e7c/1d,"
    "0e7c0e21:1212,0e7c0e21,0e8a/0e:12,0e8a/0e,0e8a/1c:12,0e8a/1c,0e8a/14:12,0e8a/14,0e91/0e:12,"
    "0e91/0e,0e91/12:12,0e91/12,0e91/1c:12,0e91/1c,0e91/14:12,0e91/14,0e99/1c:12,0e99/1c,"
    "0e99/14:12,0e99/14,0e9e/1e:12,0e9e/1e,0e9f/19:12,0e9f/19,0e9f/17:12,0e9f/17,0e9f/15:12,"
    "0e9f/15,0e9f/1a:12,0e9f/1a,0e9f/1d:12,0e9f/1d,0e9f/1b:12,0e9f/1b,0ea4/12:12,0ea4/12,"
    "0ea7/12:12,0ea7/12,0ea7/13:12,0ea9/0e:12,0ea9/0e,0ea9/10:12,0ea9/10,0ea9/14:12,0ea9/14,0e96,"
    "0e09/1e,0e09/43:12,0e09/68:12,0e09/68,0e09/87:12,0e09/87,0e0a/7a:12,0e0a/43:12,0e0a/43,"
    "0e1b/04:12,0e1a/43:12,0e1a/22:12,0e1a/22,0e1a/7c,0e21/7d:12,0e21/7e:12,0e22:12,0e23/43:12,"
    "0e23/43,0e25/43:12,0e25/7b:12,0e2d/59,0e32/7b:12,0e32/1e:12,0e36/43:12,0e36/43,0e48/1e,"
    "0e48/20,0e51/7b:12,0e70/43:12,0e70/4b,0e7c/20:12,0e7c/50:12,0e7c/50,0e7c/7c:12,0e7c/7c,"
    "0e7e/43:12,0e7e/43,0e8a:1d,0e91/87:12,0e91/87,0e91/7c:12,0e91/aa,0e99/57,0e99/43:12,0e99/43,"
    "0e99/59:12,0e9f/50:12,0e9f/50,0e9f/7b:12,0ea2/7b:12,0ea7/43:12,0ea7/43,0ea9/1e:12,0ea9/1e,"
    "0eac:12,0eac/7c:12,0eac/7c,0eac/7d,0d1c,0d22:12,0d22,0eb3,0ea4/7b,081e,081f,0820,0758,"
    "0e1a0ea9/0214:1212,0e1a0ea9/0214:12,0e1a0ea9/0214,0e480e35:1212,0e480e35:12,0e480e35,"
    "0e700e35:1212,0e700e35:12,0e700e35,0e02/14:12,0e02/14,0e32/14:12,0e32/14,0e7c/14:12,0e7c/14,"
    "0e9f/14:12,0e9f/14,0e9f/28:12,0e9f/28,0e9f/1f:12,0e9f/1f,0e9f/25:12,0e9f/25,0e9f/20:12,"
    "0e9f/20,0e21/7d,0e02/28:12,0e02/28,0e02/25:12,0e02/25,0e020e21/0217:1212,0e020e21/0217,"
    "0e25/1e:12,0e25/1e,0e25/14:12,0e25/14,0e36/14:12,0e36/14,0e7c/1b:12,0e7c/1b,0e7c/30:12,"
    "0e7c/30,0eac/14:12,0eac/14,0e35/14,0e1a0ea9:1212,0e1a0ea9:12,0e1a0ea9,0e25/0e:12,0e25/0e,"
    "0e2d/59:12,0ea4/7b:12,0e70/0f:12,0e70/0f,0e02/26:12,0e02/26,0e020e21/020e:1212,"
    "0e020e21/020e,0e7c/2d:12,0e7c/2d,0e02/44:12,0e02/44,0e02/46:12,0e02/46,0e21/44:12,0e21/44,"
    "0e21/46:12,0e21/46,0e32/44:12,0e32/44,0e32/46:12,0e32/46,0e7c/44:12,0e7c/44,0e7c/46:12,"
    "0e7c/46,0e8a/44:12,0e8a/44,0e8a/46:12,0e8a/46,0e9f/44:12,0e9f/44,0e9f/46:12,0e9f/46,"
    "0e91/5b:12,0e91/5b,0e99/5b:12,0e99/5b,0e22/97:12,0e22/97,0e2c/14:12,0e2c/14,0e70/4b:12,0e1e,"
    "0e7d:12,0e7d,0ea9/43:12,0ea9/43,0e02/10:12,0e02/10,0e21/1c:12,0e21/1c,0e7c/28:12,0e7c/28,"
    "0e7c/2e:12,0e7c/2e,0e7c/10:12,0e7c/10,0e7c/25:12,0e7c/25,0ea7/17:12,0ea7/17,0e4a/c8,0e72/c8,"
    "0e99/c8,0e35/04,0e1a0e09:12,0e890e7e:12,0e02/1c:12,0e0a/1e:12,0e0a/1e,0e48/1e:12,0e99/65:12,"
    "0e91/5c,0ea9/5c,0eb3/03:12,0eb3/03,0e09/1e:12,0e9f/1e:12,0ea2/7c:12,0e21/1d:12,0e21/1d,"
    "0e35/1d:12,0e35/1d,0e89/45:12,0e89/45,0e8a/1d:12,0e8a/1d,0ea7/1d:12,0ea7/1d"
)
_NLS_SPECIAL = {"-": b"\x82\x12", "'": b"\x80\x12"}


def _load_nls() -> dict[str, tuple[bytes, list[int], list[int]]]:
    """Character -> (primary weights, diacritic weight per weight, case weight per weight)."""
    table: dict[str, tuple[bytes, list[int], list[int]]] = {}
    codes = [*range(0x20, 0x7F), *range(0xA0, 0x250)]
    for code, entry in zip(codes, _NLS_TABLE.split(","), strict=True):
        if entry == "*":
            continue
        primary, _, case = entry.partition(":")
        primary, _, diacritic = primary.partition("/")
        n = len(primary) // 4
        table[chr(code)] = (
            bytes.fromhex(primary),
            [*bytes.fromhex(diacritic), *[2] * (n - len(diacritic) // 2)],
            [*bytes.fromhex(case), *[2] * (n - len(case) // 2)],
        )
    return table


_NLS = _load_nls()


def _sort_key(s: str) -> bytes:
    primary = bytearray()
    diacritic = bytearray()
    case = bytearray()
    special = bytearray()
    count = 0
    for ch in s:
        weight = _NLS_SPECIAL.get(ch)
        if weight is not None:
            special += (0xFFFF - count).to_bytes(2, "big") + weight
            continue
        entry = _NLS.get(ch)
        if entry is None:
            # Not in the table (not expected in ids): after the known characters, by code point.
            entry = (b"\xff" + ord(ch).to_bytes(3, "big"), [2, 2], [2, 2])
        primary += entry[0]
        diacritic += bytes(entry[1])
        case += bytes(entry[2])
        count += len(entry[1])
    return b"%b\x01%b\x01%b\x01\x01%b\x00" % (
        bytes(primary),
        bytes(diacritic).rstrip(b"\x02"),
        bytes(case).rstrip(b"\x02"),
        bytes(special),
    )


# ---------------------------------------------------------------------------------------------
# The registration-relevant content of an application program: element -> kind and attributes.
# Kinds: "el" (children unordered), "ordered" (document order is relevant), "prr" (ordered except
# below LParameters/RParameters), "b64"/"str"/"u32" (text content with a one-letter tag), "ext"
# (baggages of plugins). Attributes: name:short name:type[=default][!] ("!" = sort key). Types:
# S string, A id (fingerprint left out), B bool, U8/U16/U32/I32/I64 integers, D double, V value of
# a parameter (type given by its parameter type; the default names the resolver).

_APP_TABLE = {
    "Manufacturer": "el RefId:R:S!",
    "ApplicationProgram": "el Id:I:A! ApplicationNumber:AN:U16 ApplicationVersion:AV:U8 ProgramType:PrT:S MaskVersion:MV:S LoadProcedureStyle:LPS:S PeiType:PT:U8 DynamicTableManagement:DTM:B Linkable:L:B OriginalManufacturer:OEM:S PreEts4Style:PES:B=0 ConvertedFromPreEts4Data:CVETS:B=0 IPConfig:IP:S=Tool AdditionalAddressesCount:AAC:I32=0 ReplacesVersions:RV:S IsSecureEnabled:ISE:B TunnelingAddressIndices:TAI:S MaxUserEntries:MUE:U16=0 MaxTunnelingUserEntries:MTUE:U16=0 MaxSecurityIndividualAddressEntries:MSIAE:U16=0 MaxSecurityGroupKeyTableEntries:MSGK:U16=0 MaxSecurityP2PKeyTableEntries:MSP2:U16=0",
    "AbsoluteSegment": "el Id:I:A! Address:A:U32 Size:S:U32 UserMemory:UM:B=0",
    "RelativeSegment": "el Id:I:A! Offset:O:U32 Size:S:U32 LoadStateMachine:LSM:U8",
    "Data": "b64 D",
    "Mask": "b64 M",
    "ParameterType": "el Id:I:A! Name:N:S",
    "TypeNumber": "el SizeInBit:S:U8 Type:T:S minInclusive:min:I64 maxInclusive:max:I64 Increment:I:I64=1",
    "TypeFloat": "el Encoding:E:S minInclusive:min:D maxInclusive:max:D Increment:I:D=1",
    "TypeRestriction": "el Base:B:S SizeInBit:S:U32",
    "Enumeration": "el Id:I:A! Value:V:U32 BinaryValue:BV:S",
    "TypeText": "el SizeInBit:S:U32",
    "TypeTime": "el SizeInBit:S:U8 Unit:U:S minInclusive:min:I64 maxInclusive:max:I64",
    "TypeDate": "el Encoding:E:S DisplayTheYear:Y:B=1",
    "TypeIPAddress": "el AddressType:AT:S Version:V:S=IPv4",
    "TypeColor": "el Space:S:S",
    "TypeRawData": "el MaxSize:M:U32",
    "Parameter": "el Id:I:A! ParameterType:PT:A Value:V:V=Parameter DefaultUnionParameter:DEF:B=0 LegacyPatchAlways:LPA:B=0 CustomerAdjustable:CA:B=0 Offset:O:U32=0 BitOffset:BO:U8=0",
    "Memory": "el CodeSegment:C:A! Offset:O:U32 BitOffset:BO:U8",
    "Property": "el ObjectIndex:OI:U8! ObjectType:OT:U16 Occurrence:OC:U8=0 PropertyId:PID:U8 Offset:O:U32 BitOffset:BO:U8",
    "ParameterRef": "el Id:I:A! RefId:R:A Value:V:V=ParameterRef CustomerAdjustable:CA:B=0",
    "ParameterCalculation": "el Id:I:A! Language:L:S RLTransformationFunc:RLF:S=1 RLTransformationParameters:RLP:S=1 LRTransformationFunc:LRF:S=1 LRTransformationParameters:LRP:S=1",
    "ParameterValidation": "el Id:I:A! ValidationFunc:VF:S ValidationParameters:VP:S=0",
    "RLTransformation": "str R",
    "LRTransformation": "str L",
    "ComObjectTable": "el CodeSegment:C:A! Offset:O:U32",
    "ComObject": "el Id:I:A! Number:N:U32 ObjectSize:S:S",
    "ComObjectRef": "el Id:I:A! RefId:R:A ObjectSize:S:S",
    "AddressTable": "el CodeSegment:C:A Offset:O:U32 MaxEntries:ATM:U32",
    "AssociationTable": "el CodeSegment:C:A Offset:O:U32 MaxEntries:ASM:U32",
    "Fixup": "el FunctionRef:F:S CodeSegment:C:A",
    "Offset": "u32 O",
    "LoadProcedure": "el MergeId:M:U8",
    "LdCtrlUnload": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 AppliesTo:AT:S=auto",
    "LdCtrlLoad": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 AppliesTo:AT:S=auto",
    "LdCtrlMaxLength": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 AppliesTo:A:S=auto Size:S:U32",
    "LdCtrlClearCachedObjectTypes": "ordered AppliesTo:A:S=auto",
    "LdCtrlLoadCompleted": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 AppliesTo:AT:S=auto",
    "LdCtrlAbsSegment": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 SegType:ST:U8 Address:A:U16 Size:S:U32 Access:AC:U8 MemType:M:U8 SegFlags:SF:U8 AppliesTo:AT:S=auto",
    "LdCtrlRelSegment": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 Size:S:U32 Mode:M:U8 Fill:F:U8 AppliesTo:AT:S=auto",
    "LdCtrlTaskSegment": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 Address:A:U16 AppliesTo:AT:S=auto",
    "LdCtrlTaskPtr": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 InitPtr:IP:U16 SavePtr:S:U16 SerialPtr:SP:U16 AppliesTo:AT:S=auto",
    "LdCtrlTaskCtrl1": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 Address:A:U16 Count:C:U8 AppliesTo:AT:S=auto",
    "LdCtrlTaskCtrl2": "ordered LsmIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 Callback:C:U16 Address:A:U16 Seg0:S0:U16 Seg1:S1:U16 AppliesTo:AT:S=auto",
    "LdCtrlWriteProp": "ordered ObjIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 PropId:P:U8 StartElement:S:U16=1 Count:C:U16=1 Verify:V:B InlineData:D:S AppliesTo:AT:S=auto",
    "LdCtrlCompareProp": "ordered ObjIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 PropId:P:U8 StartElement:S:U16=1 Count:C:U16=1 InlineData:D:S AppliesTo:AT:S=auto",
    "LdCtrlLoadImageProp": "ordered ObjIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 PropId:P:U8 Count:C:U16=1 StartElement:S:U16=1 AppliesTo:AT:S=auto",
    "LdCtrlInvokeFunctionProp": "ordered ObjIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 PropId:P:U8 InlineData:D:S AppliesTo:AT:S=auto",
    "LdCtrlReadFunctionProp": "ordered ObjIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 PropId:P:U8 AppliesTo:AT:S=auto",
    "LdCtrlWriteMem": "ordered AddressSpace:AS:S=Standard Address:A:U32 Size:S:U32 Verify:V:B InlineData:D:S AppliesTo:AT:S=auto",
    "LdCtrlCompareMem": "ordered AddressSpace:AS:S=Standard Address:A:U32 Size:S:U32 InlineData:D:S AppliesTo:AT:S=auto",
    "LdCtrlLoadImageMem": "ordered AddressSpace:AS:S=Standard Address:A:U32 Size:S:U32 AppliesTo:AT:S=auto",
    "LdCtrlWriteRelMem": "ordered ObjIdx:I:U8 Offset:O:U32 Size:S:U32 Verify:V:B InlineData:D:S AppliesTo:AT:S=auto",
    "LdCtrlCompareRelMem": "ordered ObjIdx:I:U8 Offset:O:U32 Size:S:U32 InlineData:D:S AppliesTo:AT:S=auto",
    "LdCtrlLoadImageRelMem": "ordered ObjIdx:I:U8 Offset:O:U32 Size:S:U32 AppliesTo:AT:S=auto",
    "LdCtrlConnect": "ordered AppliesTo:AT:S=auto",
    "LdCtrlDisconnect": "ordered AppliesTo:AT:S=auto",
    "LdCtrlRestart": "ordered AppliesTo:AT:S=auto",
    "LdCtrlMasterReset": "ordered EraseCode:EC:U8 ChannelNumber:CN:U8 AppliesTo:AT:S=auto",
    "LdCtrlDelay": "ordered MilliSeconds:M:U16 AppliesTo:AT:S=auto",
    "LdCtrlSetControlVariable": "ordered Name:N:S Value:V:B AppliesTo:AT:S=auto",
    "LdCtrlMapError": "ordered LdCtrlFilter:L:U8=0 OriginalError:O:U32 MappedError:M:U32 AppliesTo:AT:S=auto",
    "LdCtrlProgressText": "ordered TextId:T:U32 AppliesTo:AT:S=auto",
    "LdCtrlDeclarePropDesc": "ordered ObjIdx:I:U8 ObjType:T:U16 Occurrence:O:U8=0 PropId:P:U8 PropType:PT:S MaxElements:M:U16 ReadAccess:R:U8 WriteAccess:WA:U8 Writable:W:B AppliesTo:AT:S=auto",
    "LdCtrlClearLCFilterTable": "ordered AppliesTo:AT:S=auto UseFunctionProp:U:B=0",
    "LdCtrlMerge": "ordered MergeId:M:U8",
    "OnError": "el Cause:C:S! Ignore:I:B=0",
    "Script": "str S",
    "SecurityRole": "el Id:I:A! Mask:M:U16",
    "BusInterface": "el Id:I:A! AddressIndex:AI:U16 AccessType:AT:S",
    "Options": "el LineCoupler0912NewProgrammingStyle:L:B=0 NotLoadable:NL:B=0 CustomerAdjustableParameters:CAP:S=0 MaxRoutingApduLength:MPAL:U32=0 MasterResetOnCRCMismatch:MR:B=0 PromptBeforeFullDownload:PBF:B=0 SupportsExtendedMemoryServices:SEMS:B=0 SupportsExtendedPropertyServices:SEPS:B=0 SupportsIpSystemBroadcast:SISB:B=0 LegacyPatchManufacturerIdInTaskSegment:LPMTS:B=0",
    "Extension": "ext B",
    "ParameterBlock": "ordered Id:I:A ParamRefId:R:A",
    "ParameterSeparator": "ordered Id:I:A",
    "ParameterRefRef": "prr RefId:R:A AliasName:AN:S",
    "choose": "ordered ParamRefId:R:A",
    "when": "ordered test:T:S default:D:B=0",
    "BinaryDataRef": "ordered RefId:R:A",
    "ComObjectRefRef": "ordered RefId:R:A",
    "Assign": "ordered TargetParamRefRef:T:A SourceParamRefRef:S:A Value:V:S",
    "Rename": "ordered Id:I:A RefId:R:A",
    "Channel": "ordered Id:I:A Number:N:S",
    "ChannelIndependentBlock": "ordered",
}


@dataclass(frozen=True, slots=True)
class _Attr:
    name: str
    short: str
    type: str
    default: str | None
    sort: bool


@dataclass(frozen=True, slots=True)
class _Info:
    kind: str
    tag: str
    attrs: tuple[_Attr, ...]

    @property
    def sort_attr(self) -> _Attr | None:
        return next((a for a in self.attrs if a.sort), None)


# The registration-relevant attributes of hardware (products and Hardware2Program elements).
_HARDWARE_TABLE = {
    "Hardware": "el Id:I:S SerialNumber:SN:S VersionNumber:VN:U32 IsAccessory:A:B=0 HasIndividualAddress:HIA:B HasApplicationProgram:HAP:B HasApplicationProgram2:HAP2:B=0 IsPowerSupply:PS:B=0 IsChoke:CH:B=0 IsCoupler:CO:B=0 IsPowerLineRepeater:PR:B=0 IsPowerLineSignalFilter:PF:B=0 IsCable:CA:B=0 IsIPEnabled:IP:B=0 IsRFRetransmitter:RFR:B=0 RFDeviceMode:DM:S RuntimeUnidirectional:RU:B=0 OriginalManufacturer:OM:S",
    "Product": "el Id:I:S OrderNumber:O:S",
    "Hardware2Program": "el CheckSums:C:S LoadedImage:L:S",
    "ApplicationProgramRef": "ordered RefId:R:A",
}


def _load_table(spec_table: dict[str, str]) -> dict[str, _Info]:
    table: dict[str, _Info] = {}
    for element, spec in spec_table.items():
        kind, *rest = spec.split(" ")
        if kind in ("b64", "str", "u32", "ext"):
            table[element] = _Info(kind, rest[0], ())
            continue
        attrs: list[_Attr] = []
        for item in rest:
            sort = item.endswith("!")
            name, short, typ = item.rstrip("!").split(":")
            typ, _, default = typ.partition("=")
            attrs.append(_Attr(name, short, typ, default or None, sort))
        attrs.sort(key=lambda a: _sort_key(a.name))
        table[element] = _Info(kind, "", tuple(attrs))
    return table


_APP_INFO = _load_table(_APP_TABLE)
_HARDWARE_INFO = _load_table(_HARDWARE_TABLE)
_NULL = "$<null>$"


# ---------------------------------------------------------------------------------------------
# Value encoding of the canonical form (little endian, strings with a 7-bit length prefix).


def _str(s: str) -> bytes:
    data = s.encode("utf-8")
    n = len(data)
    prefix = bytearray()
    while n >= 0x80:
        prefix.append((n & 0x7F) | 0x80)
        n >>= 7
    prefix.append(n)
    return bytes(prefix) + data


_INT_RE = re.compile(r"[+-]?[0-9]+")
_FLOAT_RE = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")
_INT_FORMATS = {
    "U8": ("<B", 0, 0xFF),
    "U16": ("<H", 0, 0xFFFF),
    "U32": ("<I", 0, 0xFFFFFFFF),
    "I32": ("<i", -(2**31), 2**31 - 1),
    "I64": ("<q", -(2**63), 2**63 - 1),
}


def _to_int(value: str, typ: str) -> bytes:
    fmt, low, high = _INT_FORMATS[typ]
    s = value.strip(_XML_WS)
    if not _INT_RE.fullmatch(s):
        raise ValueError(f"Not an integer: {value!r}")
    n = int(s)
    if not low <= n <= high:
        raise ValueError(f"Integer out of range: {value!r}")
    return struct.pack(fmt, n)


def _to_bool(value: str) -> bool:
    s = value.strip(_XML_WS)
    if s in ("1", "true"):
        return True
    if s in ("0", "false"):
        return False
    raise ValueError(f"Not a boolean: {value!r}")


def _to_double(value: str) -> float:
    s = value.strip(_XML_WS)
    if s in ("INF", "Infinity"):
        return float("inf")
    if s in ("-INF", "-Infinity"):
        return float("-inf")
    if s == "NaN":
        return float("nan")
    if not _FLOAT_RE.fullmatch(s):
        raise ValueError(f"Not a number: {value!r}")
    return float(s)


_OA_EPOCH = datetime.datetime(1899, 12, 30)
_DATE_RE = re.compile(
    r"(-?\d{4,})-(\d\d)-(\d\d)(?:T(\d\d):(\d\d):(\d\d)(\.\d+)?)?(Z|[+-]\d\d:\d\d)?"
)


def _to_oa_date(value: str) -> float:
    m = _DATE_RE.fullmatch(value.strip(_XML_WS))
    if not m:
        raise ValueError(f"Not a date: {value!r}")
    year, month, day, hour, minute, second, fraction, _ = m.groups()
    dt = datetime.datetime(
        int(year),
        int(month),
        int(day),
        int(hour or 0),
        int(minute or 0),
        int(second or 0),
    )
    millis = (dt - _OA_EPOCH) // datetime.timedelta(milliseconds=1) + round(
        float(fraction or 0) * 1000
    )
    if millis < 0:
        frac = -(-millis % 86_400_000)
        if frac:
            millis -= (86_400_000 + frac) * 2
    return millis / 86_400_000


def _attr_bytes(attr: _Attr, value: str | None) -> bytes:
    if not value:
        value = attr.default
    head = _str(attr.short)
    if value is None:
        return head + _str(_NULL)
    match attr.type:
        case "S":
            return head + _str(value)
        case "A":
            # The fingerprint (characters 16 to 20, "-0000") is left out.
            if len(value) < 21:
                raise ValueError(f"Not an application program id: {value!r}")
            return head + _str(value[:16] + value[21:])
        case "B":
            return head + bytes((_to_bool(value),))
        case "D":
            return head + struct.pack("<d", _to_double(value))
        case _:
            return head + _to_int(value, attr.type)


# ---------------------------------------------------------------------------------------------
# The canonical form of an application program. The file is read as a sequence of nodes and
# walked exactly as the converter walks it, also where an element's text is looked for beyond the
# element.

_START, _END, _TEXT = 0, 1, 2


def _tokens(el: _Element, out: list[tuple[int, _Element | None, str]]) -> None:
    out.append((_START, el, ""))
    if el.empty:
        return
    for child in el.children:
        if isinstance(child, _Element):
            _tokens(child, out)
        else:
            out.append((_TEXT, None, child.value))
    out.append((_END, el, ""))


class _Reader:
    __slots__ = ("pos", "tokens")

    def __init__(
        self, tokens: list[tuple[int, _Element | None, str]], pos: int
    ) -> None:
        self.tokens = tokens
        self.pos = pos

    def read(self) -> bool:
        self.pos += 1
        return self.pos < len(self.tokens)

    @property
    def kind(self) -> int:
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else -1

    @property
    def element(self) -> _Element:
        el = self.tokens[self.pos][1]
        assert el is not None
        return el

    @property
    def text(self) -> str:
        return self.tokens[self.pos][2] if self.pos < len(self.tokens) else ""


class _Values:
    """Resolves parameter values by the kind of their parameter type."""

    def __init__(self, baggage: Callable[[str], str | None]) -> None:
        self.param_types: dict[str, str] = {}
        self.converters: dict[str, str] = {}
        self.last_type: str | None = None
        self.baggage = baggage

    def callback(self, element: str, values: dict[str, str | None]) -> None:
        if element == "ParameterType":
            type_id = values["Id"]
            if type_id is None or type_id in self.converters:
                raise ValueError(f"Missing or duplicate parameter type id {type_id!r}")
            self.last_type = type_id
            self.converters[type_id] = "normal"
        elif element in ("TypeFloat", "TypeDate"):
            if self.last_type is None:
                raise ValueError(f"{element} outside a parameter type")
            self.converters[self.last_type] = (
                "double" if element == "TypeFloat" else "date"
            )

    def resolve(
        self, registry: str, value: str | None, values: dict[str, str | None]
    ) -> bytes:
        if registry == "Parameter":
            param_id, type_id = values["Id"], values["ParameterType"]
            if param_id is None or type_id is None:
                raise ValueError("Parameter without id or type")
            self.param_types.setdefault(param_id, type_id)
        else:
            ref = values["RefId"]
            if ref is None:
                raise ValueError("ParameterRef without RefId")
            type_id = self.param_types[ref]
        converter = self.converters[type_id]
        if not value:
            return _str(_NULL)
        if converter == "double":
            return struct.pack("<d", _to_double(value))
        if converter == "date":
            return struct.pack("<d", _to_oa_date(value))
        return _str(value)


def _plugin_present(*values: str | None) -> bool:
    """Whether a plugin of the newer kind is named (not a class id in braces)."""
    return any(v and not (v.startswith("{") and v.endswith("}")) for v in values)


def _decode_id_part(part: str) -> str:
    out = bytearray()
    i = 0
    while i < len(part):
        if part[i] == ".":
            out.append(int(part[i + 1 : i + 3], 16))
            i += 3
        else:
            out += part[i].encode("utf-8")
            i += 1
    return out.decode("utf-8")


def _critical_baggage(ref_id: str | None) -> bool:
    if not ref_id:
        return False
    parts = re.split("[-_]", ref_id.split("_", 1)[1]) if "_" in ref_id else []
    if len(parts) < 3 or parts[0] != "BG":
        raise ValueError(f"Not a baggage id: {ref_id!r}")
    name = _decode_id_part(parts[2]).lower()
    return name.rpartition(".")[2] in ("exe", "dll", "msi") and "." in name


def _node_bytes(
    r: _Reader, info: _Info, parent: str, ctx: _Values
) -> tuple[bytes, str | None, bool]:
    """The element's own part; also its sort key (None: by content) and if order is relevant."""
    el = r.element
    if info.kind in ("b64", "str", "u32"):
        while r.kind != _TEXT and r.read():
            pass
        out = info.tag.encode()
        value = r.text if r.kind == _TEXT else ""
        if value:
            if info.kind == "b64":
                out += base64.b64decode(re.sub(r"[ \t\r\n]", "", value), validate=True)
            else:
                out += _str(value)
        return out, None, False
    if info.kind == "ext":
        if not _plugin_present(
            el.get("EtsUiPlugin"), el.get("EtsDownloadPlugin"), el.get("EtsDataHandler")
        ):
            return b"", None, False
        refs: list[str] = []
        while r.read():
            if r.kind == _START:
                ref = r.element.get("RefId")
                if ref is not None and _critical_baggage(ref):
                    refs.append(ref)
            elif r.kind == _END:
                break
        refs.sort(key=_sort_key)
        text = "".join(ref + h for ref in refs if (h := ctx.baggage(ref)) is not None)
        return info.tag.encode() + (_str(text) if text else b""), None, False
    values: dict[str, str | None] = dict.fromkeys(a.name for a in info.attrs)
    for name, value in el.attrs:
        if name in values:
            values[name] = value
    if el.local in ("ParameterType", "TypeFloat", "TypeDate"):
        ctx.callback(el.local, values)
    out = bytearray()
    for attr in info.attrs:
        if attr.type == "V":
            assert attr.default is not None
            out += _str(attr.short) + ctx.resolve(
                attr.default, values[attr.name] or None, values
            )
        else:
            out += _attr_bytes(attr, values[attr.name])
    ordered = info.kind == "ordered" or (
        info.kind == "prr" and parent not in ("LParameters", "RParameters")
    )
    if ordered:
        return bytes(out), None, True
    sort = info.sort_attr
    return bytes(out), (values[sort.name] if sort else None), False


def _generate(
    r: _Reader, parent: str, ctx: _Values
) -> tuple[bytes, int | str | None, bool]:
    """The canonical form of the element at the reader, its sort key and if order is relevant."""
    el = r.element
    out = bytearray()
    key: str | None = None
    ordered = False
    info = _APP_INFO.get(el.local)
    if info is not None:
        own, key, ordered = _node_bytes(r, info, parent, ctx)
        out += own
    children: list[tuple[int | str, bytes]] = []
    if not el.empty:
        while r.read():
            if r.kind == _START:
                data, child_key, child_ordered = _generate(r, el.local, ctx)
                k = len(children) if child_ordered else child_key
                if k is not None:
                    children.append((k, data))
            elif r.kind == _END:
                break
    if children:
        if len({type(k) for k, _ in children}) > 1:
            raise ValueError(f"Ordered and unordered children in {el.local}")
        keys = [k if isinstance(k, int) else _sort_key(k) for k, _ in children]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Duplicate child in {el.local}")
        for _, data in sorted(
            zip(keys, (d for _, d in children), strict=True), key=lambda kd: kd[0]
        ):
            out += data
    data = bytes(out)
    if key is None and data:
        key = base64.b64encode(hashlib.md5(data).digest()).decode()
    return data, key, ordered


def _application_hash(
    root: _Element, baggage: Callable[[str], str | None]
) -> tuple[str, bytes]:
    """The id and the digest of the (first) application program."""
    tokens: list[tuple[int, _Element | None, str]] = []
    _tokens(root, tokens)
    for pos, (kind, el, _) in enumerate(tokens):
        if kind == _START and el is not None and el.local == "ApplicationProgram":
            app_id = el.get("Id")
            if app_id is None:
                raise ValueError("ApplicationProgram without Id")
            data, _, _ = _generate(
                _Reader(tokens, pos), "ApplicationPrograms", _Values(baggage)
            )
            return app_id, hashlib.md5(data).digest()
    raise ValueError("No ApplicationProgram")


# Attributes holding ids of the application program (prefixed with its id), per element.
_IDS_TO_PATCH: dict[str, frozenset[str]] = {
    name: frozenset(attrs.split())
    for name, attrs in {
        "ApplicationProgram": "Id",
        "AbsoluteSegment": "Id",
        "RelativeSegment": "Id",
        "ParameterType": "Id ValidationErrorRef",
        "Enumeration": "Id",
        "Parameter": "Id ParameterType",
        "Memory": "CodeSegment",
        "ParameterRef": "Id RefId TextParameterRefId",
        "ParameterCalculation": "Id",
        "ParameterValidation": "Id",
        "ParameterRefRef": "RefId",
        "ComObjectTable": "CodeSegment",
        "ComObject": "Id",
        "ComObjectRef": "Id RefId TextParameterRefId",
        "AddressTable": "CodeSegment",
        "AssociationTable": "CodeSegment",
        "Fixup": "CodeSegment",
        "BinaryData": "Id",
        "ExcludeMemory": "CodeSegment",
        "Message": "Id",
        "SecurityRole": "Id",
        "BusInterface": "Id",
        "ParameterSeparator": "Id TextParameterRefId",
        "choose": "ParamRefId",
        "BinaryDataRef": "RefId",
        "Assign": "TargetParamRefRef SourceParamRefRef",
        "Channel": "Id TextParameterRefId",
        "ComObjectRefRef": "RefId",
        "ParameterBlock": "Id ParamRefId TextParameterRefId",
        "Rename": "Id RefId",
        "Row": "Id TextParameterRefId",
        "Column": "Id TextParameterRefId",
        "Button": "Id TextParameterRefId",
        "TranslationUnit": "RefId",
        "TranslationElement": "RefId",
        "OnError": "MessageRef",
    }.items()
}


def _with_fingerprint(value: str, fingerprint: str) -> str:
    # The id of the program is "M-XXXX_A-NNNN-VV-FFFF": the fingerprint is at 17..20.
    if len(value) < 21:
        raise ValueError(f"Not an application program id: {value!r}")
    return value[:17] + fingerprint + value[21:]


def _patch_application(root: _Element, fingerprint: str, digest: str) -> None:
    for el in root.iter():
        if el.local == "KNX":
            continue
        patch = _IDS_TO_PATCH.get(el.local)
        if patch:
            for attr in el.attrs:
                if attr[0] in patch:
                    attr[1] = _with_fingerprint(attr[1], fingerprint)
        if el.local == "ApplicationProgram":
            el.set("Hash", digest)


# ---------------------------------------------------------------------------------------------
# Hardware, catalog and the removal of unregistered elements.

_APP_FILE = re.compile(r"(M-[0-9A-Fa-f]{4})_A-[^/]*\.xml")


def _values_bytes(
    el: _Element, info: _Info, override: dict[str, str] | None = None
) -> bytes:
    values: dict[str, str | None] = dict.fromkeys(a.name for a in info.attrs)
    for name, value in el.attrs:
        if name in values:
            values[name] = value
    values |= override or {}
    return b"".join(_attr_bytes(a, values[a.name]) for a in info.attrs)


def _sha1(data: bytes) -> str:
    return base64.b64encode(hashlib.sha1(data).digest()).decode()


def _patch_hardware(
    root: _Element, app_ids: dict[str, str], app_hashes: dict[str, str]
) -> dict[str, str]:
    """New Hardware2Program ids, program references and the ``Hash`` of products and
    Hardware2Program elements; returns old -> new Hardware2Program id.

    A product hash covers the relevant attributes of its hardware and its own; a Hardware2Program
    hash also the digests of its application programs and the program references.
    """
    h2p_ids: dict[str, str] = {}
    hashes: dict[int, str] = {}
    for manufacturer in root.path("ManufacturerData", "Manufacturer")[:1]:
        for hardware in manufacturer.path("Hardware", "Hardware"):
            hw_id = hardware.get("Id") or ""
            hw_part = _values_bytes(hardware, _HARDWARE_INFO["Hardware"])
            for el in hardware.iter():
                if el.local == "Product":
                    if el.get("Id"):
                        hashes[id(el)] = _sha1(
                            hw_part + _values_bytes(el, _HARDWARE_INFO["Product"])
                        )
                    continue
                if el.local != "Hardware2Program":
                    continue
                old = el.get("Id")
                if old is None:
                    raise ValueError(f"Hardware2Program without Id in {hw_id}")
                new = hw_id + "_HP"
                digests = b""
                own = _values_bytes(el, _HARDWARE_INFO["Hardware2Program"])
                for ref in el.elements("ApplicationProgramRef"):
                    app_id = app_ids[ref.get("RefId") or ""]
                    new += app_id[8:]
                    digests += app_hashes[app_id].encode("utf-16-le")
                    own += _values_bytes(
                        ref, _HARDWARE_INFO["ApplicationProgramRef"], {"RefId": app_id}
                    )
                if old in h2p_ids:
                    raise ValueError(f"Duplicate Hardware2Program id {old}")
                h2p_ids[old] = new
                hashes[id(el)] = _sha1(hw_part + digests + own)
    for el in root.iter():
        if el.local == "Hardware2Program":
            el.set("Id", h2p_ids[el.get("Id") or ""])
        elif el.local == "ApplicationProgramRef":
            el.set("RefId", app_ids[el.get("RefId") or ""])
        if id(el) in hashes:
            el.set("Hash", hashes[id(el)])
    return h2p_ids


def _patch_catalog(root: _Element, h2p_ids: dict[str, str]) -> None:
    item_ids: dict[str, str] = {}
    for el in root.iter():
        if el.local == "CatalogItem":
            h2p = h2p_ids[el.get("Hardware2ProgramRefId") or ""]
            product = (el.get("ProductRefId") or " _P- ").split("_P-")[1]
            new = f"{h2p}_CI-{product}-{el.get('Number') or ''}"
            for attr in el.attrs:
                if attr[0] == "Id":
                    item_ids.setdefault(attr[1], new)
                    attr[1] = new
                elif attr[0] == "Hardware2ProgramRefId":
                    attr[1] = h2p
        elif el.local in ("TranslationUnit", "TranslationElement"):
            for attr in el.attrs:
                if attr[0] == "RefId" and attr[1] in item_ids:
                    attr[1] = item_ids[attr[1]]


def _unregistered(el: _Element) -> bool:
    info = next(el.elements("RegistrationInfo"), None)
    return info is None or info.get("RegistrationStatus") == "Unregistered"


def _clean_hardware(root: _Element) -> list[str]:
    """Removes unregistered Hardware2Program elements; returns their ids."""
    removed: list[str] = []
    containers = root.path(
        "ManufacturerData", "Manufacturer", "Hardware", "Hardware", "Hardware2Programs"
    )
    for container in containers:
        for h2p in list(container.elements("Hardware2Program")):
            if _unregistered(h2p):
                removed.append(h2p.get("Id") or "")
                container.remove(h2p)
    for hardware in root.path(
        "ManufacturerData", "Manufacturer", "Hardware", "Hardware"
    ):
        for container in list(hardware.elements("Hardware2Programs")):
            if not container.children:
                hardware.remove(container)
    return removed


def _clean_catalog(root: _Element, removed_h2p: list[str]) -> None:
    for h2p in removed_h2p:
        for catalog in root.path("ManufacturerData", "Manufacturer", "Catalog"):
            for section in list(catalog.elements("CatalogSection")):
                for parent in list(section.iter()):
                    for item in list(parent.elements("CatalogItem")):
                        if item.get("Hardware2ProgramRefId") == h2p:
                            parent.remove(item)
                            _remove_translations(root, item.get("Id") or "")


def _remove_translations(root: _Element, ref_id: str) -> None:
    manufacturers = root.path("ManufacturerData", "Manufacturer")
    for languages in [lang for m in manufacturers for lang in m.elements("Languages")]:
        for language in languages.elements("Language"):
            for unit in list(language.elements("TranslationUnit")):
                if unit.get("RefId") == ref_id:
                    language.remove(unit)
            if not language.children:
                languages.remove(language)
    first = next(
        (lang for m in manufacturers for lang in m.elements("Languages")), None
    )
    if first is not None and not first.children:
        for m in manufacturers:
            m.remove(first)


def _referenced_programs(root: _Element) -> set[str]:
    path = (
        "ManufacturerData",
        "Manufacturer",
        "Hardware",
        "Hardware",
        "Hardware2Programs",
    )
    return {
        ref.get("RefId") or ""
        for h2p in root.path(*path, "Hardware2Program")
        for ref in h2p.elements("ApplicationProgramRef")
    }


def _baggage_lookup(files: dict[str, bytes]) -> Callable[[str], str | None]:
    """The integrity value of a baggage of a plugin (only asked for programs with such plugins)."""
    cache: dict[str, str] = {}

    def lookup(ref_id: str) -> str | None:
        if not cache:
            for path, data in files.items():
                directory, _, name = path.partition("/")
                if name != "Baggages.xml":
                    continue
                for baggage in _parse(data).iter():
                    if baggage.local != "Baggage" or baggage.get("Id") is None:
                        continue
                    target = "/".join(
                        p for p in (baggage.get("TargetPath") or "").split("\\") if p
                    )
                    file = f"{directory}/Baggages/{target + '/' if target else ''}{baggage.get('Name')}"
                    content = files.get(file)
                    if content is None:
                        continue
                    digest = hashlib.md5(content).digest()
                    folded = bytes(
                        digest[i] ^ digest[i + 4] ^ digest[i + 8] ^ digest[i + 12]
                        for i in range(4)
                    )
                    cache[baggage.get("Id") or ""] = folded.hex().upper()
            cache.setdefault("", "")
        return cache.get(ref_id)

    return lookup


def sign(
    files: dict[str, bytes], *, cancel: threading.Event | None = None
) -> dict[str, bytes]:
    """Applies fingerprints, patches ids and removes unregistered elements, as the converter does.

    ``files`` maps package paths (``M-XXXX/Hardware.xml``, ``M-XXXX/M-XXXX_A-NNNN-VV-0000.xml``, …)
    to the bytes the conversion wrote. Returns the files after this step: application programs
    under their new names, hardware and catalog patched; other files unchanged. Setting
    ``cancel`` stops it with :class:`ImportCancelled`.
    """
    out = dict(files)
    baggage = _baggage_lookup(files)
    programs: dict[str, dict[str, tuple[str, _Element]]] = {}
    for path in sorted(files):
        directory, _, name = path.partition("/")
        m = _APP_FILE.fullmatch(name)
        if m and "/" not in name:
            check_cancelled(cancel)
            programs.setdefault(m.group(1), {})[path] = (directory, _parse(files[path]))
    app_ids_of: dict[str, dict[str, str]] = {}
    app_hashes: dict[str, str] = {}
    for manufacturer, apps in programs.items():
        app_ids = app_ids_of.setdefault(manufacturer, {})
        for path, (directory, root) in apps.items():
            check_cancelled(cancel)
            old_id, digest = _application_hash(root, baggage)
            fingerprint = f"{digest[0]:02X}{digest[-1]:02X}"
            new_id = _with_fingerprint(old_id, fingerprint)
            if old_id in app_ids:
                raise ValueError(f"Duplicate application program {old_id}")
            app_ids[old_id] = new_id
            app_hashes[new_id] = base64.b64encode(digest).decode()
            _patch_application(root, fingerprint, app_hashes[new_id])
            del out[path]
            out[f"{directory}/{new_id}.xml"] = _serialize(root, raw_tab=False)
    for directory in sorted({p.partition("/")[0] for p in files if "/" in p}):
        hardware_path = f"{directory}/Hardware.xml"
        if hardware_path not in files:
            continue
        hardware = _parse(files[hardware_path])
        h2p_ids = _patch_hardware(hardware, app_ids_of.get(directory, {}), app_hashes)
        removed = _clean_hardware(hardware)
        out[hardware_path] = _serialize(hardware, raw_tab=True)
        catalog_path = f"{directory}/Catalog.xml"
        if catalog_path in files:
            catalog = _parse(files[catalog_path])
            _patch_catalog(catalog, h2p_ids)
            _clean_catalog(catalog, removed)
            out[catalog_path] = _serialize(catalog, raw_tab=True)
        referenced = _referenced_programs(hardware)
        for path in [p for p in out if p.startswith(directory + "/")]:
            name = path[len(directory) + 1 :]
            if (
                "/" not in name
                and name[:2].upper() == "M-"
                and name.rpartition(".")[0] not in referenced
            ):
                del out[path]
    return out

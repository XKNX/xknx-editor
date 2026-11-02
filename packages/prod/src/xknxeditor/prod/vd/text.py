"""Texts of the old database: languages (LCIDs), code pages, and the conversion to XML strings.

The dump is written in code page 1252; a text in another language holds the bytes of that
language's ANSI code page. The converter re-encodes such a text with code page 1252 and decodes it
with the language's code page, then replaces characters XML cannot hold by spaces.
"""

from __future__ import annotations

import codecs
import functools

from . import codepages

# LCID -> (culture name, ANSI code page)
CULTURES: dict[int, tuple[str, int]] = {
    1025: ("ar-SA", 1256),
    1026: ("bg-BG", 1251),
    1027: ("ca-ES", 1252),
    1028: ("zh-TW", 950),
    1029: ("cs-CZ", 1250),
    1030: ("da-DK", 1252),
    1031: ("de-DE", 1252),
    1032: ("el-GR", 1253),
    1033: ("en-US", 1252),
    1034: ("es-ES", 1252),
    1035: ("fi-FI", 1252),
    1036: ("fr-FR", 1252),
    1037: ("he-IL", 1255),
    1038: ("hu-HU", 1250),
    1039: ("is-IS", 1252),
    1040: ("it-IT", 1252),
    1041: ("ja-JP", 932),
    1042: ("ko-KR", 949),
    1043: ("nl-NL", 1252),
    1044: ("nb-NO", 1252),
    1045: ("pl-PL", 1250),
    1046: ("pt-BR", 1252),
    1048: ("ro-RO", 1250),
    1049: ("ru-RU", 1251),
    1050: ("hr-HR", 1250),
    1051: ("sk-SK", 1250),
    1052: ("sq-AL", 1250),
    1053: ("sv-SE", 1252),
    1054: ("th-TH", 874),
    1055: ("tr-TR", 1254),
    1057: ("id-ID", 1252),
    1058: ("uk-UA", 1251),
    1059: ("be-BY", 1251),
    1060: ("sl-SI", 1250),
    1061: ("et-EE", 1257),
    1062: ("lv-LV", 1257),
    1063: ("lt-LT", 1257),
    1066: ("vi-VN", 1258),
    1071: ("mk-MK", 1251),
    1081: ("hi-IN", 0),
    1086: ("ms-MY", 1252),
    1087: ("kk-KZ", 1251),
    2052: ("zh-CN", 936),
    2055: ("de-CH", 1252),
    2057: ("en-GB", 1252),
    2058: ("es-MX", 1252),
    2060: ("fr-BE", 1252),
    2064: ("it-CH", 1252),
    2067: ("nl-BE", 1252),
    2068: ("nn-NO", 1252),
    2070: ("pt-PT", 1252),
    2074: ("sr-Latn-CS", 1250),
    3076: ("zh-HK", 950),
    3079: ("de-AT", 1252),
    3081: ("en-AU", 1252),
    3082: ("es-ES", 1252),
    3084: ("fr-CA", 1252),
    3098: ("sr-Cyrl-CS", 1251),
    4100: ("zh-SG", 936),
    4103: ("de-LU", 1252),
    4105: ("en-CA", 1252),
    4108: ("fr-CH", 1252),
    5127: ("de-LI", 1252),
    5129: ("en-NZ", 1252),
    5132: ("fr-LU", 1252),
    6153: ("en-IE", 1252),
}

# neutral cultures (LCID = primary language id): the language part of the specific culture
for _lcid, (_name, _cp) in list(CULTURES.items()):
    CULTURES.setdefault(_lcid & 0x3FF, (_name.split("-")[0], _cp))
CULTURES[4] = ("zh-Hans", 936)

_BY_NAME = {name.lower(): (lcid, cp) for lcid, (name, cp) in CULTURES.items()}
_BY_NAME["es-es"] = (1034, 1252)


def culture_name(lcid: int) -> str | None:
    entry = CULTURES.get(lcid)
    return entry[0] if entry else None


def lcid_of(language: str) -> int:
    entry = _BY_NAME.get(language.lower())
    return entry[0] if entry else 0


def code_page(language: str) -> int:
    """The ANSI code page of a culture name (1252 for unknown cultures)."""
    entry = _BY_NAME.get(language.lower())
    if entry is not None and entry[1]:
        return entry[1]
    return 1252


def two_letter(language: str) -> str:
    return language.split("-", 1)[0].lower()


def _lenient(error: UnicodeError) -> tuple[str, int]:
    # Bytes a code page leaves undefined decode to the code point of the same value.
    if isinstance(error, UnicodeDecodeError):
        return chr(error.object[error.start]), error.start + 1
    if isinstance(error, UnicodeEncodeError):
        ch = error.object[error.start]
        return (chr(ord(ch)) if ord(ch) < 256 else "?"), error.start + 1
    raise error


codecs.register_error("vd-lenient", _lenient)


def _encode_1252(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            out.append(ord(ch) if ord(ch) < 256 else 0x3F)
    return bytes(out)


def _codec(cp: int) -> str:
    return "gbk" if cp == 936 else f"cp{cp}"


@functools.cache
def _single_table(cp: int) -> str:
    """The 256 characters of the single bytes of ``cp`` as the converter decodes them."""
    undefined = codepages.SINGLE[cp]
    chars: list[str] = []
    for b in range(256):
        if b in undefined:
            chars.append(chr(undefined[b]))
        else:
            chars.append(bytes([b]).decode(_codec(cp)))
    return "".join(chars)


@functools.cache
def _translation(cp: int) -> dict[int, str]:
    """The characters of the bytes (as code points of Latin-1) of a single-byte code page."""
    return dict(enumerate(_single_table(cp)))


@functools.cache
def _pair_table(cp: int) -> dict[int, str]:
    out: dict[int, str] = {}
    for first, count, code in codepages.PAIRS[cp]:
        for k in range(count):
            out[first + k] = chr(code + k)
    return out


@functools.cache
def _lead_bytes(cp: int) -> frozenset[int]:
    return frozenset(
        b for low, high in codepages.LEAD_BYTES[cp] for b in range(low, high + 1)
    )


def _decode_dbcs(data: bytes, cp: int) -> str:
    single = _single_table(cp)
    pairs = _pair_table(cp)
    leads = _lead_bytes(cp)
    replacement = codepages.REPLACEMENT[cp]
    out: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b in leads and i + 1 < n:
            # a lead byte always takes the next byte, also where the pair is undefined
            key = (b << 8) | data[i + 1]
            ch = pairs.get(key)
            if ch is None:
                try:
                    ch = data[i : i + 2].decode(_codec(cp))
                except UnicodeDecodeError:
                    ch = replacement
                if len(ch) != 1:
                    ch = replacement
            out.append(ch)
            i += 2
        else:
            out.append(single[b])
            i += 1
    return "".join(out)


def _decode(data: bytes, cp: int) -> str:
    """``data`` decoded with code page ``cp`` as the converter's runtime does it (the same on every
    OS: undefined bytes and sequences come from :mod:`.codepages`)."""
    if cp in codepages.LEAD_BYTES:
        return _decode_dbcs(data, cp)
    if cp in codepages.SINGLE:
        return data.decode("latin-1").translate(_translation(cp))
    name = "cp1252" if cp == 1252 else f"cp{cp}"
    try:
        return data.decode(name, errors="vd-lenient")
    except LookupError:
        return data.decode("cp1252", errors="vd-lenient")


def is_valid_xml_char(ch: str) -> bool:
    c = ord(ch)
    if c < 0x20:
        return c in (0x9, 0xA, 0xD)
    return c <= 0xD7FF or 0xE000 <= c <= 0xFFFD


def strip_invalid(text: str) -> str:
    """Replace characters XML cannot hold by spaces."""
    if all(is_valid_xml_char(ch) for ch in text):
        return text
    return "".join(ch if is_valid_xml_char(ch) else " " for ch in text)


def from_db(language: str | None, text: str | None) -> str:
    """A text of the database as XML string in ``language`` (``None``: keep code page 1252)."""
    if not text:
        return "" if text is None else text
    cp = 1252 if language is None else code_page(language)
    if cp != 1252:
        text = _decode(_encode_1252(text), cp)
    return strip_invalid(text)

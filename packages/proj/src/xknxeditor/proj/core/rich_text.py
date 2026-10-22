"""Comments and installation hints: rich text with bold, italic and underline, stored as RTF.

Project files keep these texts as RTF the way a WPF RichTextBox saves it (``\\htmautsp`` header,
one ``{\\f2 ...\\par}`` group per paragraph, ``{\\b\\ltrch text}`` per formatted run). The reader
takes any RTF (or plain text, as older files and other tools write it) and keeps the paragraphs and
the bold/italic/underline runs; the writer produces that same RTF, so an edited comment looks as if
the other tool had written it.
"""

from __future__ import annotations

import codecs
import contextlib
from dataclasses import dataclass

__all__ = [
    "RichText",
    "Span",
    "parse_rich_text",
    "plain_text",
    "rich_text_rtf",
]


@dataclass(frozen=True)
class Span:
    """A piece of text with one formatting."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False

    def same_format(self, other: Span) -> bool:
        return (self.bold, self.italic, self.underline) == (
            other.bold,
            other.italic,
            other.underline,
        )


# Paragraphs of formatted spans; an empty paragraph is an empty line.
RichText = tuple[tuple[Span, ...], ...]

# Groups whose text is not part of the document (font and color tables, metadata, pictures).
_SKIPPED_DESTINATIONS = frozenset(
    {
        "fonttbl",
        "colortbl",
        "stylesheet",
        "info",
        "pict",
        "header",
        "footer",
        "listtable",
        "listoverridetable",
        "rsidtbl",
        "generator",
        "xmlnstbl",
        "themedata",
        "colorschememapping",
        "latentstyles",
        "datastore",
        "fldinst",
        "object",
    }
)
_SYMBOLS = {
    "tab": "\t",
    "emdash": "\u2014",
    "endash": "\u2013",
    "lquote": "\u2018",
    "rquote": "\u2019",
    "ldblquote": "\u201c",
    "rdblquote": "\u201d",
    "bullet": "\u2022",
    "emspace": "\u2003",
    "enspace": "\u2002",
    "~": "\u00a0",
    "_": "\u2011",
}


@dataclass
class _State:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    skip: bool = False
    uc: int = 1

    def copy(self) -> _State:
        return _State(self.bold, self.italic, self.underline, self.skip, self.uc)


class _Builder:
    def __init__(self) -> None:
        self.paragraphs: list[list[Span]] = [[]]

    def add(self, text: str, state: _State) -> None:
        if not text or state.skip:
            return
        span = Span(text, state.bold, state.italic, state.underline)
        runs = self.paragraphs[-1]
        if runs and runs[-1].same_format(span):
            runs[-1] = Span(
                runs[-1].text + text, span.bold, span.italic, span.underline
            )
        else:
            runs.append(span)

    def paragraph(self) -> None:
        self.paragraphs.append([])


def parse_rich_text(value: str) -> RichText:
    """The paragraphs and formatted runs of a stored comment: RTF, or plain text (one paragraph
    per line)."""
    if not value:
        return ()
    if not value.lstrip().startswith("{\\rtf"):
        return tuple((Span(line),) if line else () for line in value.splitlines())
    paragraphs = _parse_rtf(value)
    # The closing \par of the last paragraph does not start another one.
    if paragraphs and not paragraphs[-1]:
        paragraphs = paragraphs[:-1]
    if len(paragraphs) == 1 and not paragraphs[0]:
        return ()
    return tuple(
        tuple(Span(_join_surrogates(s.text), s.bold, s.italic, s.underline) for s in p)
        for p in paragraphs
    )


def _join_surrogates(text: str) -> str:
    """Characters beyond the BMP come as two ``\\u`` surrogates."""
    if not any("\ud800" <= ch <= "\udfff" for ch in text):
        return text
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _parse_rtf(value: str) -> list[list[Span]]:
    codepage = "cp1252"
    builder = _Builder()
    stack: list[_State] = []
    state = _State()
    pending_bytes = bytearray()
    skip_chars = 0
    i, n = 0, len(value)

    def flush_bytes() -> None:
        if pending_bytes:
            builder.add(codecs.decode(bytes(pending_bytes), codepage, "replace"), state)
            pending_bytes.clear()

    while i < n:
        ch = value[i]
        if ch == "{":
            flush_bytes()
            stack.append(state.copy())
            i += 1
            if value.startswith("\\*", i):
                state.skip = True
            continue
        if ch == "}":
            flush_bytes()
            state = stack.pop() if stack else _State()
            i += 1
            continue
        if ch in "\r\n":
            i += 1
            continue
        if ch != "\\":
            start = i
            while i < n and value[i] not in "\\{}\r\n":
                i += 1
            text = value[start:i]
            if skip_chars:
                dropped = min(skip_chars, len(text))
                text, skip_chars = text[dropped:], skip_chars - dropped
            flush_bytes()
            builder.add(text, state)
            continue
        # A control word or symbol.
        i += 1
        if i >= n:
            break
        ch = value[i]
        if ch == "'":
            hex_digits = value[i + 1 : i + 3]
            i += 3
            if skip_chars:
                skip_chars -= 1
                continue
            with contextlib.suppress(ValueError):
                pending_bytes.append(int(hex_digits, 16))
            continue
        if not ch.isalpha():
            i += 1
            if skip_chars and ch in "\\{}":
                skip_chars -= 1
                continue
            flush_bytes()
            if ch in "\\{}":
                builder.add(ch, state)
            elif ch in _SYMBOLS:
                builder.add(_SYMBOLS[ch], state)
            elif ch == "*":
                state.skip = True
            continue
        start = i
        while i < n and value[i].isalpha():
            i += 1
        word = value[start:i]
        param_start = i
        if i < n and value[i] == "-":
            i += 1
        while i < n and value[i].isdigit():
            i += 1
        param = value[param_start:i]
        if i < n and value[i] == " ":
            i += 1
        number = int(param) if param not in ("", "-") else None
        if word == "u" and number is not None:
            flush_bytes()
            builder.add(_unicode_char(number), state)
            skip_chars = state.uc
            continue
        flush_bytes()
        _control_word(word, number, state, builder)
        if word == "ansicpg" and number is not None:
            codepage = f"cp{number}"
            try:
                codecs.lookup(codepage)
            except LookupError:
                codepage = "cp1252"
    flush_bytes()
    return builder.paragraphs


def _unicode_char(number: int) -> str:
    code = number + 0x10000 if number < 0 else number
    try:
        return chr(code)
    except ValueError:
        return "\ufffd"


def _control_word(
    word: str, number: int | None, state: _State, builder: _Builder
) -> None:
    on = number != 0
    if word in _SKIPPED_DESTINATIONS:
        state.skip = True
    elif word == "b":
        state.bold = on
    elif word == "i":
        state.italic = on
    elif word in ("ul", "uld", "uldb", "ulw", "ulth", "uldash"):
        state.underline = on
    elif word in ("ulnone", "ul0"):
        state.underline = False
    elif word == "plain":
        state.bold = state.italic = state.underline = False
    elif word == "uc" and number is not None:
        state.uc = max(number, 0)
    elif word in ("par", "line"):
        if not state.skip:
            builder.paragraph()
    elif word in _SYMBOLS:
        builder.add(_SYMBOLS[word], state)


def plain_text(text: RichText) -> str:
    """The text without formatting, one line per paragraph."""
    return "\n".join("".join(span.text for span in paragraph) for paragraph in text)


_HEADER = (
    "{\\rtf1\\ansi\\ansicpg1252\\uc1\\htmautsp\\deff2{\\fonttbl{\\f0\\fcharset0 Times New Roman;}"
    "{\\f2\\fcharset0 Segoe UI;}}{\\colortbl\\red0\\green0\\blue0;\\red255\\green255\\blue255;}"
    "\\loch\\hich\\dbch\\pard\\plain\\ltrpar\\itap0"
)
_PARAGRAPH_END = "\\li0\\ri0\\sa0\\sb0\\fi0\\ql\\par}"


def rich_text_rtf(text: RichText, *, language: int = 1033, font_size: int = 16) -> str:
    """The RTF of ``text`` as a WPF RichTextBox saves it; "" for no text. ``language`` is the
    Windows language id and ``font_size`` is in half points."""
    if (
        not any(span.text for paragraph in text for span in paragraph)
        and len(text) <= 1
    ):
        return ""
    body = f"{{\\lang{language}\\fs{font_size}\\f2\\cf0 \\cf0\\ql"
    parts: list[str] = []
    for paragraph in text:
        runs = "".join(_run_rtf(span) for span in paragraph if span.text)
        parts.append(f"{{\\f2 {runs}{_PARAGRAPH_END}")
    return _HEADER + body + "\r\n".join(parts) + "\r\n}\r\n}"


def _run_rtf(span: Span) -> str:
    words = ("\\b" if span.bold else "") + ("\\i" if span.italic else "")
    words += "\\ul" if span.underline else ""
    return f"{{{words}\\ltrch {_escape(span.text)}}}"


def _escape(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\t":
            out.append("\\tab ")
        elif ch == "\n":
            out.append("\\line ")
        elif " " <= ch <= "~":
            out.append(ch)
        else:
            out.append(_encode_char(ch))
    return "".join(out)


def _encode_char(ch: str) -> str:
    try:
        encoded = ch.encode("cp1252")
    except UnicodeEncodeError:
        units = ch.encode("utf-16-le")
        codes = [
            int.from_bytes(units[k : k + 2], "little") for k in range(0, len(units), 2)
        ]
        return "".join(f"\\u{c - 0x10000 if c > 0x7FFF else c}?" for c in codes)
    return "".join(f"\\'{b:02x}" for b in encoded)

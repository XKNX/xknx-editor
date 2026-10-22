"""Comments and installation hints: reading and writing their RTF."""

from __future__ import annotations

from xknxeditor.proj.core.rich_text import (
    Span,
    parse_rich_text,
    plain_text,
    rich_text_rtf,
)

_HEAD = (
    "{\\rtf1\\ansi\\ansicpg1252\\uc1\\htmautsp\\deff2{\\fonttbl{\\f0\\fcharset0 Times New Roman;}"
    "{\\f2\\fcharset0 Segoe UI;}}{\\colortbl\\red0\\green0\\blue0;\\red255\\green255\\blue255;}"
    "\\loch\\hich\\dbch\\pard\\plain\\ltrpar\\itap0"
)
_END = "\\li0\\ri0\\sa0\\sb0\\fi0\\ql\\par}"
# As the other tool saved them (a project export).
_COMMENT = (
    _HEAD
    + "{\\lang1033\\fs16\\f2\\cf0 \\cf0\\ql{\\f2 {\\ltrch Comment}"
    + _END
    + "\r\n}\r\n}"
)
_EMPTY = _HEAD + "{\\lang1033\\fs18\\f2\\cf0 \\cf0\\ql}\r\n}"
_FORMATTED = (
    _HEAD
    + "{\\lang1033\\fs16\\f2\\cf0 \\cf0\\ql{\\f2 {\\ltrch Installation Hint}"
    + _END
    + "\r\n{\\f2 {\\b\\ltrch bold}"
    + _END
    + "\r\n{\\f2 {\\i\\ltrch italic}"
    + _END
    + "\r\n{\\f2 {\\ul\\ltrch underscore}"
    + _END
    + "\r\n{\\f2 {\\ltrch }"
    + _END
    + "\r\n}\r\n}"
)


def test_reads_the_paragraphs_and_their_formatting() -> None:
    assert parse_rich_text(_COMMENT) == ((Span("Comment"),),)
    assert parse_rich_text(_EMPTY) == ()
    assert parse_rich_text(_FORMATTED) == (
        (Span("Installation Hint"),),
        (Span("bold", bold=True),),
        (Span("italic", italic=True),),
        (Span("underscore", underline=True),),
        (),
    )


def test_writes_rtf_as_the_other_tool_does() -> None:
    assert rich_text_rtf(parse_rich_text(_COMMENT)) == _COMMENT
    text = parse_rich_text(_FORMATTED)
    assert parse_rich_text(rich_text_rtf(text)) == text
    assert rich_text_rtf(()) == ""
    assert rich_text_rtf(((),)) == ""


def test_mixed_runs_and_special_characters() -> None:
    # Written by a WPF RichTextBox: runs in one paragraph, cp1252 bytes, \u with a fallback char,
    # escaped braces and backslash, a tab.
    rtf = (
        _HEAD
        + "{\\lang1033\\fs18\\f2\\cf0 \\cf0\\ql{\\f2 {\\ltrch plain }{\\b\\ltrch bold}{\\ltrch  }"
        "{\\b\\i\\ul\\ltrch all}"
        + _END
        + "\r\n{\\f2 {\\ltrch Zeile \\'e4\\'f6\\'fc \\'80 "
        "\\u20013?\\u25991? \\{x\\} \\\\ tab\\tab end}" + _END + "\r\n}\r\n}"
    )
    text = parse_rich_text(rtf)
    assert text == (
        (
            Span("plain "),
            Span("bold", bold=True),
            Span(" "),
            Span("all", bold=True, italic=True, underline=True),
        ),
        (Span("Zeile äöü € 中文 {x} \\ tab\tend"),),
    )
    assert parse_rich_text(rich_text_rtf(text)) == text
    assert "\\'e4\\'f6\\'fc \\'80 \\u20013?\\u25991? \\{x\\} \\\\ tab\\tab end" in (
        rich_text_rtf(text)
    )
    assert plain_text(text) == "plain bold all\nZeile äöü € 中文 {x} \\ tab\tend"


def test_plain_text_and_other_rtf() -> None:
    assert parse_rich_text("") == ()
    assert parse_rich_text("one\n\nthree") == ((Span("one"),), (), (Span("three"),))
    # A word processor's RTF: \b0 ends bold, \line breaks, an emoji as a surrogate pair.
    rtf = "{\\rtf1\\ansi{\\*\\generator x;}\\b on\\b0  off\\line \\u-10179?\\u-8704?\\par}"
    assert parse_rich_text(rtf) == (
        (Span("on", bold=True), Span(" off")),
        (Span("\U0001f600"),),
    )

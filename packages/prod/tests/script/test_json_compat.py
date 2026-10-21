"""Lenient JSON parsing for manufacturer parameter/handler blobs."""

from __future__ import annotations

import pytest

from xknxeditor.prod.script.json_compat import loads_lenient


def test_strict_json_passes_through() -> None:
    assert loads_lenient('{"a": 1, "b": [2, 3], "c": "x"}') == {
        "a": 1,
        "b": [2, 3],
        "c": "x",
    }


def test_unquoted_property_names() -> None:
    # The Zennio CX case that previously logged "invalid transformation parameters".
    assert loads_lenient("{ empty: 0}") == {"empty": 0}
    assert loads_lenient("{a: 1, b_2: true, $c: null}") == {
        "a": 1,
        "b_2": True,
        "$c": None,
    }


def test_single_quoted_strings() -> None:
    assert loads_lenient("{name: 'hello world'}") == {"name": "hello world"}
    assert loads_lenient("""{q: 'she said "hi"'}""") == {"q": 'she said "hi"'}
    assert loads_lenient(r"{p: 'it\'s fine'}") == {"p": "it's fine"}


def test_trailing_commas_and_comments() -> None:
    assert loads_lenient("{a: 1, b: 2,}") == {"a": 1, "b": 2}
    assert loads_lenient("[1, 2, 3,]") == [1, 2, 3]
    assert loads_lenient("{a: 1 /* c */, // line\n b: 2}") == {"a": 1, "b": 2}


def test_quotes_and_separators_inside_strings_are_preserved() -> None:
    # A colon/comma/brace inside a string must not be treated as structure.
    assert loads_lenient('{text: "a: b, {c}"}') == {"text": "a: b, {c}"}
    assert loads_lenient("{text: 'key: value, more'}") == {"text": "key: value, more"}


def test_literal_words_as_keys_are_quoted() -> None:
    # true/false/null are literal VALUES, but as property names they are keys (the authoring tool
    # reader accepts them). Key detection must win over literal detection.
    assert loads_lenient("{true: 1, false: 2, null: 3}") == {
        "true": 1,
        "false": 2,
        "null": 3,
    }
    assert loads_lenient("{enabled: true, disabled: false, spare: null}") == {
        "enabled": True,
        "disabled": False,
        "spare": None,
    }


def test_comment_does_not_join_tokens() -> None:
    # A stripped comment must not fuse the tokens around it into a value the reader would reject.
    for blob in ("{n: 1/* c */2}", "{flag: tr/* c */ue}"):
        with pytest.raises(ValueError):
            loads_lenient(blob)


def test_invalid_escapes_and_unterminated_strings_raise() -> None:
    for blob in (
        r"{s: '\x41'}",  # invalid escape (reader rejects; must not become "x41")
        r'{s: "\u041"}',  # short \u escape
        r'{s: "\u 041"}',  # non-hex \u escape
        '{s: "abc}',  # unterminated string
        "{s: 'abc}",
    ):
        with pytest.raises(ValueError):
            loads_lenient(blob)


def test_still_invalid_raises() -> None:
    with pytest.raises(ValueError):
        loads_lenient("{ this is not json at all ")

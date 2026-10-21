"""Context texts of calculations, validations and buttons: JSON or JavaScript object literals."""

from __future__ import annotations

import math

import pytest

from xknxeditor.prod.script.literal import parse_literal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"empty": 0}', {"empty": 0}),
        ("{ empty: 0}", {"empty": 0}),
        ("{empty:1}", {"empty": 1}),
        ("{a: 'x\\'y', $b_1: \"z\"}", {"a": "x'y", "$b_1": "z"}),
        ("{a: [1, 2,], b: {c: true,},}", {"a": [1, 2], "b": {"c": True}}),
        ("{a: -1.5e2, b: 0x1F, c: .5}", {"a": -150.0, "b": 31, "c": 0.5}),
        ("{a: null, b: undefined, c: false}", {"a": None, "b": None, "c": False}),
        ("// note\n{a: 1 /* one */}", {"a": 1}),
        ("{a: '\\u00e4\\n'}", {"a": "ä\n"}),
        ("{1: 'one'}", {"1": "one"}),
        ("[1, 2, 3,]", [1, 2, 3]),
        ("{a: 1 /* c */, // line\n b: 2}", {"a": 1, "b": 2}),
        ("""{q: 'she said "hi"'}""", {"q": 'she said "hi"'}),
        ('{text: "a: b, {c}"}', {"text": "a: b, {c}"}),
        ("{text: 'key: value, more'}", {"text": "key: value, more"}),
    ],
)
def test_parse(text: str, expected: object) -> None:
    assert parse_literal(text) == expected


def test_special_numbers() -> None:
    value = parse_literal("{a: NaN, b: -Infinity}")
    assert math.isnan(value["a"])
    assert value["b"] == -math.inf


@pytest.mark.parametrize(
    "text",
    [
        "{a: 1",
        "{a 1}",
        "{a: x}",
        "{a: 'x}",
        "{a: 1} b",
        "{,}",
        "",
        "{ this is not json at all ",
    ],
)
def test_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_literal(text)

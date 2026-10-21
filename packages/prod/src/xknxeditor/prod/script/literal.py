"""Read the ``context`` texts of calculations, validations and buttons.

Products write them as JSON, or as JavaScript object literals, e.g. ``{ empty: 0}``. The same
lenient forms as the reference tool's JSON reader are accepted: unquoted property names, single
quoted strings, comments, trailing commas, ``NaN``/``Infinity``, ``undefined`` and hex numbers.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

_SPACE = re.compile(r"(?:\s+|//[^\n]*|/\*.*?\*/)*", re.S)
_NUMBER = re.compile(
    r"[+-]?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|Infinity|NaN)"
)
_NAME = re.compile(r"[A-Za-z_$][\w$]*")
_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "0": "\0",
}
_WORDS: dict[str, Any] = {"true": True, "false": False, "null": None, "undefined": None}


def parse_literal(text: str) -> Any:
    """The value of ``text``; raises :class:`ValueError` when it is neither form."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    parser = _Parser(text)
    value = parser.value()
    parser.space()
    if parser.pos != len(text):
        parser.fail("unexpected text")
    return value


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def fail(self, what: str) -> None:
        raise ValueError(f"{what} at position {self.pos}")

    def space(self) -> None:
        match = _SPACE.match(self.text, self.pos)
        if match:
            self.pos = match.end()

    def peek(self) -> str:
        self.space()
        return self.text[self.pos : self.pos + 1]

    def expect(self, char: str) -> None:
        if self.peek() != char:
            self.fail(f"expected {char!r}")
        self.pos += 1

    def value(self) -> Any:
        char = self.peek()
        if char == "{":
            return self.object()
        if char == "[":
            return self.array()
        if char in ("'", '"'):
            return self.string()
        number = _NUMBER.match(self.text, self.pos)
        if number:
            self.pos = number.end()
            return _number(number.group())
        name = _NAME.match(self.text, self.pos)
        if name and name.group() in _WORDS:
            self.pos = name.end()
            return _WORDS[name.group()]
        self.fail("expected a value")
        return None

    def object(self) -> dict[str, Any]:
        self.expect("{")
        out: dict[str, Any] = {}
        while self.peek() != "}":
            if self.peek() in ("'", '"'):
                key = self.string()
            else:
                name = _NAME.match(self.text, self.pos) or _NUMBER.match(
                    self.text, self.pos
                )
                if not name:
                    self.fail("expected a property name")
                    break
                self.pos = name.end()
                key = name.group()
            self.expect(":")
            out[key] = self.value()
            if self.peek() != ",":
                break
            self.pos += 1
        self.expect("}")
        return out

    def array(self) -> list[Any]:
        self.expect("[")
        out: list[Any] = []
        while self.peek() != "]":
            out.append(self.value())
            if self.peek() != ",":
                break
            self.pos += 1
        self.expect("]")
        return out

    def string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        out: list[str] = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                return "".join(out)
            if char != "\\":
                out.append(char)
                continue
            escape = self.text[self.pos : self.pos + 1]
            self.pos += 1
            if escape == "u":
                digits = self.text[self.pos : self.pos + 4]
                if not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                    self.fail("invalid \\u escape")
                out.append(chr(int(digits, 16)))
                self.pos += 4
            else:
                out.append(_ESCAPES.get(escape, escape))
        self.fail("unterminated string")
        return ""


def _number(token: str) -> int | float:
    sign = -1 if token.startswith("-") else 1
    body = token.lstrip("+-")
    if body == "NaN":
        return math.nan
    if body == "Infinity":
        return sign * math.inf
    if body[:2] in ("0x", "0X"):
        return sign * int(body, 16)
    value = float(body)
    if value.is_integer() and not re.search(r"[.eE]", body):
        return sign * int(body)
    return sign * value


__all__ = ["parse_literal"]

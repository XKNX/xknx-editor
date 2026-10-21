"""Lenient JSON parsing for manufacturer parameter/handler blobs.

The authoring tool parses these blobs (``TransformationParameters``, event-handler parameters) with a
JavaScript-style lenient JSON reader: property names may be unquoted (``{ empty: 0}``), strings may
be single-quoted, and ``//`` / ``/* */`` comments and trailing commas are tolerated. Python's ``json``
is strict and rejects those, so ``loads_lenient`` first tries strict parsing and, on failure,
normalizes the common leniencies to strict JSON (string-aware, so quotes/commas inside strings are
left intact) and parses that. A blob that is still not valid raises ``ValueError``, like ``json``.
"""

from __future__ import annotations

import json
from typing import Any

_WS = " \t\r\n"


def loads_lenient(text: str) -> Any:
    """Parse ``text`` as JSON, tolerating JavaScript-style object literals (see module docstring)."""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_to_strict_json(text))


def _read_string(text: str, i: int) -> tuple[str, int]:
    """Read the quoted string starting at ``text[i]`` (either quote) and return its decoded value
    plus the index just past the closing quote, honouring backslash escapes."""
    quote = text[i]
    i += 1
    n = len(text)
    chars: list[str] = []
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            nxt = text[i + 1]
            simple = {
                '"': '"',
                "'": "'",
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }
            if nxt in simple:
                chars.append(simple[nxt])
                i += 2
            elif nxt == "u" and i + 6 <= n:
                chars.append(chr(int(text[i + 2 : i + 6], 16)))
                i += 6
            else:
                chars.append(nxt)  # unknown escape: keep the character itself
                i += 2
            continue
        if c == quote:
            return "".join(chars), i + 1
        chars.append(c)
        i += 1
    return "".join(chars), i  # unterminated: take the rest


def _skip_ws_comments(text: str, i: int) -> int:
    n = len(text)
    while i < n:
        if text[i] in _WS:
            i += 1
        elif text[i] == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
        elif text[i] == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                i += 1
            i += 2
        else:
            break
    return i


def _to_strict_json(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] in "/*":
            i = _skip_ws_comments(text, i)
        elif c in "\"'":
            value, i = _read_string(text, i)
            out.append(json.dumps(value))
        elif c == ",":
            nxt = _skip_ws_comments(text, i + 1)
            if nxt < n and text[nxt] in "}]":
                i += 1  # trailing comma before a closing bracket: drop it
            else:
                out.append(c)
                i += 1
        elif c.isalpha() or c in "_$":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            word = text[i:j]
            after = _skip_ws_comments(text, j)
            if word in ("true", "false", "null"):
                out.append(word)  # JSON literal value
            elif after < n and text[after] == ":":
                out.append(json.dumps(word))  # unquoted property name
            else:
                out.append(word)  # leave as-is: invalid, strict parse then reports it
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


__all__ = ["loads_lenient"]

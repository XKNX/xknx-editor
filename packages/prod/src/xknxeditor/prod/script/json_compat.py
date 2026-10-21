"""Lenient JSON parsing for manufacturer parameter/handler blobs.

The authoring tool parses these blobs (``TransformationParameters``, event-handler parameters) with a
JavaScript-style lenient JSON reader: property names may be unquoted (``{ empty: 0}``), strings may
be single-quoted, and ``//`` / ``/* */`` comments and trailing commas are tolerated. Python's ``json``
is strict and rejects those, so ``loads_lenient`` first tries strict parsing and, on failure,
normalizes the common leniencies to strict JSON (string-aware, so quotes/commas inside strings are
left intact) and parses that. A blob that is still not valid raises ``ValueError``, like ``json``.

Where this cannot faithfully match the reader it fails (raises) rather than guess, so the caller
falls back to an empty object instead of acting on a changed value. A few rarely-seen lenient forms
the reader also accepts are therefore not supported and fall back: hex/octal/leading-dot numbers
(``0x10``, ``010``, ``.5``), numeric property names, and elided array elements (``[,]``).
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


_SIMPLE_ESCAPES = {
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
_HEX = "0123456789abcdefABCDEF"


def _read_string(text: str, i: int) -> tuple[str, int]:
    """Read the quoted string starting at ``text[i]`` (either quote) and return its decoded value
    plus the index just past the closing quote, honouring backslash escapes. Raises ``ValueError`` on
    an unterminated string or an invalid escape, matching the authoring tool's reader (so the blob
    falls back to empty rather than silently taking a changed value)."""
    quote = text[i]
    i += 1
    n = len(text)
    chars: list[str] = []
    while i < n:
        c = text[i]
        if c == "\\":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt in _SIMPLE_ESCAPES:
                chars.append(_SIMPLE_ESCAPES[nxt])
                i += 2
            elif nxt == "u":
                hexits = text[i + 2 : i + 6]
                if len(hexits) != 4 or any(h not in _HEX for h in hexits):
                    raise ValueError(f"invalid \\u escape: {text[i : i + 6]!r}")
                chars.append(chr(int(hexits, 16)))
                i += 6
            else:
                raise ValueError(f"invalid escape: {text[i : i + 2]!r}")
            continue
        if c == quote:
            return "".join(chars), i + 1
        chars.append(c)
        i += 1
    raise ValueError("unterminated string")


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
            # Replace the comment with a space, never nothing: joining the tokens around it (e.g.
            # `1/* */2` -> `12`, `tr/* */ue` -> `true`) would invent a value the authoring tool's
            # reader rejects. A space keeps those blobs invalid, so they fall back to empty.
            i = _skip_ws_comments(text, i)
            out.append(" ")
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
            if after < n and text[after] == ":":
                out.append(
                    json.dumps(word)
                )  # unquoted property name (incl. a key named true/null)
            elif word in ("true", "false", "null"):
                out.append(word)  # JSON literal value
            else:
                out.append(word)  # leave as-is: invalid, strict parse then reports it
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


__all__ = ["loads_lenient"]

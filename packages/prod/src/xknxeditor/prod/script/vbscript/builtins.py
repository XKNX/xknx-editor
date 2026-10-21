"""The built-in functions and constants of the VBScript interpreter."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from .errors import (
    INVALID_CALL,
    INVALID_USE_OF_NULL,
    OVERFLOW,
    VBRuntimeError,
)
from .variant import (
    EMPTY,
    INT32_MAX,
    INT32_MIN,
    NULL,
    Byte,
    Culture,
    Integer,
    Single,
    Value,
    describe,
    is_numeric_type,
    make_double,
    negate,
    parse_number,
    round_even,
    single_or_none,
    to_boolean,
    to_byte,
    to_double,
    to_integer,
    to_long,
    to_single,
    to_text,
    type_name,
    vartype,
)

Function = Callable[[list[Value], Culture], Value]


@dataclass(frozen=True, slots=True)
class Builtin:
    name: str
    min_args: int
    max_args: int
    func: Function


BUILTINS: dict[str, Builtin] = {}


def _builtin(
    name: str, min_args: int, max_args: int | None = None
) -> Callable[[Function], Function]:
    def register(func: Function) -> Function:
        BUILTINS[name.lower()] = Builtin(
            name, min_args, min_args if max_args is None else max_args, func
        )
        return func

    return register


CONSTANTS: Final[dict[str, Value]] = {
    "vbcr": "\r",
    "vblf": "\n",
    "vbcrlf": "\r\n",
    "vbnewline": "\r\n",
    "vbtab": "\t",
    "vbnullstring": "",
    "vbnullchar": "\0",
    "vbformfeed": "\f",
    "vbverticaltab": "\v",
    "vbtrue": Integer(-1),
    "vbfalse": Integer(0),
    "vbusedefault": Integer(-2),
    "vbbinarycompare": Integer(0),
    "vbtextcompare": Integer(1),
    "vbempty": Integer(0),
    "vbnull": Integer(1),
    "vbinteger": Integer(2),
    "vblong": Integer(3),
    "vbsingle": Integer(4),
    "vbdouble": Integer(5),
    "vbcurrency": Integer(6),
    "vbdate": Integer(7),
    "vbstring": Integer(8),
    "vbobject": Integer(9),
    "vberror": Integer(10),
    "vbboolean": Integer(11),
    "vbvariant": Integer(12),
    "vbdataobject": Integer(13),
    "vbdecimal": Integer(14),
    "vbbyte": Integer(17),
    "vbarray": Integer(8192),
    "vbobjecterror": -2147221504,
}


def _null_error(name: str) -> VBRuntimeError:
    return VBRuntimeError(INVALID_USE_OF_NULL, name)


def _invalid(name: str) -> VBRuntimeError:
    return VBRuntimeError(INVALID_CALL, name)


def _long_arg(v: Value, culture: Culture, name: str) -> int:
    """A count or position argument."""
    if v is NULL:
        raise _null_error(name)
    return to_long(v, culture, name if isinstance(v, str) else None)


# ---------------------------------------------------------------------------------------------
# Conversions


@_builtin("CBool", 1)
def _cbool(a: list[Value], c: Culture) -> Value:
    return to_boolean(a[0], c, "CBool")


@_builtin("CByte", 1)
def _cbyte(a: list[Value], c: Culture) -> Value:
    return to_byte(a[0], c, "CByte")


@_builtin("CInt", 1)
def _cint(a: list[Value], c: Culture) -> Value:
    return to_integer(a[0], c, "CInt")


@_builtin("CLng", 1)
def _clng(a: list[Value], c: Culture) -> Value:
    return to_long(a[0], c, "CLng")


@_builtin("CSng", 1)
def _csng(a: list[Value], c: Culture) -> Value:
    return to_single(a[0], c, "CSng")


@_builtin("CDbl", 1)
def _cdbl(a: list[Value], c: Culture) -> Value:
    return to_double(a[0], c, "CDbl")


@_builtin("CStr", 1)
def _cstr(a: list[Value], c: Culture) -> Value:
    return to_text(a[0], c, "CStr")


# ---------------------------------------------------------------------------------------------
# Numbers


def _numeric(v: Value, c: Culture) -> Value:
    """The value as a number of its own type; strings become Double, Boolean Integer."""
    if isinstance(v, bool):
        return Integer(-1 if v else 0)
    if isinstance(v, str):
        return to_double(v, c)
    if v is EMPTY:
        return Integer(0)
    return v


def _int_like(v: Value, c: Culture, func: Callable[[float], float]) -> Value:
    if v is NULL:
        return NULL
    v = _numeric(v, c)
    if isinstance(v, int):
        return v
    assert isinstance(v, float)
    r = func(float(v))
    return Single(r) if isinstance(v, Single) else r


@_builtin("Int", 1)
def _int(a: list[Value], c: Culture) -> Value:
    return _int_like(a[0], c, lambda x: float(math.floor(x)))


@_builtin("Fix", 1)
def _fix(a: list[Value], c: Culture) -> Value:
    return _int_like(a[0], c, lambda x: float(math.trunc(x)))


@_builtin("Abs", 1)
def _abs(a: list[Value], c: Culture) -> Value:
    v = a[0]
    if v is NULL:
        return NULL
    v = _numeric(v, c)
    if isinstance(v, bool | int | float) and v < 0:
        return negate(v, c)
    return v


@_builtin("Sgn", 1)
def _sgn(a: list[Value], c: Culture) -> Value:
    v = a[0]
    if v is NULL:
        raise _null_error("Sgn")
    x = to_double(v, c)
    return Integer((x > 0) - (x < 0))


@_builtin("Round", 1, 2)
def _round(a: list[Value], c: Culture) -> Value:
    v = a[0]
    if v is NULL:
        raise _null_error("Round")
    places = 0
    if len(a) > 1:
        places = _long_arg(a[1], c, "Round")
        if places < 0 or places > 16:
            raise _invalid("Round")
    if isinstance(v, bool):
        return v
    v = _numeric(v, c)
    if isinstance(v, int):
        return v
    assert isinstance(v, float)
    x = float(v)
    if places == 0:
        r = float(round_even(x)) if math.isfinite(x) else x
    else:
        scale = 10.0**places
        scaled = x * scale
        r = round_even(scaled) / scale if math.isfinite(scaled) else x
    if isinstance(v, Single):
        s = single_or_none(r)
        return s if s is not None else r
    return r


@_builtin("Sqr", 1)
def _sqr(a: list[Value], c: Culture) -> Value:
    v = a[0]
    if v is NULL:
        raise _null_error("Sqr")
    x = to_double(v, c)
    if x < 0:
        raise _invalid("Sqr")
    return math.sqrt(x)


@_builtin("Exp", 1)
def _exp(a: list[Value], c: Culture) -> Value:
    if a[0] is NULL:
        raise _null_error("Exp")
    try:
        return make_double(math.exp(to_double(a[0], c)))
    except OverflowError:
        raise VBRuntimeError(OVERFLOW, "Exp") from None


@_builtin("Log", 1)
def _log(a: list[Value], c: Culture) -> Value:
    if a[0] is NULL:
        raise _null_error("Log")
    x = to_double(a[0], c)
    if x <= 0:
        raise _invalid("Log")
    return math.log(x)


def _math(name: str, func: Callable[[float], float]) -> None:
    def call(a: list[Value], c: Culture) -> Value:
        if a[0] is NULL:
            raise _null_error(name)
        return func(to_double(a[0], c))

    BUILTINS[name.lower()] = Builtin(name, 1, 1, call)


_math("Atn", math.atan)
_math("Cos", math.cos)
_math("Sin", math.sin)
_math("Tan", math.tan)


def _radix_text(a: list[Value], c: Culture, name: str, fmt: str) -> Value:
    v = a[0]
    if v is NULL:
        return NULL
    if v is EMPTY:
        return "0"
    if isinstance(v, bool):
        n, bits = (-1 if v else 0), 16
    elif isinstance(v, Byte):
        n, bits = int(v), 8
    elif isinstance(v, Integer):
        n, bits = int(v), 16
    else:
        n = to_double(v, c) if not isinstance(v, int) else v
        if isinstance(n, float):
            n = round_even(n)
        if not INT32_MIN <= n <= INT32_MAX:
            raise VBRuntimeError(OVERFLOW, describe(v, c))
        bits = 32
    return format(n & ((1 << bits) - 1), fmt)


@_builtin("Hex", 1)
def _hex(a: list[Value], c: Culture) -> Value:
    return _radix_text(a, c, "Hex", "X")


@_builtin("Oct", 1)
def _oct(a: list[Value], c: Culture) -> Value:
    return _radix_text(a, c, "Oct", "o")


# ---------------------------------------------------------------------------------------------
# Inspection


@_builtin("IsNumeric", 1)
def _isnumeric(a: list[Value], c: Culture) -> Value:
    v = a[0]
    if isinstance(v, str):
        try:
            return parse_number(v, c) is not None
        except VBRuntimeError:
            return False
    return v is EMPTY or is_numeric_type(v)


@_builtin("IsEmpty", 1)
def _isempty(a: list[Value], c: Culture) -> Value:
    return a[0] is EMPTY


@_builtin("IsNull", 1)
def _isnull(a: list[Value], c: Culture) -> Value:
    return a[0] is NULL


@_builtin("IsArray", 1)
def _isarray(a: list[Value], c: Culture) -> Value:
    return False


@_builtin("IsObject", 1)
def _isobject(a: list[Value], c: Culture) -> Value:
    return False


@_builtin("TypeName", 1)
def _typename(a: list[Value], c: Culture) -> Value:
    return type_name(a[0])


@_builtin("VarType", 1)
def _vartype(a: list[Value], c: Culture) -> Value:
    return Integer(vartype(a[0]))


# ---------------------------------------------------------------------------------------------
# Strings


def _text_or_null(v: Value, c: Culture) -> str | None:
    if v is NULL:
        return None
    return to_text(v, c)


@_builtin("Len", 1)
def _len(a: list[Value], c: Culture) -> Value:
    s = _text_or_null(a[0], c)
    return NULL if s is None else len(s)


@_builtin("Left", 2)
def _left(a: list[Value], c: Culture) -> Value:
    n = _long_arg(a[1], c, "Left")
    if n < 0:
        raise _invalid("Left")
    s = _text_or_null(a[0], c)
    return NULL if s is None else s[:n]


@_builtin("Right", 2)
def _right(a: list[Value], c: Culture) -> Value:
    n = _long_arg(a[1], c, "Right")
    if n < 0:
        raise _invalid("Right")
    s = _text_or_null(a[0], c)
    if s is None:
        return NULL
    return s[len(s) - n :] if n < len(s) else s


@_builtin("Mid", 2, 3)
def _mid(a: list[Value], c: Culture) -> Value:
    start = _long_arg(a[1], c, "Mid")
    if start < 1:
        raise _invalid("Mid")
    length: int | None = None
    if len(a) > 2:
        length = _long_arg(a[2], c, "Mid")
        if length < 0:
            raise _invalid("Mid")
    s = _text_or_null(a[0], c)
    if s is None:
        return NULL
    part = s[start - 1 :]
    return part if length is None else part[:length]


def _fold(s: str, compare: int) -> str:
    return s.lower() if compare else s


@_builtin("InStr", 2, 4)
def _instr(a: list[Value], c: Culture) -> Value:
    start = 1
    args = a
    if len(a) >= 3:
        if a[0] is NULL:
            raise _null_error("InStr")
        start = _long_arg(a[0], c, "InStr")
        if start < 1:
            raise _invalid("InStr")
        args = a[1:]
    compare = _long_arg(args[2], c, "InStr") if len(args) > 2 else 0
    s1 = _text_or_null(args[0], c)
    s2 = _text_or_null(args[1], c)
    if s1 is None or s2 is None:
        return NULL
    if start > len(s1):
        return 0
    return _fold(s1, compare).find(_fold(s2, compare), start - 1) + 1


@_builtin("InStrRev", 2, 4)
def _instrrev(a: list[Value], c: Culture) -> Value:
    s1 = _text_or_null(a[0], c)
    s2 = _text_or_null(a[1], c)
    start = _long_arg(a[2], c, "InStrRev") if len(a) > 2 else -1
    compare = _long_arg(a[3], c, "InStrRev") if len(a) > 3 else 0
    if start == 0 or start < -1:
        raise _invalid("InStrRev")
    if s1 is None or s2 is None:
        return NULL
    if start == -1:
        start = len(s1)
    if start > len(s1):
        return 0
    if not s2:
        return start
    return _fold(s1, compare).rfind(_fold(s2, compare), 0, start) + 1


def _upper(s: str) -> str:
    return "".join(ch.upper() if len(ch.upper()) == 1 else ch for ch in s)


def _lower(s: str) -> str:
    return "".join(ch.lower() if len(ch.lower()) == 1 else ch for ch in s)


@_builtin("UCase", 1)
def _ucase(a: list[Value], c: Culture) -> Value:
    s = _text_or_null(a[0], c)
    return NULL if s is None else _upper(s)


@_builtin("LCase", 1)
def _lcase(a: list[Value], c: Culture) -> Value:
    s = _text_or_null(a[0], c)
    return NULL if s is None else _lower(s)


@_builtin("Trim", 1)
def _trim(a: list[Value], c: Culture) -> Value:
    s = _text_or_null(a[0], c)
    return NULL if s is None else s.strip(" ")


@_builtin("LTrim", 1)
def _ltrim(a: list[Value], c: Culture) -> Value:
    s = _text_or_null(a[0], c)
    return NULL if s is None else s.lstrip(" ")


@_builtin("RTrim", 1)
def _rtrim(a: list[Value], c: Culture) -> Value:
    s = _text_or_null(a[0], c)
    return NULL if s is None else s.rstrip(" ")


@_builtin("StrReverse", 1)
def _strreverse(a: list[Value], c: Culture) -> Value:
    if a[0] is NULL:
        raise _null_error("StrReverse")
    return to_text(a[0], c)[::-1]


@_builtin("Space", 1)
def _space(a: list[Value], c: Culture) -> Value:
    n = _long_arg(a[0], c, "Space")
    if n < 0:
        raise _invalid("Space")
    return " " * n


@_builtin("String", 2)
def _string(a: list[Value], c: Culture) -> Value:
    n = _long_arg(a[0], c, "String")
    if n < 0:
        raise _invalid("String")
    ch = a[1]
    if ch is NULL:
        return NULL
    if isinstance(ch, str):
        if not ch:
            raise _invalid("String")
        return ch[0] * n
    code = to_long(ch, c) % 256
    return _ansi_char(code) * n


def _ansi_char(code: int) -> str:
    return (
        bytes([code]).decode("cp1252", errors="replace") if code >= 128 else chr(code)
    )


@_builtin("Chr", 1)
def _chr(a: list[Value], c: Culture) -> Value:
    code = _long_arg(a[0], c, "Chr")
    if not 0 <= code <= 255:
        raise _invalid("Chr")
    return _ansi_char(code)


@_builtin("ChrW", 1)
def _chrw(a: list[Value], c: Culture) -> Value:
    code = _long_arg(a[0], c, "ChrW")
    if not -32768 <= code <= 65535:
        raise _invalid("ChrW")
    return chr(code & 0xFFFF)


@_builtin("Asc", 1)
def _asc(a: list[Value], c: Culture) -> Value:
    if a[0] is NULL:
        raise VBRuntimeError(INVALID_USE_OF_NULL, "Null")
    s = to_text(a[0], c)
    if not s:
        raise _invalid("Asc")
    try:
        data = s[0].encode("cp1252")
    except UnicodeEncodeError:
        data = b"?"
    return Integer(data[0])


@_builtin("AscW", 1)
def _ascw(a: list[Value], c: Culture) -> Value:
    if a[0] is NULL:
        raise VBRuntimeError(INVALID_USE_OF_NULL, "Null")
    s = to_text(a[0], c)
    if not s:
        raise _invalid("AscW")
    code = ord(s[0])
    return Integer(code - 0x10000 if code >= 0x8000 else code)


@_builtin("Replace", 3, 6)
def _replace(a: list[Value], c: Culture) -> Value:
    if a[0] is NULL:
        raise _null_error("Replace")
    text = to_text(a[0], c)
    find = to_text(a[1], c, "Replace")
    repl = to_text(a[2], c, "Replace")
    start = _long_arg(a[3], c, "Replace") if len(a) > 3 else 1
    count = _long_arg(a[4], c, "Replace") if len(a) > 4 else -1
    compare = _long_arg(a[5], c, "Replace") if len(a) > 5 else 0
    if start < 1 or count < -1:
        raise _invalid("Replace")
    text = text[start - 1 :]
    if not find or count == 0:
        return text
    if not compare:
        return text.replace(find, repl, count)
    out: list[str] = []
    low, key = text.lower(), find.lower()
    pos = 0
    done = 0
    while count < 0 or done < count:
        hit = low.find(key, pos)
        if hit < 0:
            break
        out.append(text[pos:hit])
        out.append(repl)
        pos = hit + len(find)
        done += 1
    out.append(text[pos:])
    return "".join(out)


@_builtin("StrComp", 2, 3)
def _strcomp(a: list[Value], c: Culture) -> Value:
    compare = _long_arg(a[2], c, "StrComp") if len(a) > 2 else 0
    s1 = _text_or_null(a[0], c)
    s2 = _text_or_null(a[1], c)
    if s1 is None or s2 is None:
        return NULL
    s1, s2 = _fold(s1, compare), _fold(s2, compare)
    return Integer((s1 > s2) - (s1 < s2))

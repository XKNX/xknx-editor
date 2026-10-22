"""VBScript variant values with their conversions and operators.

A variant is one of ``Empty``, ``Null``, Boolean (``bool``), Byte, Integer (16 bit), Long (``int``,
32 bit), Single, Double (``float``) or String (``str``). Arithmetic promotes to the next wider type
on overflow, logical operators work bitwise on integers, and comparisons with a literal convert the
other operand to the literal's type first.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from . import fmath
from .errors import (
    DIVISION_BY_ZERO,
    INVALID_CALL,
    INVALID_USE_OF_NULL,
    OVERFLOW,
    TYPE_MISMATCH,
    VBRuntimeError,
)


class EmptyType:
    """The value of a variable that was never assigned."""

    _instance: EmptyType | None = None

    def __new__(cls) -> EmptyType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Empty"


class NullType:
    """The value that holds no valid data."""

    _instance: NullType | None = None

    def __new__(cls) -> NullType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Null"


EMPTY: Final = EmptyType()
NULL: Final = NullType()


class Integer(int):
    """A 16-bit signed integer."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Integer({int(self)})"


class Byte(int):
    """An 8-bit unsigned integer."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Byte({int(self)})"


class Single(float):
    """A 32-bit floating point number."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Single({float(self)!r})"


Value = EmptyType | NullType | bool | int | float | str

VT_EMPTY: Final = 0
VT_NULL: Final = 1
VT_INTEGER: Final = 2
VT_LONG: Final = 3
VT_SINGLE: Final = 4
VT_DOUBLE: Final = 5
VT_STRING: Final = 8
VT_BOOLEAN: Final = 11
VT_BYTE: Final = 17

_TYPE_NAMES: Final = {
    VT_EMPTY: "Empty",
    VT_NULL: "Null",
    VT_INTEGER: "Integer",
    VT_LONG: "Long",
    VT_SINGLE: "Single",
    VT_DOUBLE: "Double",
    VT_STRING: "String",
    VT_BOOLEAN: "Boolean",
    VT_BYTE: "Byte",
}

INT16_MIN: Final = -32768
INT16_MAX: Final = 32767
INT32_MIN: Final = -2147483648
INT32_MAX: Final = 2147483647
_FLT_MAX: Final = 3.4028234663852886e38


@dataclass(frozen=True, slots=True)
class Culture:
    """Number separators and currency symbol of the conversions between numbers and strings."""

    decimal: str = "."
    group: str = ","
    currency: str = "$"


def vartype(v: Value) -> int:
    t = type(v)
    if t is int:
        return VT_LONG
    if t is float:
        return VT_DOUBLE
    if t is str:
        return VT_STRING
    if t is bool:
        return VT_BOOLEAN
    if t is Integer:
        return VT_INTEGER
    if t is Byte:
        return VT_BYTE
    if t is Single:
        return VT_SINGLE
    if v is NULL:
        return VT_NULL
    return VT_EMPTY


def type_name(v: Value) -> str:
    return _TYPE_NAMES[vartype(v)]


def is_numeric_type(v: Value) -> bool:
    """Boolean and the number types."""
    return isinstance(v, (bool, int, float))


# ---------------------------------------------------------------------------------------------
# Construction with range checks


def make_integer(n: int) -> Integer:
    if not INT16_MIN <= n <= INT16_MAX:
        raise VBRuntimeError(OVERFLOW)
    return Integer(n)


def make_long(n: int) -> int:
    if not INT32_MIN <= n <= INT32_MAX:
        raise VBRuntimeError(OVERFLOW)
    return n


def make_byte(n: int) -> Byte:
    if not 0 <= n <= 255:
        raise VBRuntimeError(OVERFLOW)
    return Byte(n)


def single_or_none(x: float) -> Single | None:
    """``x`` rounded to single precision, or ``None`` when it is out of the Single range."""
    if math.isnan(x) or abs(x) > _FLT_MAX * (1 + 2**-25):
        return None
    try:
        return Single(struct.unpack("<f", struct.pack("<f", x))[0])
    except OverflowError:
        return None


def make_single(x: float) -> Single:
    s = single_or_none(x)
    if s is None:
        raise VBRuntimeError(OVERFLOW)
    return s


def make_double(x: float) -> float:
    if math.isinf(x) or math.isnan(x):
        raise VBRuntimeError(OVERFLOW)
    return x


def round_even(x: float) -> int:
    """Banker's rounding, as the integer conversions do."""
    return round(x)


# ---------------------------------------------------------------------------------------------
# Numbers and strings


def _significant(x: float, digits: int) -> tuple[str, int]:
    """The significant digits and decimal exponent of ``abs(x)``.

    The engine takes 17 significant digits of the value (18 when the first digit is 1) and rounds
    those half up to ``digits``, so a value just below a half can round up."""
    mantissa, exp_text = f"{abs(x):.16e}".split("e")
    if mantissa[0] == "1":
        mantissa, exp_text = f"{abs(x):.17e}".split("e")
    exponent = int(exp_text)
    rounded = Decimal(mantissa).quantize(Decimal(1).scaleb(1 - digits), ROUND_HALF_UP)
    if rounded >= 10:
        rounded /= 10
        exponent += 1
    sig = str(rounded).replace(".", "").rstrip("0") or "0"
    return sig, exponent


def format_float(x: float, digits: int, culture: Culture) -> str:
    """A Double (15 digits) or Single (7 digits) as text."""
    if x == 0:
        return "0"
    negative = x < 0
    sig, exponent = _significant(x, digits)
    n = len(sig)
    if exponent >= digits or n - 1 - exponent > digits:
        out = sig[0]
        if n > 1:
            out += culture.decimal + sig[1:]
        out += f"E{'+' if exponent >= 0 else '-'}{abs(exponent):02d}"
    elif exponent >= 0:
        whole = sig[: exponent + 1].ljust(exponent + 1, "0")
        frac = sig[exponent + 1 :]
        out = whole + (culture.decimal + frac if frac else "")
    else:
        out = "0" + culture.decimal + "0" * (-exponent - 1) + sig
    return "-" + out if negative else out


def number_text(v: bool | int | float, culture: Culture) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        return str(int(v))
    if type(v) is Single:
        return format_float(float(v), 7, culture)
    return format_float(float(v), 15, culture)


_HEX = re.compile(r"[hH]([0-9A-Fa-f]+)")
_OCT = re.compile(r"[oO]?([0-7]+)")
# Characters skipped around a number in a text.
_BLANKS = " \t\n\r\v\f\xa0"


def _radix_text(body: str) -> float | None:
    match = _HEX.fullmatch(body)
    if match is not None:
        v = int(match.group(1), 16)
        if v <= 0xFFFFFFFF:
            return float(v - 0x100000000 if v >= 0x80000000 else v)
        if v <= 0xFFFFFFFFFFFFFFFF:
            return float(v - 0x10000000000000000 if v >= 0x8000000000000000 else v)
        raise VBRuntimeError(OVERFLOW)
    match = _OCT.fullmatch(body)
    if match is not None:
        v = int(match.group(1), 8)
        if v <= 0xFFFFFFFF:
            return float(v - 0x100000000 if v >= 0x80000000 else v)
    return None


def parse_number(text: str, culture: Culture) -> float | None:
    """The number in ``text`` like the engine reads it, or ``None`` when it is no number.

    Blanks, the currency symbol, a sign before or after the number or parentheses around it are
    accepted, and group separators anywhere after the first digit; ``&H``/``&O`` give integers.
    The text ends at its first NUL character. Raises the Overflow error for a number outside the
    Double range.
    """
    text = text.split("\0", 1)[0]
    n = len(text)
    i = 0
    while i < n and text[i] in _BLANKS:
        i += 1
    if i < n and text[i] == "&":
        return _radix_text(text[i + 1 :].rstrip(_BLANKS))
    sign = ""
    paren = 0
    currency = False
    symbol = culture.currency
    while i < n:
        ch = text[i]
        if ch in _BLANKS:
            i += 1
        elif symbol and not currency and text.startswith(symbol, i):
            currency = True
            i += len(symbol)
        elif ch in "+-" and not sign and not paren:
            sign = ch
            i += 1
        elif ch == "(" and not sign and not paren:
            paren = 1
            i += 1
        else:
            break
    mantissa: list[str] = []
    seen_digit = False
    seen_point = False
    while i < n:
        ch = text[i]
        if "0" <= ch <= "9":
            mantissa.append(ch)
            seen_digit = True
        elif ch == culture.decimal and not seen_point:
            mantissa.append(".")
            seen_point = True
        elif ch == culture.group and seen_digit:
            pass
        else:
            break
        i += 1
    if not seen_digit:
        return None
    exponent = ""
    if i < n and text[i] in "eEdD":
        j = i + 1
        exp_sign = ""
        if j < n and text[j] in "+-":
            exp_sign = text[j]
            j += 1
        k = j
        while k < n and "0" <= text[k] <= "9":
            k += 1
        if k == j:
            return None
        exponent = "e" + exp_sign + text[j:k]
        i = k
    while i < n:
        ch = text[i]
        if ch in _BLANKS:
            i += 1
        elif symbol and not currency and text.startswith(symbol, i):
            currency = True
            i += len(symbol)
        elif ch in "+-" and not sign and not paren:
            sign = ch
            i += 1
        elif ch == ")" and paren == 1:
            paren = 2
            i += 1
        else:
            break
    if i < n or paren == 1:
        return None
    value = float("".join(mantissa) + exponent)
    if math.isinf(value):
        raise VBRuntimeError(OVERFLOW)
    return -value if sign == "-" or paren else value


def bool_from_text(text: str) -> bool | None:
    t = text.lower()
    if t == "true":
        return True
    if t == "false":
        return False
    return None


def describe(v: Value, culture: Culture) -> str:
    """The value in the form the engine names it in error texts."""
    if isinstance(v, str):
        return f'[string: "{v}"]'
    if isinstance(v, (bool, int, float)):
        return f"[number: {number_text(v, culture)}]"
    return f"[{type_name(v)}]"


def to_text(v: Value, culture: Culture, detail: str | None = None) -> str:
    """``CStr``: the text of ``v``; Null is an error."""
    if isinstance(v, str):
        return v
    if v is EMPTY:
        return ""
    if v is NULL:
        raise VBRuntimeError(INVALID_USE_OF_NULL, detail)
    assert isinstance(v, (bool, int, float))
    return number_text(v, culture)


def to_double(v: Value, culture: Culture, detail: str | None = None) -> float:
    """``CDbl``."""
    if isinstance(v, bool):
        return -1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        x = parse_number(v, culture)
        if x is None:
            raise VBRuntimeError(
                TYPE_MISMATCH, detail if detail is not None else describe(v, culture)
            )
        return x
    if v is NULL:
        raise VBRuntimeError(INVALID_USE_OF_NULL, detail)
    return 0.0


def to_integral(v: Value, culture: Culture, detail: str | None = None) -> int:
    """The value rounded to an integer (banker's rounding), before any range check."""
    if isinstance(v, bool):
        return -1 if v else 0
    if isinstance(v, int):
        return int(v)
    return round_even(to_double(v, culture, detail))


def to_long(v: Value, culture: Culture, detail: str | None = None) -> int:
    """``CLng``."""
    n = to_integral(v, culture, detail)
    if not INT32_MIN <= n <= INT32_MAX:
        raise VBRuntimeError(OVERFLOW, detail)
    return n


def to_integer(v: Value, culture: Culture, detail: str | None = None) -> Integer:
    """``CInt``."""
    n = to_integral(v, culture, detail)
    if not INT16_MIN <= n <= INT16_MAX:
        raise VBRuntimeError(OVERFLOW, detail)
    return Integer(n)


def to_byte(v: Value, culture: Culture, detail: str | None = None) -> Byte:
    """``CByte``; True is 255."""
    if isinstance(v, bool):
        return Byte(255 if v else 0)
    n = to_integral(v, culture, detail)
    if not 0 <= n <= 255:
        raise VBRuntimeError(OVERFLOW, detail)
    return Byte(n)


def to_single(v: Value, culture: Culture, detail: str | None = None) -> Single:
    """``CSng``."""
    s = single_or_none(to_double(v, culture, detail))
    if s is None:
        raise VBRuntimeError(OVERFLOW, detail)
    return s


def to_boolean(v: Value, culture: Culture, detail: str | None = None) -> bool:
    """``CBool``."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        b = bool_from_text(v)
        if b is not None:
            return b
        x = parse_number(v, culture)
        if x is None:
            raise VBRuntimeError(
                TYPE_MISMATCH, detail if detail is not None else describe(v, culture)
            )
        return x != 0
    if v is NULL:
        raise VBRuntimeError(INVALID_USE_OF_NULL, detail)
    return False


def condition(v: Value, culture: Culture, detail: str | None = None) -> bool:
    """The truth of an ``If``, ``While`` or ``Loop`` condition; Null counts as False."""
    if v is NULL:
        return False
    return to_boolean(v, culture, detail)


# ---------------------------------------------------------------------------------------------
# Arithmetic

_EMPTY_RANK: Final = -1
_BYTE: Final = 0
_INTEGER: Final = 1
_LONG: Final = 2
_SINGLE: Final = 3
_DOUBLE: Final = 4


def _operand(v: Value, culture: Culture) -> tuple[int, int | float]:
    if isinstance(v, bool):
        return _INTEGER, -1 if v else 0
    if isinstance(v, Byte):
        return _BYTE, int(v)
    if isinstance(v, Integer):
        return _INTEGER, int(v)
    if isinstance(v, int):
        return _LONG, v
    if isinstance(v, Single):
        return _SINGLE, float(v)
    if isinstance(v, float):
        return _DOUBLE, v
    if isinstance(v, str):
        x = parse_number(v, culture)
        if x is None:
            raise VBRuntimeError(TYPE_MISMATCH, describe(v, culture))
        return _DOUBLE, x
    return _EMPTY_RANK, 0


def common_number(values: tuple[Value, ...], culture: Culture) -> tuple[Value, ...]:
    """The values converted to their common number type, as a ``For`` loop uses them."""
    operands = [_operand(v, culture) for v in values]
    rank = _EMPTY_RANK
    for r, _ in operands:
        rank = _join(rank, r)
    if rank == _EMPTY_RANK:
        rank = _INTEGER
    out: list[Value] = []
    for _, x in operands:
        if rank == _BYTE:
            out.append(Byte(int(x)))
        elif rank == _INTEGER:
            out.append(Integer(int(x)))
        elif rank == _LONG:
            out.append(int(x))
        elif rank == _SINGLE:
            out.append(make_single(float(x)))
        else:
            out.append(float(x))
    return tuple(out)


def _join(ra: int, rb: int) -> int:
    if ra == _EMPTY_RANK:
        return _INTEGER if rb == _EMPTY_RANK else rb
    if rb == _EMPTY_RANK:
        return ra
    if _DOUBLE in (ra, rb):
        return _DOUBLE
    if _SINGLE in (ra, rb):
        return _DOUBLE if _LONG in (ra, rb) else _SINGLE
    return max(ra, rb)


def _fit_int(n: int, rank: int) -> Value:
    """``n`` in the integer type ``rank``, widened while it does not fit."""
    if rank <= _BYTE and 0 <= n <= 255:
        return Byte(n)
    if rank <= _INTEGER and INT16_MIN <= n <= INT16_MAX:
        return Integer(n)
    if INT32_MIN <= n <= INT32_MAX:
        return n
    return make_double(float(n))


def _fit_float(x: float, rank: int) -> Value:
    if rank == _SINGLE:
        s = single_or_none(x)
        if s is not None:
            return s
    return make_double(x)


def _arith(a: Value, b: Value, op: str, culture: Culture) -> Value:
    ra, xa = _operand(a, culture)
    rb, xb = _operand(b, culture)
    rank = _join(ra, rb)
    if rank <= _LONG:
        ia, ib = int(xa), int(xb)
        n = ia + ib if op == "+" else ia - ib if op == "-" else ia * ib
        return _fit_int(n, rank)
    fa, fb = float(xa), float(xb)
    x = fa + fb if op == "+" else fa - fb if op == "-" else fa * fb
    return _fit_float(x, rank)


def add(a: Value, b: Value, culture: Culture) -> Value:
    if a is NULL or b is NULL:
        return NULL
    if isinstance(a, str) and (isinstance(b, str) or b is EMPTY):
        return a + b if isinstance(b, str) else a
    if a is EMPTY and isinstance(b, str):
        return b
    return _arith(a, b, "+", culture)


def subtract(a: Value, b: Value, culture: Culture) -> Value:
    if a is NULL or b is NULL:
        return NULL
    return _arith(a, b, "-", culture)


def multiply(a: Value, b: Value, culture: Culture) -> Value:
    if a is NULL or b is NULL:
        return NULL
    return _arith(a, b, "*", culture)


def divide(a: Value, b: Value, culture: Culture) -> Value:
    if a is NULL or b is NULL:
        return NULL
    ra, xa = _operand(a, culture)
    rb, xb = _operand(b, culture)
    if _DOUBLE in (ra, rb) or _LONG in (ra, rb):
        rank = _DOUBLE
    elif _SINGLE in (ra, rb) or (ra == _EMPTY_RANK and rb != _EMPTY_RANK):
        rank = _SINGLE
    else:
        rank = _DOUBLE
    if xb == 0:
        raise VBRuntimeError(OVERFLOW if xa == 0 else DIVISION_BY_ZERO)
    return _fit_float(xa / xb, rank)


def _int_operand(v: Value, culture: Culture) -> tuple[int, int]:
    if isinstance(v, bool):
        return _INTEGER, -1 if v else 0
    if isinstance(v, Byte):
        return _BYTE, int(v)
    if isinstance(v, Integer):
        return _INTEGER, int(v)
    if isinstance(v, int):
        return _LONG, v
    if v is EMPTY:
        return _INTEGER, 0
    x = round_even(_operand(v, culture)[1])
    if not INT32_MIN <= x <= INT32_MAX:
        raise VBRuntimeError(OVERFLOW)
    return _LONG, x


def _trunc_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def int_divide(a: Value, b: Value, culture: Culture) -> Value:
    if a is NULL or b is NULL:
        return NULL
    ra, xa = _int_operand(a, culture)
    rb, xb = _int_operand(b, culture)
    if xb == 0:
        raise VBRuntimeError(DIVISION_BY_ZERO)
    n = _trunc_div(xa, xb)
    if not INT32_MIN <= n <= INT32_MAX:
        raise VBRuntimeError(OVERFLOW)
    return _fit_int(n, max(ra, rb))


def modulo(a: Value, b: Value, culture: Culture) -> Value:
    if a is NULL or b is NULL:
        return NULL
    ra, xa = _int_operand(a, culture)
    rb, xb = _int_operand(b, culture)
    if xb == 0:
        raise VBRuntimeError(DIVISION_BY_ZERO)
    return _fit_int(xa - xb * _trunc_div(xa, xb), max(ra, rb))


def power(a: Value, b: Value, culture: Culture) -> Value:
    if a is NULL or b is NULL:
        return NULL
    x = float(_operand(a, culture)[1])
    y = float(_operand(b, culture)[1])
    if x == 0 and y < 0:
        raise VBRuntimeError(INVALID_CALL)
    if x < 0 and not y.is_integer():
        raise VBRuntimeError(INVALID_CALL)
    try:
        r = fmath.power(x, y)
    except OverflowError:
        raise VBRuntimeError(OVERFLOW) from None
    except ValueError:
        raise VBRuntimeError(INVALID_CALL) from None
    return make_double(r)


def negate(a: Value, culture: Culture) -> Value:
    if a is NULL:
        return NULL
    rank, x = _operand(a, culture)
    if rank == _EMPTY_RANK:
        return Integer(0)
    if rank <= _LONG:
        return _fit_int(-int(x), max(rank, _INTEGER))
    return _fit_float(-float(x), rank)


def concat(a: Value, b: Value, culture: Culture) -> Value:
    if a is NULL and b is NULL:
        return NULL
    left = "" if a is NULL else to_text(a, culture)
    right = "" if b is NULL else to_text(b, culture)
    return left + right


# ---------------------------------------------------------------------------------------------
# Logical (bitwise) operators

_L_BOOL: Final = 0
_L_BYTE: Final = 1
_L_INT: Final = 2
_L_LONG: Final = 3


def _logic_operand(v: Value, culture: Culture) -> tuple[int, int] | None:
    if isinstance(v, bool):
        return _L_BOOL, -1 if v else 0
    if isinstance(v, Byte):
        return _L_BYTE, int(v)
    if isinstance(v, Integer):
        return _L_INT, int(v)
    if isinstance(v, int):
        return _L_LONG, v
    if v is NULL:
        return None
    if v is EMPTY:
        return _L_LONG, 0
    if isinstance(v, str):
        b = bool_from_text(v)
        if b is not None:
            return _L_BOOL, -1 if b else 0
    n = round_even(_operand(v, culture)[1])
    if not INT32_MIN <= n <= INT32_MAX:
        raise VBRuntimeError(OVERFLOW)
    return _L_LONG, n


def _logic_kind(ka: int, kb: int) -> int:
    if ka == _L_BOOL and kb == _L_BOOL:
        return _L_BOOL
    return max(_L_INT if ka == _L_BOOL else ka, _L_INT if kb == _L_BOOL else kb)


def _logic_value(n: int, kind: int) -> Value:
    if kind == _L_BOOL:
        return n != 0
    if kind == _L_BYTE:
        return Byte(n & 0xFF)
    if kind == _L_INT:
        n &= 0xFFFF
        return Integer(n - 0x10000 if n >= 0x8000 else n)
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n >= 0x80000000 else n


def logical(op: str, a: Value, b: Value, culture: Culture) -> Value:
    """``And``, ``Or``, ``Xor``, ``Eqv`` or ``Imp``."""
    la = _logic_operand(a, culture)
    lb = _logic_operand(b, culture)
    if la is None or lb is None:
        return _logical_null(op, la, lb)
    kind = _logic_kind(la[0], lb[0])
    x, y = la[1], lb[1]
    if op == "and":
        n = x & y
    elif op == "or":
        n = x | y
    elif op == "xor":
        n = x ^ y
    elif op == "eqv":
        n = ~(x ^ y)
    else:
        n = ~x | y
    return _logic_value(n, kind)


def _logical_null(
    op: str, la: tuple[int, int] | None, lb: tuple[int, int] | None
) -> Value:
    if op == "imp":
        if la is None:
            if lb is None or lb[1] == 0:
                return NULL
            return _logic_value(lb[1], lb[0])
        not_a = _logic_value(~la[1], la[0])
        return NULL if not_a == 0 else not_a
    other = lb if la is None else la
    if other is None or op in ("xor", "eqv"):
        return NULL
    if op == "and":
        return _logic_value(0, other[0]) if other[1] == 0 else NULL
    return NULL if other[1] == 0 else _logic_value(other[1], other[0])


def logical_not(a: Value, culture: Culture) -> Value:
    la = _logic_operand(a, culture)
    if la is None:
        return NULL
    return _logic_value(~la[1], la[0])


# ---------------------------------------------------------------------------------------------
# Comparison


def _literal_operand(lit: Value, other: Value, culture: Culture) -> Value:
    """The other operand converted to the type of a literal it is compared with."""
    if isinstance(lit, str):
        if is_numeric_type(other):
            return to_text(other, culture)
        return other
    if is_numeric_type(lit) and isinstance(other, str):
        x = parse_number(other, culture)
        if x is None:
            raise VBRuntimeError(TYPE_MISMATCH, describe(other, culture))
        return x
    return other


def compare(
    a: Value,
    b: Value,
    culture: Culture,
    a_literal: bool = False,
    b_literal: bool = False,
) -> int | None:
    """-1, 0 or 1, or ``None`` when an operand is Null."""
    if a is NULL or b is NULL:
        return None
    if a_literal and b_literal:
        if isinstance(a, str) and is_numeric_type(b):
            a = _literal_operand(b, a, culture)
        elif isinstance(b, str) and is_numeric_type(a):
            b = _literal_operand(a, b, culture)
    elif a_literal:
        b = _literal_operand(a, b, culture)
    elif b_literal:
        a = _literal_operand(b, a, culture)
    if a is EMPTY:
        if b is EMPTY:
            return 0
        a = "" if isinstance(b, str) else 0
    elif b is EMPTY:
        b = "" if isinstance(a, str) else 0
    if isinstance(a, str):
        if isinstance(b, str):
            return (a > b) - (a < b)
        return 1
    if isinstance(b, str):
        return -1
    assert isinstance(a, (bool, int, float)) and isinstance(b, (bool, int, float))
    x: int | float = (-1 if a else 0) if isinstance(a, bool) else a
    y: int | float = (-1 if b else 0) if isinstance(b, bool) else b
    return (x > y) - (x < y)


def _test(op: str, c: int) -> bool:
    if op == "=":
        return c == 0
    if op == "<>":
        return c != 0
    if op == "<":
        return c < 0
    if op == ">":
        return c > 0
    if op == "<=":
        return c <= 0
    return c >= 0


def compare_op(
    op: str,
    a: Value,
    b: Value,
    culture: Culture,
    a_literal: bool = False,
    b_literal: bool = False,
) -> Value:
    c = compare(a, b, culture, a_literal, b_literal)
    if c is None:
        return NULL
    return _test(op, c)

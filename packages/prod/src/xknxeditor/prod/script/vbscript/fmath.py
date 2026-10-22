"""Transcendental functions rounded correctly, so every OS computes the same double.

The C math libraries differ in the last bit between platforms. These compute in decimal with
extra digits and round once to the nearest double.
"""

from __future__ import annotations

import math
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal
from functools import cache

_DIGITS = 40
_MAX_EXACT_POWER = 64


def _context(prec: int) -> Context:
    return Context(prec=prec, Emax=MAX_EMAX, Emin=MIN_EMIN)


@cache
def _pi(prec: int) -> Decimal:
    ctx = _context(prec + 5)
    three = Decimal(3)
    last, t, s, n, na, d, da = Decimal(0), three, three, 1, 0, 0, 24
    while s != last:
        last = s
        n, na = n + na, na + 8
        d, da = d + da, da + 32
        t = ctx.divide(ctx.multiply(t, n), d)
        s = ctx.add(s, t)
    return _context(prec).plus(s)


def _to_double(value: Decimal) -> float:
    result = float(value)
    if math.isinf(result):
        raise OverflowError
    return result


def _reduced(x: float) -> tuple[Decimal, Context]:
    """``x`` reduced to [-pi, pi], with a context precise enough for the reduction."""
    exact = Decimal(x)
    prec = _DIGITS + max(0, exact.adjusted())
    ctx = _context(prec)
    two_pi = ctx.multiply(2, _pi(prec))
    turns = ctx.divide(exact, two_pi).to_integral_value()
    return ctx.subtract(exact, ctx.multiply(turns, two_pi)), _context(_DIGITS + 5)


def _sin_cos(r: Decimal, ctx: Context) -> tuple[Decimal, Decimal]:
    """The Taylor series of sine and cosine for ``|r| <= pi``."""
    eps = Decimal(10) ** -(_DIGITS + 20)
    square = ctx.multiply(r, r)
    sin, cos = Decimal(0), Decimal(0)
    term_s, term_c = r, Decimal(1)
    n = 1
    while abs(term_s) > eps * abs(r) or abs(term_c) > eps:
        sin, cos = ctx.add(sin, term_s), ctx.add(cos, term_c)
        term_s = ctx.divide(ctx.multiply(-term_s, square), (n + 1) * (n + 2))
        term_c = ctx.divide(ctx.multiply(-term_c, square), n * (n + 1))
        n += 2
    return sin, cos


def sin(x: float) -> float:
    if x == 0 or not math.isfinite(x):
        return math.sin(x)
    r, ctx = _reduced(x)
    return _to_double(_sin_cos(r, ctx)[0])


def cos(x: float) -> float:
    if not math.isfinite(x):
        return math.cos(x)
    r, ctx = _reduced(x)
    return _to_double(_sin_cos(r, ctx)[1])


def tan(x: float) -> float:
    if x == 0 or not math.isfinite(x):
        return math.tan(x)
    r, ctx = _reduced(x)
    s, c = _sin_cos(r, ctx)
    return _to_double(ctx.divide(s, c))


def atan(x: float) -> float:
    if x == 0 or not math.isfinite(x):
        return math.atan(x)
    ctx = _context(_DIGITS + 5)
    v = Decimal(x)
    sign = -1 if v < 0 else 1
    v = abs(v)
    invert = v > 1
    if invert:
        v = ctx.divide(1, v)
    # atan(v) = 2 atan(v / (1 + sqrt(1 + v^2))) brings v near 0, where the series converges fast.
    halvings = 0
    while v > Decimal("0.05"):
        v = ctx.divide(v, ctx.add(1, ctx.sqrt(ctx.add(1, ctx.multiply(v, v)))))
        halvings += 1
    square = ctx.multiply(v, v)
    total, term, n = Decimal(0), v, 1
    while True:
        step = ctx.divide(term, n)
        if not step or abs(step) < abs(total) * Decimal(10) ** -(_DIGITS + 3):
            break
        total = ctx.add(total, step)
        term = ctx.multiply(-term, square)
        n += 2
    total = ctx.multiply(total, 2**halvings)
    if invert:
        total = ctx.subtract(ctx.divide(_pi(_DIGITS + 5), 2), total)
    return _to_double(total) * sign


def exp(x: float) -> float:
    return _to_double(_context(_DIGITS).exp(Decimal(x)))


def log(x: float) -> float:
    return _to_double(_context(_DIGITS).ln(Decimal(x)))


def power(x: float, y: float) -> float:
    """``x ^ y``; the caller has ruled out a zero base with a negative exponent and a negative
    base with a fractional one."""
    if y == 0:
        return 1.0
    if x == 0:
        return 0.0
    if y.is_integer() and x.is_integer() and 0 < y <= _MAX_EXACT_POWER:
        return _to_double(Decimal(int(x) ** int(y)))
    ctx = _context(_DIGITS)
    if y.is_integer():
        return _to_double(ctx.power(Decimal(x), int(y)))
    return _to_double(ctx.exp(ctx.multiply(Decimal(y), ctx.ln(Decimal(x)))))

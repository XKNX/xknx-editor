"""The transcendental functions are rounded correctly, so every OS computes the same double."""

from __future__ import annotations

import math

import pytest

from xknxeditor.prod.script.vbscript import fmath


def test_sine_is_rounded_correctly() -> None:
    # sin(2.5) = 0.598472144103956494...; some C libraries return the double one bit below.
    assert fmath.sin(2.5).hex() == "0x1.326af0dcfcab1p-1"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (fmath.exp(1.0), math.e),
        (4 * fmath.atan(1.0), math.pi),
        (fmath.log(10.0), 2.302585092994046),
        (fmath.cos(0.0), 1.0),
        (fmath.sin(-0.0), -0.0),
        (fmath.tan(math.pi / 4), 0.9999999999999999),
        (fmath.atan(-1e300), -math.pi / 2),
        (fmath.power(2.0, 0.5), math.sqrt(2.0)),
        (fmath.power(1.1, 3.0), 1.3310000000000004),
        (fmath.power(2.0, -3.0), 0.125),
        (fmath.power(0.0, 0.0), 1.0),
    ],
)
def test_known_values(value: float, expected: float) -> None:
    assert value == expected


def test_large_arguments_are_reduced_exactly() -> None:
    # 1e22 is a multiple of no simple fraction of pi; the exact reduction gives this sine.
    assert fmath.sin(1e22) == -0.8522008497671888


def test_overflow() -> None:
    with pytest.raises(OverflowError):
        fmath.exp(1000.0)
    with pytest.raises(OverflowError):
        fmath.power(10.0, 400.0)

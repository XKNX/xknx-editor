"""Packed TypeTime units: PackedDaysHoursMinutesAndSeconds and PackedSecondsAndMilliseconds."""

from __future__ import annotations

import pytest

from xknxeditor.namespaces.intermediate.parameter_type_t_type_time import (
    ParameterTypeTypeTime,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_time_unit import (
    ParameterTypeTypeTimeUnit,
)
from xknxeditor.prod.parser_v2.encode import _decode_value, _encode_value

DHMS = ParameterTypeTypeTimeUnit.PACKED_DAYS_HOURS_MINUTES_AND_SECONDS
SMS = ParameterTypeTypeTimeUnit.PACKED_SECONDS_AND_MILLISECONDS


def _tc(
    unit: ParameterTypeTypeTimeUnit, size: int, maximum: int
) -> ParameterTypeTypeTime:
    return ParameterTypeTypeTime(
        size_in_bit=size, unit=unit, min_inclusive=0, max_inclusive=maximum
    )


@pytest.mark.parametrize(
    ("seconds", "octets"),
    [
        ("1", "010000"),
        ("30", "1e0000"),
        ("60", "000100"),
        ("300", "000500"),
        ("600", "000a00"),
        ("3600", "000001"),
        ("3661", "010101"),
        ("86399", "3b3b17"),
        ("86400", "000020"),
    ],
)
def test_packed_days_hours_minutes_seconds(seconds: str, octets: str) -> None:
    tc = _tc(DHMS, 24, 86400)
    raw = _encode_value(seconds, 24, tc, little_endian=False)
    assert raw is not None
    assert raw.to_bytes(3, "big").hex() == octets
    assert _decode_value(raw, 24, tc, little_endian=False) == seconds


def test_packed_days_hours_minutes_seconds_rejects_invalid_octets() -> None:
    tc = _tc(DHMS, 24, 86400)
    # 60 seconds in the seconds octet is not a value of this type.
    assert _decode_value(0x3C0000, 24, tc, little_endian=False) is None


@pytest.mark.parametrize(
    ("milliseconds", "octets"),
    [
        ("0", "0000"),
        ("250", "fa00"),
        ("1000", "0001"),
        ("1500", "f441"),
        ("59999", "e7fb"),
    ],
)
def test_packed_seconds_and_milliseconds(milliseconds: str, octets: str) -> None:
    tc = _tc(SMS, 16, 59999)
    raw = _encode_value(milliseconds, 16, tc, little_endian=False)
    assert raw is not None
    assert raw.to_bytes(2, "big").hex() == octets
    assert _decode_value(raw, 16, tc, little_endian=False) == milliseconds


def test_plain_seconds_unit_unchanged() -> None:
    tc = _tc(ParameterTypeTypeTimeUnit.SECONDS, 24, 86400)
    raw = _encode_value("600", 24, tc, little_endian=False)
    assert raw is not None
    assert raw.to_bytes(3, "big").hex() == "000258"

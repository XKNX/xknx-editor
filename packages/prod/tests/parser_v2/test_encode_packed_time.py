"""PackedDaysHoursMinutesAndSeconds: seconds, minutes, hours - one octet each."""

from __future__ import annotations

import pytest

from xknxeditor.namespaces.intermediate.parameter_type_t_type_time import (
    ParameterTypeTypeTime,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_time_unit import (
    ParameterTypeTypeTimeUnit,
)
from xknxeditor.prod.parser_v2.encode import _decode_value, _encode_value


def _tc(unit: ParameterTypeTypeTimeUnit) -> ParameterTypeTypeTime:
    return ParameterTypeTypeTime(
        size_in_bit=24, unit=unit, min_inclusive=1, max_inclusive=86400
    )


PACKED = ParameterTypeTypeTimeUnit.PACKED_DAYS_HOURS_MINUTES_AND_SECONDS


@pytest.mark.parametrize(
    ("seconds", "octets"),
    [
        ("30", "1e0000"),  # ETS capture (Steinel)
        ("60", "000100"),
        ("300", "000500"),  # ETS capture (Steinel)
        ("600", "000a00"),  # B.E.G. PD2N default "Nachlaufzeit" 10 min
        ("3600", "000001"),
        ("86400", "000018"),
    ],
)
def test_packed_dhms_encodes_seconds_minutes_hours(seconds: str, octets: str) -> None:
    raw = _encode_value(seconds, 24, _tc(PACKED), little_endian=False)
    assert raw is not None
    assert raw.to_bytes(3, "big").hex() == octets
    assert _decode_value(raw, 24, _tc(PACKED), little_endian=False) == seconds


def test_plain_seconds_unit_unchanged() -> None:
    tc = _tc(ParameterTypeTypeTimeUnit.SECONDS)
    raw = _encode_value("600", 24, tc, little_endian=False)
    assert raw is not None
    assert raw.to_bytes(3, "big").hex() == "000258"

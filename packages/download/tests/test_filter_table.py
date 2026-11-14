"""Tests for the coupler filter-table bitmap and routed-set computation."""

from xknxeditor.download.filter_table import (
    FILTER_TABLE_SIZE,
    addresses_in_filter_table,
    build_filter_table,
    compute_coupler_filter_table,
    is_coupler_address,
    routed_group_addresses,
)


def test_build_bit_indexing() -> None:
    # ga 0 -> byte 0 bit 0; ga 7 -> byte 0 bit 7; ga 8 -> byte 1 bit 0; ga 65535 -> last byte bit 7.
    table = build_filter_table([0, 7, 8, 65535])
    assert len(table) == FILTER_TABLE_SIZE
    assert table[0] == 0b1000_0001
    assert table[1] == 0b0000_0001
    assert table[8191] == 0b1000_0000


def test_round_trip() -> None:
    gas = [0, 1, 100, 2305, 30000, 65535]  # 2305 = 1/1/1 in 3-level
    table = build_filter_table(gas)
    assert addresses_in_filter_table(table) == sorted(gas)


def test_truncation_drops_out_of_range() -> None:
    # BCU1 coupler stores 3584 bytes -> first 28672 GAs; higher ones are dropped.
    table = build_filter_table([100, 28671, 28672, 65535], length=3584)
    assert len(table) == 3584
    assert addresses_in_filter_table(table) == [100, 28671]


def test_out_of_range_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        build_filter_table([65536])
    with pytest.raises(ValueError):
        build_filter_table([-1])


def test_is_coupler_address() -> None:
    assert is_coupler_address(0x1100)  # 1.1.0
    assert is_coupler_address(0x1000)  # 1.0.0 (area/backbone coupler)
    assert not is_coupler_address(0x1101)  # 1.1.1
    assert not is_coupler_address(0x110A)  # 1.1.10


def test_routed_crossing_only() -> None:
    # Only addresses linked on BOTH sides cross the coupler.
    inside = [10, 11, 12]
    outside = [11, 12, 13]
    assert routed_group_addresses(inside, outside) == [11, 12]


def _ia(area: int, line: int, device: int) -> int:
    return (area << 12) | (line << 8) | device


def test_compute_coupler_filter_line_coupler() -> None:
    # Line coupler 1.1.0. Devices in line 1.1 are "inside"; devices elsewhere are "outside".
    # GA 100: linked inside (1.1.5) and outside (1.2.5) -> crosses. GA 200: only inside -> blocked.
    # GA 300: only outside -> blocked.
    coupler = _ia(1, 1, 0)
    device_gas = {
        _ia(1, 1, 5): {100, 200},
        _ia(1, 1, 6): {200},
        _ia(1, 2, 5): {100, 300},
        _ia(1, 2, 6): {300},
    }
    table = compute_coupler_filter_table(coupler, device_gas)
    assert addresses_in_filter_table(table) == [100]


def test_compute_coupler_filter_area_coupler() -> None:
    # Area coupler 1.0.0: the whole area 1 is "inside"; area 2 is "outside".
    coupler = _ia(1, 0, 0)
    device_gas = {
        _ia(1, 1, 5): {100},
        _ia(1, 2, 5): {200},
        _ia(2, 1, 5): {100, 200},  # outside area -> both GAs cross
    }
    table = compute_coupler_filter_table(coupler, device_gas)
    assert addresses_in_filter_table(table) == [100, 200]


def test_compute_coupler_filter_length_and_extras() -> None:
    coupler = _ia(1, 1, 0)
    device_gas = {_ia(1, 1, 5): {10}, _ia(1, 2, 5): {10}}
    table = compute_coupler_filter_table(
        coupler, device_gas, length=3584, unfiltered=[7000], additional=[9000]
    )
    assert len(table) == 3584
    assert addresses_in_filter_table(table) == [10, 7000, 9000]


def test_routed_includes_unfiltered_and_additional() -> None:
    routed = routed_group_addresses(
        inside=[10], outside=[99], unfiltered=[500, 500], additional=[600]
    )
    # 10 does not cross (not outside); unfiltered + additional always route; de-duplicated + sorted.
    assert routed == [500, 600]


def test_routed_without_a_partner_requirement() -> None:
    # 0x0A03 only behind (e.g. a visualisation over KNXnet/IP is its partner), 0x0A04 both sides
    behind, front = [0x0A03, 0x0A04], [0x0A04, 0x0B00]
    assert routed_group_addresses(behind, front) == [0x0A04]
    assert routed_group_addresses(behind, front, partner_required=False) == [
        0x0A03,
        0x0A04,
    ]
    assert routed_group_addresses(
        behind, front, unfiltered=[0x0C00], partner_required=False
    ) == [0x0A03, 0x0A04, 0x0C00]


def test_compute_coupler_filter_ets_rule() -> None:
    coupler = (1 << 12) | (1 << 8)  # 1.1.0
    behind = (1 << 12) | (1 << 8) | 5  # 1.1.5
    front = (1 << 12) | 3  # 1.0.3
    links = {behind: [0x0A03, 0x0A04], front: [0x0A04]}
    strict = compute_coupler_filter_table(coupler, links, length=3584)
    ets = compute_coupler_filter_table(
        coupler, links, length=3584, partner_required=False
    )
    assert addresses_in_filter_table(strict) == [0x0A04]
    assert addresses_in_filter_table(ets) == [0x0A03, 0x0A04]

"""Tests for the System B group communication table formatter.

The golden vectors marked "device" are the exact bytes read from a real System B
device (System B sample, individual address 1.1.5) over the bus.
"""

from __future__ import annotations

import pytest

from xknxeditor.download.errors import ImageError
from xknxeditor.download.tables import Association
from xknxeditor.download.tables_systemb import (
    association_table_wide,
    build_association_table_b,
    build_group_address_table_b,
    build_group_object_table_b,
    group_address_index_b,
)


def test_address_table_has_two_octet_count_and_no_device_address() -> None:
    data = build_group_address_table_b([0x0B0A, 0x0B08, 0x0B09])
    # count = 3 (big-endian, no leading device address), then sorted addresses
    assert data == bytes.fromhex("0003 0b08 0b09 0b0a".replace(" ", ""))


def test_address_table_matches_real_device() -> None:
    # Sample: 43 consecutive addresses 0x0b08..0x0b32 plus 0x1c07.
    addresses = [*range(0x0B08, 0x0B33), 0x1C07]
    expected = "002c" + "".join(f"{a:04x}" for a in sorted(addresses))
    assert build_group_address_table_b(addresses).hex() == expected


def test_address_index_is_one_based_over_sorted_addresses() -> None:
    assert group_address_index_b([0x0B0A, 0x0B08, 0x0B09]) == {
        0x0B08: 1,
        0x0B09: 2,
        0x0B0A: 3,
    }


def test_association_table_narrow_entries() -> None:
    associations = [
        Association(group_address_index=0, group_object_number=1, sending=True),
        Association(group_address_index=1, group_object_number=2, sending=True),
    ]
    data = build_association_table_b(associations)
    # count = 2, then [gaindex+1][object number] per entry
    assert data == bytes.fromhex("0002 0101 0202".replace(" ", ""))


def test_association_table_wide_entries() -> None:
    associations = [
        Association(group_address_index=0, group_object_number=1, sending=True)
    ]
    data = build_association_table_b(associations, wide=True)
    assert data == bytes.fromhex("0001 0001 0001".replace(" ", ""))


def test_association_ordering_sending_first_then_object_then_index() -> None:
    associations = [
        Association(group_address_index=5, group_object_number=2, sending=False),
        Association(group_address_index=1, group_object_number=2, sending=True),
        Association(group_address_index=0, group_object_number=1, sending=True),
    ]
    data = build_association_table_b(associations)
    # sending (obj 1 idx 0), sending (obj 2 idx 1), then receiving (obj 2 idx 5);
    # each entry is [group address index + 1][object number]
    assert data == bytes.fromhex("0003 0101 0202 0602".replace(" ", ""))


def test_group_object_table_linked_records_and_gaps() -> None:
    # Objects 1, 2 and 10 linked; everything up to 10 else stays 00 00.
    descriptors = {1: (0x4F, 0x07), 2: (0xF7, 0x07), 10: (0x4F, 0x00)}
    data = build_group_object_table_b(descriptors, highest_number=10)
    expected = (
        "000a"  # count = highest number = 10
        "4f07"  # object 1
        "f707"  # object 2
        + "0000" * 7  # objects 3..9
        + "4f00"  # object 10
    )
    assert data.hex() == expected


def test_group_object_table_matches_real_device_prefix() -> None:
    # Sample device prefix: obj 1 = 0x4f07, obj 2 = 0xf707, obj 3..9 gaps.
    descriptors = {1: (0x4F, 0x07), 2: (0xF7, 0x07)}
    data = build_group_object_table_b(descriptors, highest_number=200)
    assert data[:2] == bytes.fromhex("00c8")  # count = 200
    assert data[2:6] == bytes.fromhex("4f07f707")
    assert data[6:20] == b"\x00" * 14  # objects 3..9 unlinked


def test_octet_overflow_is_rejected() -> None:
    with pytest.raises(ImageError):
        build_association_table_b(
            [Association(group_address_index=300, group_object_number=1)]
        )


def test_association_table_wide_rule() -> None:
    assert not association_table_wide(address_count=254, highest_object_number=255)
    assert association_table_wide(address_count=255, highest_object_number=1)
    assert association_table_wide(address_count=1, highest_object_number=256)

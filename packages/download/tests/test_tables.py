"""Group communication table formatting, checked byte-exact against real hardware.

The expected bytes were read from a Berker BE-GT2Tx.01 (1.1.74) after genuine
programming, so these assert the formatters reproduce the device's output exactly.
"""

from __future__ import annotations

import pytest

from xknxeditor.download.errors import ImageError
from xknxeditor.download.tables import (
    UNUSED_SLOT,
    Association,
    association_slots,
    build_association_list,
    build_association_table,
    build_group_address_table,
    group_address_index,
    memory_size_code,
)

# Device 1.1.74 = 0x114a; its 18 group addresses (sorted ascending).
_DEVICE_74 = 0x114A
_GAS_74 = [
    0x0B00,
    0x0B02,
    0x0B03,
    0x0B2B,
    0x102C,
    0x1030,
    0x1031,
    0x1033,
    0x1035,
    0x1800,
    0x1801,
    0x1802,
    0x1803,
    0x181B,
    0x181C,
    0x181D,
    0x181E,
    0x2828,
]

# (group address index into the address table, com object number)
_ASSOC_74 = [
    Association(12, 0),
    Association(13, 1),
    Association(17, 2),
    Association(16, 3),
    Association(5, 10),
    Association(7, 11),
    Association(9, 13),
    Association(6, 21),
    Association(8, 23),
    Association(10, 30),
    Association(11, 31),
    Association(15, 32),
    Association(14, 33),
    Association(3, 106),
    Association(18, 107),
    Association(1, 112),
    Association(2, 114),
    Association(4, 122),
]


def test_group_address_table_matches_device() -> None:
    expected = bytes.fromhex(
        "13"  # count = 1 + 18
        "114a"  # device individual address 1.1.74
        "0b00 0b02 0b03 0b2b 102c 1030 1031 1033 1035"
        "1800 1801 1802 1803 181b 181c 181d 181e 2828".replace(" ", "")
    )
    assert build_group_address_table(_DEVICE_74, _GAS_74) == expected


def test_group_address_table_sorts_and_dedups() -> None:
    table = build_group_address_table(0x1101, [0x0B02, 0x0B00, 0x0B02])
    assert table == bytes.fromhex("0311010b00 0b02".replace(" ", ""))


def test_group_address_index_is_one_based() -> None:
    index = group_address_index([0x0B02, 0x0B00])
    assert index == {0x0B00: 1, 0x0B02: 2}


def test_association_table_matches_device() -> None:
    expected = bytes.fromhex(
        "12"  # count = 18
        "0c00 0d01 1102 1003 050a 070b 090d 0615 0817"
        "0a1e 0b1f 0f20 0e21 036a 126b 0170 0272 047a".replace(" ", "")
    )
    assert build_association_table(_ASSOC_74) == expected


def test_association_table_orders_by_group_object_number() -> None:
    table = build_association_table(
        [Association(5, 20), Association(3, 7), Association(9, 12)]
    )
    assert table == bytes.fromhex("030307 090c 0514".replace(" ", ""))


def test_group_address_table_rejects_overflow() -> None:
    with pytest.raises(ImageError):
        build_group_address_table(0x1101, list(range(300)))


def test_association_slots_hold_each_objects_sending_address() -> None:
    """The unused slot of object 1 skips the receiving link on the address object 0
    sends on from its slot and takes the next one. A second sending association of
    object 2 counts as receiving."""
    slots = association_slots(
        [
            Association(1, 0),
            Association(1, 1, sending=False),
            Association(3, 1, sending=False),
            Association(2, 2),
            Association(4, 2),
        ],
        3,
    )
    assert build_association_list(slots) == bytes.fromhex(
        "05 0100 0301 0202 0101 0402".replace(" ", "")
    )
    assert [slot.sending for slot in slots] == [True, False, True, False, False]


def test_association_slots_of_objects_without_links_are_unused() -> None:
    slots = association_slots([Association(1, 1)], 3)
    assert build_association_list(slots) == bytes.fromhex(
        "03 fe00 0101 fe02".replace(" ", "")
    )
    assert slots[0] == Association(UNUSED_SLOT, 0, sending=False)


@pytest.mark.parametrize(
    ("object_size", "code"),
    [
        ("1 Bit", 0),
        ("14 Bytes", 14),
        ("5 Bytes", 15),
        ("LegacyVarData", 15),
        ("15 Bytes", 21),
        ("50 Bytes", 56),
        ("51 Bytes", 0),
    ],
)
def test_memory_size_codes(object_size: str, code: int) -> None:
    assert memory_size_code(object_size) == code

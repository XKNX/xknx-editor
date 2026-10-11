"""Round-trip tests: decode must invert the hardware-validated encoders."""

from __future__ import annotations

import pytest

from xknxeditor.download.tables import (
    Association,
    build_association_table,
    build_group_address_table,
    com_object_flag_byte,
)
from xknxeditor.download.tables_systemb import (
    build_association_table_b,
    build_group_address_table_b,
    build_group_object_table_b,
    group_address_index_b,
)
from xknxeditor.recover.tables_decode import (
    TableDecodeError,
    decode_association_table,
    decode_association_table_b,
    decode_com_object_table,
    decode_flag_byte,
    decode_group_address_table,
    decode_group_address_table_b,
    decode_group_object_records_b,
    decode_group_object_table_b,
)


def test_flag_byte_round_trip() -> None:
    byte = com_object_flag_byte(
        priority="Low",
        communication=True,
        read=True,
        write=False,
        transmit=True,
        update=False,
        read_on_init=True,
    )
    priority, comm, read, write, transmit, update, roi = decode_flag_byte(byte)
    assert (priority, comm, read, write, transmit, update, roi) == (
        "Low",
        True,
        True,
        False,
        True,
        False,
        True,
    )


def test_group_address_table_round_trip() -> None:
    device_address = 0x1101  # 1.1.1
    gas = [0x0B00, 0x0B01, 0x114A]
    data = build_group_address_table(device_address, gas)
    decoded_device, decoded_gas = decode_group_address_table(data)
    assert decoded_device == device_address
    assert decoded_gas == sorted(gas)


def test_association_table_round_trip_memory_mapped() -> None:
    gas = [0x0B00, 0x0B01, 0x114A]
    index = {ga: i + 1 for i, ga in enumerate(sorted(gas))}
    associations = [
        Association(index[0x0B00], group_object_number=0, sending=True),
        Association(index[0x0B01], group_object_number=0, sending=False),
        Association(index[0x114A], group_object_number=2, sending=True),
    ]
    data = build_association_table(associations)
    _, decoded_gas = decode_group_address_table(build_group_address_table(0x1101, gas))
    links = decode_association_table(data, decoded_gas)
    # Group object 0 gets both addresses; the first (sending-ordered) is sending.
    by_ga = {link.group_address: link for link in links}
    assert by_ga[0x0B00].group_object_number == 0
    assert by_ga[0x0B00].sending is True
    assert by_ga[0x0B01].sending is False
    assert by_ga[0x114A].group_object_number == 2
    assert by_ga[0x114A].sending is True


def test_com_object_table_round_trip() -> None:
    seed = bytes([0x87, 0x07, 0x00, 0x07, 0x88]) + bytes(4 * 3)
    flags = com_object_flag_byte(
        priority="Low",
        communication=True,
        read=False,
        write=True,
        transmit=True,
        update=False,
        read_on_init=False,
    )
    from xknxeditor.download.tables import ComObjectDescriptor, build_com_object_table

    data, _ = build_com_object_table(
        seed, [ComObjectDescriptor(number=1, flags=flags, size=7)]
    )
    decoded = decode_com_object_table(data)
    obj = decoded[1]
    assert obj.communication is True
    assert obj.write is True
    assert obj.transmit is True
    assert obj.size_code == 7
    assert obj.object_size == "1 Byte"


def test_group_address_table_b_round_trip() -> None:
    gas = [0x0B00, 0x0B01, 0x114A]
    data = build_group_address_table_b(gas)
    assert decode_group_address_table_b(data) == sorted(gas)


def test_association_table_b_round_trip() -> None:
    # Mirror the real image path: image.py builds Association with the ZERO-based
    # sorted position (group_address_index_b[ga] - 1), and the encoder adds one, so
    # the decoder must invert with reference - 1. Feeding the 0-based index here is
    # what exposes the offset contract end-to-end.
    gas = [0x0B00, 0x0B01, 0x114A]
    index = group_address_index_b(gas)
    associations = [
        Association(index[0x0B00] - 1, group_object_number=1, sending=True),
        Association(index[0x114A] - 1, group_object_number=1, sending=False),
    ]
    data = build_association_table_b(associations)
    decoded_gas = decode_group_address_table_b(build_group_address_table_b(gas))
    links = decode_association_table_b(data, decoded_gas)
    by_ga = {link.group_address: link for link in links}
    assert set(by_ga) == {0x0B00, 0x114A}  # exactly the linked addresses, no shift
    assert by_ga[0x0B00].group_object_number == 1
    assert by_ga[0x0B00].sending is True
    assert by_ga[0x114A].sending is False


def test_association_table_b_first_address_round_trip() -> None:
    # Regression for the off-by-one: a link to the FIRST address must survive.
    gas = [0x0B00, 0x0B01]
    index = group_address_index_b(gas)
    associations = [Association(index[0x0B00] - 1, group_object_number=2, sending=True)]
    data = build_association_table_b(associations)
    links = decode_association_table_b(
        data, decode_group_address_table_b(build_group_address_table_b(gas))
    )
    assert [(link.group_address, link.group_object_number) for link in links] == [
        (0x0B00, 2)
    ]


def test_association_table_b_wide_round_trip() -> None:
    gas = [0x0B00, 0x0B01]
    index = group_address_index_b(gas)
    associations = [Association(index[0x0B01] - 1, group_object_number=5, sending=True)]
    data = build_association_table_b(associations, wide=True)
    links = decode_association_table_b(data, sorted(gas))
    assert links == [
        type(links[0])(group_address=0x0B01, group_object_number=5, sending=True)
    ]


def test_group_object_table_b_round_trip() -> None:
    flags = com_object_flag_byte(
        priority="Low",
        communication=True,
        read=True,
        write=False,
        transmit=True,
        update=False,
        read_on_init=False,
    )
    data = build_group_object_table_b({1: (flags, 7), 3: (flags, 8)}, highest_number=3)
    decoded = decode_group_object_table_b(data)
    assert set(decoded) == {1, 3}  # object 2 is an unlinked 00 00 slot
    assert decoded[1].communication is True
    assert decoded[3].size_code == 8


def test_group_object_records_b_from_a_slice() -> None:
    flags = com_object_flag_byte(
        priority="Low",
        communication=True,
        read=False,
        write=True,
        transmit=False,
        update=False,
        read_on_init=False,
    )
    data = build_group_object_table_b(
        {20: (flags, 0), 22: (flags, 10)}, highest_number=30
    )
    # Records 20..22 start after the two-octet count at 2 + (20 - 1) * 2.
    decoded = decode_group_object_records_b(20, data[40:46])
    assert set(decoded) == {20, 22}
    assert decoded[22].size_code == 10
    assert decoded[20].write is True


def test_decode_rejects_truncated_table() -> None:
    with pytest.raises(TableDecodeError):
        decode_group_address_table(bytes([0x05, 0x11]))


def test_record_ranges_merge_nearby_numbers() -> None:
    from xknxeditor.recover.read_config import (
        _record_ranges,  # pyright: ignore[reportPrivateUsage]
    )

    assert _record_ranges([22, 20, 21, 290, 299, 310], 10) == [
        (20, 3),
        (290, 10),
        (310, 1),
    ]

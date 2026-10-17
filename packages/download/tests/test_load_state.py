"""Tests for load event encodings.

Byte layouts and the worked examples are taken from KNX Standard 3/5/2
"Management Procedures", section 3.31.3 "DMP_LoadStateMachineWrite_RCo_IO",
where every load event written to PID_LOAD_STATE_CONTROL is a 10 octet value.
"""

from __future__ import annotations

import pytest

from xknxeditor.download import load_state as ls


def test_simple_events_are_ten_octets() -> None:
    for event in (ls.start_loading(), ls.load_complete(), ls.unload()):
        assert len(event) == ls.LOAD_STATE_CONTROL_SIZE


def test_start_loading() -> None:
    # 3.31.3.2: data = 01 00 ...
    assert ls.start_loading() == bytes([0x01]) + bytes(9)


def test_load_complete() -> None:
    # 3.31.3.3: data = 02 00 ...
    assert ls.load_complete() == bytes([0x02]) + bytes(9)


def test_unload() -> None:
    # 3.31.3.1: data = 04 00 ...
    assert ls.unload() == bytes([0x04]) + bytes(9)


def test_memory_mapped_control_packs_lsm_index() -> None:
    # BIM M112: octet 0 is (lsm_idx << 4) | event, padded to 12 octets.
    record = ls.memory_mapped_control(1, ls.unload())
    assert record == bytes.fromhex("140000000000000000000000")
    assert len(record) == ls.MEMORY_MAPPED_CONTROL_SIZE
    loading = ls.memory_mapped_control(3, ls.start_loading())
    assert loading[0] == 0x31
    assert loading[1:] == bytes(11)


def test_alloc_absolute_data_segment() -> None:
    # AllocAbsDataSeg (segment type 0): 03 00 SSSS LLLL AA TT MM 00.
    # Load Controls cookbook example: start 0x4000, end 0x41FE => length 0x01FF,
    # read+write access (0xFF), EEPROM (0x03), checksum control enabled (0x80).
    event = ls.alloc_absolute_segment(
        ls.SegmentType.ABS_DATA,
        start_address=0x4000,
        length=0x01FF,
        access_attributes=0xFF,
        memory_type=0x03,
        memory_attributes=0x80,
    )
    assert event == bytes.fromhex("0300400001FFFF038000")


def test_alloc_absolute_stack_segment() -> None:
    # AllocAbsStackSeg (segment type 1): same layout as data segment.
    event = ls.alloc_absolute_segment(
        ls.SegmentType.ABS_STACK,
        start_address=0x0700,
        length=0x0290,
        access_attributes=0x00,
        memory_type=0x02,
        memory_attributes=0x00,
    )
    assert event == bytes.fromhex("0301070002900002 0000".replace(" ", ""))
    assert len(event) == ls.LOAD_STATE_CONTROL_SIZE


def test_alloc_task_segment() -> None:
    # AllocAbsTaskSeg (segment type 2): 03 02 SSSS PP MMMM TTTT VV.
    # Cookbook example: start 0x43FC, PEI type 0x01, manufacturer 0x0072,
    # device type 0x0001, version 0x01.
    event = ls.alloc_task_segment(
        start_address=0x43FC,
        pei_type=0x01,
        application_id=bytes.fromhex("0072000101"),
    )
    assert event == bytes.fromhex("030243FC010072000101")
    assert len(event) == ls.LOAD_STATE_CONTROL_SIZE


def test_task_pointer_pads_reserved() -> None:
    # TaskPtr (segment type 3): 03 03 IIII SSSS PPPP 0000.
    event = ls.task_pointer(0x1234, 0x5678, 0x9ABC)
    assert event == bytes.fromhex("0303123456789ABC0000")


def test_task_control_1() -> None:
    # TaskCtrl1 (segment type 4): 03 04 AAAA NN 00...
    event = ls.task_control_1(interface_object_address=0x1000, interface_object_count=3)
    assert event == bytes.fromhex("0304100003 0000000000".replace(" ", ""))


def test_task_control_2_is_full_length() -> None:
    # TaskCtrl2 (segment type 5): 03 05 CCCC OOOO 1111 2222.
    event = ls.task_control_2(0x1111, 0x2222, 0x3333, 0x4444)
    assert event == bytes.fromhex("03051111222233334444")
    assert len(event) == ls.LOAD_STATE_CONTROL_SIZE


def test_relative_allocation() -> None:
    # Relative allocation (subtype 0Ah): 03 0A <count(2)> 00...
    assert ls.relative_allocation(0x0100) == bytes.fromhex("030A0100000000000000")


def test_data_relative_allocation() -> None:
    # Data relative allocation (subtype 0Bh, System B):
    # 03 0B <size(4)> <mode(1)> <fill(1)> <reserved(2)>.
    event = ls.data_relative_allocation(0x00001E15, mode=0x01, fill=0x00)
    assert event == bytes.fromhex("030B00001E1501000000")
    assert len(event) == ls.LOAD_STATE_CONTROL_SIZE


def test_task_segment_rejects_wrong_application_id_length() -> None:
    with pytest.raises(ValueError, match="5 octets"):
        ls.alloc_task_segment(0x0000, 0x00, b"\x00")


def test_absolute_segment_rejects_task_type() -> None:
    with pytest.raises(ValueError, match="ABS_DATA or ABS_STACK"):
        ls.alloc_absolute_segment(ls.SegmentType.ABS_TASK, 0, 0)

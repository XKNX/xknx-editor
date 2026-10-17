"""Load State Machine states and load events.

A loadable part of a KNX device (address table, association table, group object
table, application program, ...) is guarded by a Load State Machine. Property
based masks (Realisation Type 1) are driven by writing *load events* to
``PID_LOAD_STATE_CONTROL`` (property id 5) of the part's interface object.
BIM M112 masks (``LoadControl_M112``) share one 11-octet memory record instead;
the LSM index occupies the high nibble of the first octet.

The state machine and its load events are defined in KNX Standard v3.0.0,
Chapter 3/5/1 "Resources", section 4.23 "Load State Machine" (the property based
Realisation Type 1, section 4.23.2) and section 4.2.5 "PID_LOAD_STATE_CONTROL
(PID = 5)"; the procedures that issue them are in Chapter 3/5/2 "Management
Procedures" (DM_LoadStateMachineWrite). Every property load event is a 10 octet
control value; unused octets are zero.
"""

from __future__ import annotations

from enum import IntEnum

from xknx.profile.const import ResourceGenericPropertyId

PID_LOAD_STATE_CONTROL = ResourceGenericPropertyId.PID_LOAD_STATE_CONTROL
# Number of octets one control element occupies.
LOAD_STATE_CONTROL_SIZE = 10
# BIM M112 (LoadControl_M112) packs the LSM index into the first octet.
# ETS writes 11 octets (A_Memory_Write count=11) at the shared control address.
MEMORY_MAPPED_CONTROL_SIZE = 11


class LoadState(IntEnum):
    """State reported when reading ``PID_LOAD_STATE_CONTROL``.

    ``UNLOADING`` and ``LOAD_COMPLETING`` are optional transient states a device
    may report while an unload or load-complete is still in progress.
    """

    UNLOADED = 0
    LOADED = 1
    LOADING = 2
    ERROR = 3
    UNLOADING = 4
    LOAD_COMPLETING = 5


class LoadEvent(IntEnum):
    """First octet of a load event written to ``PID_LOAD_STATE_CONTROL``."""

    START_LOADING = 1
    LOAD_COMPLETE = 2
    ADDITIONAL = 3
    UNLOAD = 4


class SegmentType(IntEnum):
    """Subtype of an ``ADDITIONAL`` load event (segment allocation)."""

    ABS_DATA = 0
    ABS_STACK = 1
    ABS_TASK = 2
    TASK_PTR = 3
    TASK_CTRL_1 = 4
    TASK_CTRL_2 = 5
    RELATIVE_ALLOCATION = 0x0A
    DATA_RELATIVE_ALLOCATION = 0x0B


def _pad(data: bytes) -> bytes:
    """Pad a control value to the fixed load state control element size."""
    if len(data) > LOAD_STATE_CONTROL_SIZE:
        raise ValueError(
            f"load event too long: {len(data)} > {LOAD_STATE_CONTROL_SIZE}"
        )
    return data + bytes(LOAD_STATE_CONTROL_SIZE - len(data))


def start_loading() -> bytes:
    """Load event moving the machine to ``LOADING``."""
    return _pad(bytes([LoadEvent.START_LOADING]))


def load_complete() -> bytes:
    """Load event moving the machine to ``LOADED``."""
    return _pad(bytes([LoadEvent.LOAD_COMPLETE]))


def unload() -> bytes:
    """Load event moving the machine to ``UNLOADED``."""
    return _pad(bytes([LoadEvent.UNLOAD]))


def memory_mapped_control(lsm_idx: int, event: bytes) -> bytes:
    """Pack a property load event into the 11-octet ADM1/M112 memory record.

    Octet 0 is ``(lsm_idx << 4) | event``. Additional events insert a reserved
    zero after the segment type. Absolute data/stack segments keep the property
    format's length (ETS ``LoadControl_M112`` on MV-0701: start ``0x4000``,
    length ``0x01FF``). Unload/load/complete stay a single opcode plus padding.
    """
    if not event:
        raise ValueError("empty load event")
    if not 0 <= lsm_idx <= 0x0F:
        raise ValueError(f"load state machine index {lsm_idx} does not fit four bits")
    opcode = (lsm_idx << 4) | (event[0] & 0x0F)
    if event[0] == LoadEvent.ADDITIONAL and len(event) >= 2:
        payload = event[1:]
        record = bytes([opcode, payload[0], 0x00]) + bytes(payload[1:])
    else:
        record = bytes([opcode]) + event[1:]
    if len(record) > MEMORY_MAPPED_CONTROL_SIZE:
        raise ValueError(
            f"load event too long for memory-mapped control: {len(record)} > "
            f"{MEMORY_MAPPED_CONTROL_SIZE}"
        )
    return record + bytes(MEMORY_MAPPED_CONTROL_SIZE - len(record))


def alloc_absolute_segment(
    segment_type: SegmentType,
    start_address: int,
    length: int,
    *,
    access_attributes: int = 0,
    memory_type: int = 0,
    memory_attributes: int = 0,
) -> bytes:
    """Absolute data or stack segment allocation.

    Access attributes: bits 0-3 write access level, bits 4-7 read access level.
    Memory type: bits 0-2 (1 = zero page RAM, 2 = RAM, 3 = EEPROM).
    Memory attributes: bit 7 enables checksum control.
    """
    if segment_type not in (SegmentType.ABS_DATA, SegmentType.ABS_STACK):
        raise ValueError("segment_type must be ABS_DATA or ABS_STACK")
    return _pad(
        bytes([LoadEvent.ADDITIONAL, segment_type])
        + start_address.to_bytes(2, "big")
        + length.to_bytes(2, "big")
        + bytes(
            [access_attributes & 0xFF, memory_type & 0xFF, memory_attributes & 0xFF]
        )
    )


def alloc_task_segment(
    start_address: int,
    pei_type: int,
    application_id: bytes,
) -> bytes:
    """Absolute task segment allocation.

    ``application_id`` is the 5 octet application id: manufacturer id (2),
    application software type (2) and version (1).
    """
    if len(application_id) != 5:
        raise ValueError("application_id must be 5 octets")
    return _pad(
        bytes([LoadEvent.ADDITIONAL, SegmentType.ABS_TASK])
        + start_address.to_bytes(2, "big")
        + bytes([pei_type & 0xFF])
        + application_id
    )


def task_pointer(init_address: int, save_address: int, pei_handler: int) -> bytes:
    """Task pointer load event."""
    return _pad(
        bytes([LoadEvent.ADDITIONAL, SegmentType.TASK_PTR])
        + init_address.to_bytes(2, "big")
        + save_address.to_bytes(2, "big")
        + pei_handler.to_bytes(2, "big")
    )


def task_control_1(interface_object_address: int, interface_object_count: int) -> bytes:
    """Task control 1 load event."""
    return _pad(
        bytes([LoadEvent.ADDITIONAL, SegmentType.TASK_CTRL_1])
        + interface_object_address.to_bytes(2, "big")
        + bytes([interface_object_count & 0xFF])
    )


def task_control_2(
    callback_address: int,
    com_object_pointer: int,
    com_object_segment_pointer_1: int,
    com_object_segment_pointer_2: int,
) -> bytes:
    """Task control 2 load event."""
    return _pad(
        bytes([LoadEvent.ADDITIONAL, SegmentType.TASK_CTRL_2])
        + callback_address.to_bytes(2, "big")
        + com_object_pointer.to_bytes(2, "big")
        + com_object_segment_pointer_1.to_bytes(2, "big")
        + com_object_segment_pointer_2.to_bytes(2, "big")
    )


def relative_allocation(number_of_octets: int) -> bytes:
    """Relative allocation load event (subtype 0x0A, 2 octet count)."""
    return _pad(
        bytes([LoadEvent.ADDITIONAL, SegmentType.RELATIVE_ALLOCATION])
        + number_of_octets.to_bytes(2, "big")
    )


def data_relative_allocation(size: int, *, mode: int = 0, fill: int = 0) -> bytes:
    """Data relative allocation load event (subtype 0x0B, System B).

    Layout: ``03 0B <size:4> <mode:1> <fill:1> <reserved:2>``. ``mode`` bit 0
    set fills the allocated memory with ``fill``; other bits are reserved.
    """
    return _pad(
        bytes([LoadEvent.ADDITIONAL, SegmentType.DATA_RELATIVE_ALLOCATION])
        + size.to_bytes(4, "big")
        + bytes([mode & 0xFF, fill & 0xFF])
    )

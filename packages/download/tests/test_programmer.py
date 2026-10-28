"""Tests for the low level device programmer."""

from __future__ import annotations

import pytest
from xknx.telegram.apci import (
    MemoryRead,
    MemoryWrite,
    PropertyValueRead,
    PropertyValueResponse,
    PropertyValueWrite,
)

from xknxeditor.download.errors import (
    LoadStateError,
    PropertyAccessRejected,
    VerificationError,
)
from xknxeditor.download.load_state import (
    PID_LOAD_STATE_CONTROL,
    LoadEvent,
    LoadState,
    SegmentType,
    alloc_absolute_segment,
    start_loading,
    unload,
)
from xknxeditor.download.programmer import (
    PID_IO_LIST,
    PID_OBJECT_TYPE,
    DeviceProgrammer,
)

from .conftest import FakeDevice


class _CappingConnection:
    """A connection that answers A_Memory_Read with at most ``cap`` octets.

    Models a device that limits its memory-read reply below the negotiated APDU.
    """

    def __init__(self, memory: bytes, cap: int) -> None:
        self._memory = memory
        self._cap = cap
        self.request_counts: list[int] = []

    async def send_data(self, payload: object, wait_for_ack: bool = True) -> None:
        raise AssertionError("unexpected send_data")

    async def request(self, payload: object, expected: object) -> object:
        from xknx.telegram import IndividualAddress, Telegram
        from xknx.telegram.apci import MemoryResponse

        assert isinstance(payload, MemoryRead)
        n = min(payload.count, self._cap)
        self.request_counts.append(payload.count)
        data = self._memory[payload.address : payload.address + n]
        return Telegram(
            destination_address=IndividualAddress("1.1.1"),
            payload=MemoryResponse(address=payload.address, data=data),
        )


async def test_read_memory_tolerates_short_replies() -> None:
    memory = bytes(range(20))
    connection = _CappingConnection(memory, cap=4)
    programmer = DeviceProgrammer(connection, max_apdu_length=55)  # asks up to 52
    result = await programmer.read_memory(0, 20)
    assert result == memory  # reassembled from 4-octet replies
    # After the first short reply the read stops over-asking (caps at 4).
    assert connection.request_counts[0] > 4
    assert all(count <= 4 for count in connection.request_counts[1:])


async def test_read_memory_raises_on_empty_reply() -> None:
    connection = _CappingConnection(b"", cap=0)
    programmer = DeviceProgrammer(connection, max_apdu_length=15)
    with pytest.raises(VerificationError, match="no memory response"):
        await programmer.read_memory(0, 8)


def test_memory_chunk_size_follows_apdu_by_default() -> None:
    # By default the classic memory chunk follows the negotiated APDU; a device that
    # cannot accept the full amount is handled by the no-ACK fallback, not a flat cap.
    assert DeviceProgrammer(FakeDevice(), max_apdu_length=15).memory_chunk_size == 12
    assert DeviceProgrammer(FakeDevice(), max_apdu_length=55).memory_chunk_size == 52


def test_memory_chunk_size_honours_lowered_cap() -> None:
    # The no-ACK fallback lowers the cap; the chunk then follows the smaller of the
    # cap and the APDU budget.
    programmer = DeviceProgrammer(FakeDevice(), max_apdu_length=55)
    programmer.max_memory_service_count = 15
    assert programmer.memory_chunk_size == 15


def test_memory_chunk_size_accounts_for_secure_overhead() -> None:
    # A Data Secure session adds 13 octets, so the plaintext ceiling shrinks by
    # 13 before the memory overhead is applied: 55 - 13 - 3 = 39.
    programmer = DeviceProgrammer(FakeDevice(), max_apdu_length=55, apdu_overhead=13)
    assert programmer.memory_chunk_size == 39
    with pytest.raises(VerificationError, match="APDU budget"):
        _ = DeviceProgrammer(
            FakeDevice(), max_apdu_length=15, apdu_overhead=13
        ).memory_chunk_size


async def test_write_memory_is_chunked() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device, max_apdu_length=15)
    data = bytes(range(30))

    await programmer.write_memory(0x4000, data)

    writes = [p for p in device.sent if isinstance(p, MemoryWrite)]
    assert [len(w.data) for w in writes] == [12, 12, 6]
    assert [w.address for w in writes] == [0x4000, 0x400C, 0x4018]
    read_back = bytes(device.memory[0x4000 + i] for i in range(30))
    assert read_back == data


async def test_read_memory_reassembles_chunks() -> None:
    device = FakeDevice()
    device.memory.update({0x100 + i: i for i in range(20)})
    programmer = DeviceProgrammer(device, max_apdu_length=15)

    assert await programmer.read_memory(0x100, 20) == bytes(range(20))


async def test_write_memory_verify_success() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device)
    await programmer.write_memory(0x10, b"\x01\x02\x03", verify=True)


async def test_write_memory_verify_detects_mismatch() -> None:
    class DroppingDevice(FakeDevice):
        async def send_data(self, payload: object, wait_for_ack: bool = True) -> None:
            if isinstance(payload, MemoryWrite):
                return  # silently drop the write
            await super().send_data(payload, wait_for_ack)  # type: ignore[arg-type]

    programmer = DeviceProgrammer(DroppingDevice())
    with pytest.raises(VerificationError, match="verification failed"):
        await programmer.write_memory(0x10, b"\x01\x02\x03", verify=True)


async def test_memory_write_no_ack_names_operation_and_context() -> None:
    # A lost acknowledgement must surface as the typed MemoryWriteNotAcknowledged and
    # name the exact write (address and length) plus the active diagnostic context.
    from xknx.exceptions import ManagementConnectionError

    from xknxeditor.download.errors import MemoryWriteNotAcknowledged

    class NoAckDevice(FakeDevice):
        async def send_data(self, payload: object, wait_for_ack: bool = True) -> None:
            if isinstance(payload, MemoryWrite):
                raise ManagementConnectionError("No ACK received for repeated telegram")
            await super().send_data(payload, wait_for_ack)  # type: ignore[arg-type]

    programmer = DeviceProgrammer(NoAckDevice())
    programmer.diagnostic_context = "control=(5, 48) target-index=4 target-type=3"
    with pytest.raises(MemoryWriteNotAcknowledged) as excinfo:
        await programmer.write_memory(0x45C0, bytes(10))
    message = str(excinfo.value)
    assert "No ACK received for repeated telegram" in message
    assert "writing memory 0x45c0" in message
    assert "10 byte" in message
    assert "control=(5, 48)" in message


async def test_property_round_trip() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device)
    await programmer.write_property(5, 0x33, b"\xaa\xbb")
    assert await programmer.read_property(5, 0x33) == b"\xaa\xbb"


async def test_unverified_property_write_tolerates_empty_response() -> None:
    # An empty (0-element) ordinary write confirmation is a rejection when
    # the write is verified; an unverified write accepts it as done. A device may
    # confirm a property write (e.g. the MCB table) with 0 elements.
    device = FakeDevice()
    device.absent_objects = {4}
    programmer = DeviceProgrammer(device)
    assert await programmer.write_property(4, 27, bytes(10), verify=False) == b""


async def test_verified_property_write_rejects_empty_response() -> None:
    device = FakeDevice()
    device.absent_objects = {4}
    programmer = DeviceProgrammer(device)
    with pytest.raises(PropertyAccessRejected):
        await programmer.write_property(4, 27, bytes(10), verify=True)


async def test_write_property_chunks_large_value() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device, max_apdu_length=15)
    # 20 one-byte elements, 10 data octets fit a frame -> two frames of 10
    await programmer.write_property(5, 0x10, bytes(20), count=20)
    writes = [p for p in device.sent if isinstance(p, PropertyValueWrite)]
    assert [w.count for w in writes] == [10, 10]
    assert [w.start_index for w in writes] == [1, 11]


async def test_write_memory_verify_reads_back_each_block() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device, max_apdu_length=15)  # 12 byte chunks
    await programmer.write_memory(0x10, bytes(20), verify=True)
    reads = [p for p in device.sent if isinstance(p, MemoryRead)]
    assert len(reads) == 2  # one read-back per written block


async def test_read_table_reference() -> None:
    device = FakeDevice()
    device.table_references[2] = 0x1234
    assert await DeviceProgrammer(device).read_table_reference(2) == 0x1234


def test_memory_chunk_size_rejects_impossible_budget() -> None:
    with pytest.raises(VerificationError, match="APDU budget"):
        _ = DeviceProgrammer(FakeDevice(), max_apdu_length=2).memory_chunk_size


async def test_locate_object_by_type_and_occurrence() -> None:
    device = FakeDevice(object_types={0: 0, 1: 0x0100, 2: 0x0100})
    programmer = DeviceProgrammer(device)

    # occurrence is zero-based: 0 == first instance, 1 == second
    assert await programmer.locate_object(0x0100, occurrence=0) == 1
    assert await programmer.locate_object(0x0100, occurrence=1) == 2
    # second lookup is served from the cache without further reads
    reads_before = len(device.sent)
    assert await programmer.locate_object(0x0100, occurrence=0) == 1
    assert len(device.sent) == reads_before


async def test_locate_object_reads_object_type_property() -> None:
    device = FakeDevice(object_types={0: 0x0000, 1: 0x0007})
    programmer = DeviceProgrammer(device)
    await programmer.locate_object(0x0007)
    assert any(getattr(p, "property_id", None) == PID_OBJECT_TYPE for p in device.sent)


async def test_locate_object_missing_raises() -> None:
    device = FakeDevice(object_types={0: 0x0000})
    programmer = DeviceProgrammer(device)
    with pytest.raises(LoadStateError, match="not found"):
        await programmer.locate_object(0x9999)


async def test_send_load_event_reaches_expected_state() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device)
    await programmer.send_load_event(3, start_loading(), LoadState.LOADING)
    assert device.load_states[3] == LoadState.LOADING


async def test_send_load_event_error_state_raises() -> None:
    class ErrorDevice(FakeDevice):
        def _handle_property_write(self, payload: object) -> None:
            self.load_states[payload.object_index] = LoadState.ERROR  # type: ignore[attr-defined]

    programmer = DeviceProgrammer(ErrorDevice())
    with pytest.raises(LoadStateError, match="ERROR"):
        await programmer.send_load_event(3, start_loading(), LoadState.LOADING)


async def test_send_load_event_timeout_raises() -> None:
    class StuckDevice(FakeDevice):
        def _handle_property_write(self, payload: object) -> None:
            return  # never changes state, stays UNLOADED

    programmer = DeviceProgrammer(StuckDevice())
    with pytest.raises(LoadStateError, match="did not reach"):
        await programmer.send_load_event(
            3, start_loading(), LoadState.LOADING, retries=2, retry_delay=0
        )


@pytest.mark.parametrize("retries", [1, 2])
async def test_load_accepts_loaded_from_poll(retries: int) -> None:
    class FastLoadDevice(FakeDevice):
        def _handle_property_write(self, payload: PropertyValueWrite) -> None:
            self.load_states[payload.object_index] = LoadState.UNLOADING

        def _handle_property_read(
            self, payload: PropertyValueRead
        ) -> PropertyValueResponse:
            response = super()._handle_property_read(payload)
            self.load_states[payload.object_index] = LoadState.LOADED
            return response

    device = FastLoadDevice()
    # Write confirms UNLOADING, then the first poll is already LOADED. Check
    # both the last allowed poll and a poll with retries remaining.
    await DeviceProgrammer(device).send_load_event(
        1,
        start_loading(),
        LoadState.LOADING,
        also_accept=LoadState.LOADED,
        retries=retries,
        retry_delay=0,
    )
    assert sum(isinstance(p, PropertyValueRead) for p in device.sent) == 1


@pytest.mark.parametrize(
    "state", [LoadState.ERROR, LoadState.UNLOADED, LoadState.UNLOADING]
)
async def test_load_alternative_does_not_hide_failure(state: LoadState) -> None:
    class StuckDevice(FakeDevice):
        def _handle_property_write(self, payload: PropertyValueWrite) -> None:
            self.load_states[payload.object_index] = state

    device = StuckDevice()
    with pytest.raises(LoadStateError, match=state.name):
        await DeviceProgrammer(device).send_load_event(
            1,
            start_loading(),
            LoadState.LOADING,
            also_accept=LoadState.LOADED,
            retries=2,
            retry_delay=0,
        )
    reads = [
        p
        for p in device.sent
        if isinstance(p, PropertyValueRead) and p.property_id == PID_LOAD_STATE_CONTROL
    ]
    assert len(reads) == (0 if state == LoadState.ERROR else 2)


async def test_restart() -> None:
    device = FakeDevice()
    await DeviceProgrammer(device).restart()
    assert device.restarted


async def test_authorize_returns_granted_level() -> None:
    device = FakeDevice()
    device.authorize_level = 0
    level = await DeviceProgrammer(device).authorize()
    assert level == 0
    # Free access key is presented when no per-device key is available.
    assert device.authorize_keys == [0xFFFFFFFF]


async def test_authorize_reports_locked_level() -> None:
    device = FakeDevice()
    device.authorize_level = 15  # device locked with an access key
    level = await DeviceProgrammer(device).authorize(key=0x11223344)
    assert level == 15
    assert device.authorize_keys == [0x11223344]


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize(
    ("count", "start", "data", "exception", "message"),
    [
        (0, 1, b"", PropertyAccessRejected, "0 elements"),
        (1, 2, b"\x02", VerificationError, "start index"),
        (2, 1, b"\x02", VerificationError, "element count"),
        (1, 1, b"", LoadStateError, "empty load state"),
        (1, 1, b"\xff", LoadStateError, "unknown load state"),
    ],
)
async def test_load_state_response_validation(
    operation, count, start, data, exception, message
) -> None:
    from xknx.telegram.apci import PropertyValueResponse

    class ResponseDevice(FakeDevice):
        def _handle_property_read(self, payload):
            return PropertyValueResponse(
                object_index=payload.object_index,
                property_id=payload.property_id,
                count=count,
                start_index=start,
                data=data,
            )

    device = ResponseDevice()
    programmer = DeviceProgrammer(device)
    with pytest.raises(exception, match=message):
        if operation == "write":
            await programmer.send_load_event(1, start_loading(), LoadState.LOADING)
        else:
            await programmer.read_load_state(1)
    assert len(device.sent) == 1  # no fallback read hides a malformed write response


async def test_pid5_is_strict_even_when_verify_false() -> None:
    device = FakeDevice()
    device.absent_objects = {1}
    with pytest.raises(PropertyAccessRejected):
        await DeviceProgrammer(device).write_property(
            1, 5, start_loading(), verify=False
        )


async def test_optional_property_reads_still_tolerate_absence() -> None:
    from xknx.telegram.apci import PropertyValueResponse

    class AbsentDevice(FakeDevice):
        def _handle_property_read(self, payload):
            return PropertyValueResponse(
                object_index=payload.object_index,
                property_id=payload.property_id,
                count=0,
                start_index=payload.start_index,
                data=b"",
            )

    programmer = DeviceProgrammer(AbsentDevice())
    assert await programmer.read_property(0, 56) == b""
    assert await programmer.read_max_apdu_length() == 15


async def test_loaded_is_not_loading_and_every_poll_is_logged(caplog) -> None:
    import logging

    class StuckLoadedDevice(FakeDevice):
        def _handle_property_write(self, payload):
            self.load_states[payload.object_index] = LoadState.LOADED

    device = StuckLoadedDevice()
    programmer = DeviceProgrammer(device)
    programmer.diagnostic_context = "control=(14, 30) target-index=1 target-type=1"
    with (
        caplog.at_level(logging.DEBUG, logger="xknxeditor.download.programmer"),
        pytest.raises(LoadStateError, match="did not reach LOADING, last state LOADED"),
    ):
        await programmer.send_load_event(
            1, start_loading(), LoadState.LOADING, retries=2, retry_delay=0
        )
    assert "control=(14, 30) target-index=1 target-type=1" in caplog.text
    assert "event=01000000000000000000" in caplog.text
    assert "count=1 start-index=1 data=01" in caplog.text
    assert "poll=1/2 state=LOADED" in caplog.text
    assert "poll=2/2 state=LOADED" in caplog.text


async def test_state_is_decoded_from_first_octet() -> None:
    from xknx.telegram.apci import PropertyValueResponse

    class ResponseDevice(FakeDevice):
        def _handle_property_read(self, payload):
            return PropertyValueResponse(
                object_index=payload.object_index,
                property_id=5,
                start_index=1,
                data=b"\x01\x02",
            )

    with pytest.raises(LoadStateError, match="last state LOADED"):
        await DeviceProgrammer(ResponseDevice()).send_load_event(
            1, start_loading(), LoadState.LOADING, retries=0
        )


class _IoListDevice(FakeDevice):
    """Answers PID_IO_LIST; the association table's PID_OBJECT_TYPE is not readable."""

    io_list = (0x0000, 0x0001, 0x0002, 0x0003, 0x0004, 0x0011, 0x0008, 0x000B, 0xC351)

    def _handle_property_read(
        self, payload: PropertyValueRead
    ) -> PropertyValueResponse:
        if (payload.object_index, payload.property_id) != (0, PID_IO_LIST):
            return super()._handle_property_read(payload)
        if payload.start_index == 0:
            data = len(self.io_list).to_bytes(2, "big")
        else:
            start = payload.start_index - 1
            data = b"".join(
                t.to_bytes(2, "big")
                for t in self.io_list[start : start + payload.count]
            )
        return PropertyValueResponse(
            object_index=0,
            property_id=PID_IO_LIST,
            data=data,
            count=payload.count,
            start_index=payload.start_index,
        )


async def test_locate_object_uses_the_io_list_past_an_unreadable_object() -> None:
    types = dict(enumerate(_IoListDevice.io_list))
    del types[2]
    device = _IoListDevice(object_types=types)
    programmer = DeviceProgrammer(device)
    assert await programmer.locate_object(0x000B) == 7
    assert await programmer.locate_object(0xC351) == 8
    # the objects tell their types up to the unreadable one; the list covers the rest
    assert [
        p.object_index
        for p in device.sent
        if getattr(p, "property_id", None) == PID_OBJECT_TYPE
    ] == [0, 1, 2]
    with pytest.raises(LoadStateError, match="not found"):
        await programmer.locate_object(0x0006)


async def test_locate_object_trusts_the_objects_over_the_io_list() -> None:
    # The list lacks the entry of object 5, so from there on it names the type of the
    # next object; each object still tells its own type.
    types = {0: 0x0000, 1: 0x0001, 2: 0x0002, 3: 0x0003, 4: 0x0004, 5: 0x0005}
    types |= {6: 0x0006, 7: 0x000B}
    device = _IoListDevice(object_types=types)
    device.io_list = (0x0000, 0x0001, 0x0002, 0x0003, 0x0004, 0x0006, 0x000B)
    programmer = DeviceProgrammer(device)
    assert await programmer.locate_object(0x000B) == 7
    assert not any(getattr(p, "property_id", None) == PID_IO_LIST for p in device.sent)


async def test_locate_object_reads_each_object_type_once() -> None:
    device = FakeDevice(object_types={0: 0x0000, 1: 0x0001, 2: 0x0002, 3: 0x0009})
    programmer = DeviceProgrammer(device)
    assert await programmer.locate_object(0x0009) == 3
    reads_before = len(device.sent)
    assert await programmer.locate_object(0x0001) == 1
    assert await programmer.locate_object(0x0002) == 2
    assert len(device.sent) == reads_before


async def test_transient_read_timeout_is_retried() -> None:
    # A lost response on a live connection (the reported "Timeout while waiting for
    # MemoryResponse" during a verify read) is retried instead of aborting the download.
    from xknx.exceptions import ManagementConnectionError

    class FlakyRead(FakeDevice):
        def __init__(self) -> None:
            super().__init__()
            self.read_failures = 0

        async def request(self, payload: object, expected: object) -> object:
            if isinstance(payload, MemoryRead) and self.read_failures == 0:
                self.read_failures = 1
                raise ManagementConnectionError(
                    "Timeout while waiting for MemoryResponse"
                )
            return await super().request(payload, expected)  # type: ignore[arg-type]

    device = FlakyRead()
    device.memory.update({0x10 + i: i for i in range(4)})
    programmer = DeviceProgrammer(device)
    assert await programmer.read_memory(0x10, 4) == bytes(range(4))
    assert device.read_failures == 1  # first read raised once and was retried


class _FlakyPropertyWrite(FakeDevice):
    """Loses the answer to the first ``failures`` property writes."""

    def __init__(self, failures: int = 1) -> None:
        super().__init__()
        self.failures = failures
        self.writes: list[PropertyValueWrite] = []

    async def request(self, payload: object, expected: object) -> object:
        from xknx.exceptions import ManagementConnectionError

        if isinstance(payload, PropertyValueWrite):
            self.writes.append(payload)
            if self.failures:
                self.failures -= 1
                raise ManagementConnectionError(
                    "Timeout while waiting for PropertyValueResponse"
                )
        return await super().request(payload, expected)  # type: ignore[arg-type]


async def test_transient_property_write_timeout_is_retried() -> None:
    device = _FlakyPropertyWrite()
    programmer = DeviceProgrammer(device)
    await programmer.write_property(5, 0x33, b"\xaa\xbb")
    assert len(device.writes) == 2
    assert await programmer.read_property(5, 0x33) == b"\xaa\xbb"


async def test_a_load_event_whose_response_got_lost_is_sent_again() -> None:
    # An unload whose response did not arrive within the timeout broke the download off.
    device = _FlakyPropertyWrite()
    programmer = DeviceProgrammer(device)
    await programmer.send_load_event(3, unload(), LoadState.UNLOADED)
    assert [w.data[0] for w in device.writes] == [LoadEvent.UNLOAD] * 2
    assert device.load_states[3] == LoadState.UNLOADED


async def test_a_property_write_lost_every_time_still_fails() -> None:
    from xknx.exceptions import ManagementConnectionError

    device = _FlakyPropertyWrite(failures=10)
    programmer = DeviceProgrammer(device)
    with pytest.raises(ManagementConnectionError, match="writing object 5 property 51"):
        await programmer.write_property(5, 0x33, b"\xaa\xbb")
    assert len(device.writes) == 3  # sent once and retried twice


async def test_an_additional_load_control_is_sent_once() -> None:
    # A second segment allocation would allocate again: a lost answer is not resent.
    from xknx.exceptions import ManagementConnectionError

    device = _FlakyPropertyWrite()
    device.load_states[3] = LoadState.LOADING
    programmer = DeviceProgrammer(device)
    with pytest.raises(ManagementConnectionError):
        await programmer.write_property(
            3,
            PID_LOAD_STATE_CONTROL,
            alloc_absolute_segment(SegmentType.ABS_DATA, 0x4000, 0x100),
        )
    assert len(device.writes) == 1

"""Shared test doubles for the download package."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import (
    APCI,
    AuthorizeRequest,
    AuthorizeResponse,
    DeviceDescriptorRead,
    DeviceDescriptorResponse,
    FunctionPropertyCommand,
    FunctionPropertyStateRead,
    FunctionPropertyStateResponse,
    MemoryExtendedRead,
    MemoryExtendedReadResponse,
    MemoryExtendedWrite,
    MemoryExtendedWriteResponse,
    MemoryRead,
    MemoryResponse,
    MemoryWrite,
    PropertyDescriptionRead,
    PropertyDescriptionResponse,
    PropertyValueRead,
    PropertyValueResponse,
    PropertyValueWrite,
    Restart,
    RestartMasterReset,
    RestartMasterResetResponse,
    UserMemoryRead,
    UserMemoryResponse,
    UserMemoryWrite,
)

from xknxeditor.download.crc import segment_crc
from xknxeditor.download.load_state import (
    PID_LOAD_STATE_CONTROL,
    LoadEvent,
    LoadState,
)
from xknxeditor.download.programmer import PID_OBJECT_TYPE, PID_TABLE_REFERENCE

_LOAD_STATE = IndividualAddress("1.1.1")
# BIM M112 shared load-control record and LSM-1 status (MV-0701/MV-0705).
_M112_CONTROL = 0x0104
_M112_STATUS_LSM1 = 0xB6EA
_M112_STATUS_COUNT = 8
_LOAD_TRANSITIONS = {
    LoadEvent.START_LOADING: LoadState.LOADING,
    LoadEvent.LOAD_COMPLETE: LoadState.LOADED,
    LoadEvent.UNLOAD: LoadState.UNLOADED,
    LoadEvent.ADDITIONAL: LoadState.LOADING,
}


class FakeDevice:
    """Simulate the bus behaviour of a KNX device being programmed.

    Records everything written and answers reads from an in-memory model:
    a flat memory, interface object properties, per-object load states and a
    mapping of object index to object type (for object location).
    """

    def __init__(
        self, object_types: dict[int, int] | None = None, descriptor: int = 0x0705
    ) -> None:
        """Initialize an empty device with an optional object type map."""
        self.memory: dict[int, int] = {}
        self.properties: dict[tuple[int, int], bytes] = {}
        self.property_element_sizes: dict[tuple[int, int], int] = {
            (5, 0x33): 2,
            (4, 50): 1,
        }
        self.load_states: dict[int, LoadState] = {}
        self.table_references: dict[int, int] = {}
        self.object_types = object_types or {}
        self.descriptor = descriptor
        self.sent: list[APCI] = []
        self.restarted = False
        self.master_reset: tuple[int, int] | None = None
        # Error code and process time (ms) reported in the Master Reset response.
        self.master_reset_error_code = 0
        self.master_reset_process_time = 0
        # Quirk emulation: a device that restarts on a Master Reset without answering it raises
        # this (the connection it drops, or the timeout waiting for the response).
        self.master_reset_unanswered: type[Exception] | None = None
        # Function property command/read state, keyed by (object, property).
        self.function_properties: dict[tuple[int, int], bytes] = {}
        self.function_property_return_code = 0
        # Access level the device grants for an A_Authorize, and the keys it saw.
        self.authorize_level = 0
        self.authorize_keys: list[int] = []
        # Interface objects the device does not carry: a property write to one is
        # rejected with a 0-element response, as a real device answers an access
        # to an absent object. Used to exercise LdCtrlMapError tolerance.
        self.absent_objects: set[int] = set()
        # Quirk emulation: when set, an A_Memory_Read requesting more than this many octets answers
        # with a ZERO-length MemoryResponse (as observed on BIM M112 / mask 0701), forcing the
        # reader to back the block size off. None = always serve the full requested count.
        self.memory_read_zero_above: int | None = None
        # The end of the memory the device lets a tool read (e.g. of an allocated table):
        # an A_Memory_Read from there on answers with no octets. None = no end.
        self.readable_end: int | None = None
        # A_MemoryExtended_Write_Response return code: 0 success, 1 success with the CRC over
        # count, address and data in the confirmation (``extended_crc`` replaces that CRC).
        self.extended_write_code = 0
        self.extended_crc: int | None = None
        # The device takes no extended memory services: they go unanswered.
        self.extended_memory_unanswered = False

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        """Handle a payload the programmer does not expect an answer to."""
        self.sent.append(payload)
        if isinstance(payload, MemoryWrite | UserMemoryWrite):
            self._store_memory(payload.address, payload.data)
        elif isinstance(payload, PropertyValueWrite):
            self._handle_property_write(payload)
        elif isinstance(payload, Restart):
            self.restarted = True

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        """Handle a payload the programmer waits for a response to."""
        self.sent.append(payload)
        if isinstance(payload, PropertyDescriptionRead):
            width = self.property_element_sizes.get(
                (payload.object_index, payload.property_id), 1
            )
            return self._telegram(
                PropertyDescriptionResponse(
                    object_index=payload.object_index,
                    property_id=payload.property_id,
                    type_=16 + width,
                    max_count=100,
                )
            )
        if isinstance(payload, MemoryExtendedWrite | MemoryExtendedRead):
            if self.extended_memory_unanswered:
                raise ManagementConnectionError("no response to the extended service")
            if isinstance(payload, MemoryExtendedRead):
                data = self._read_memory(payload.address, payload.count)
                return self._telegram(
                    MemoryExtendedReadResponse(
                        return_code=0, address=payload.address, data=data
                    )
                )
            self._store_memory(payload.address, payload.data)
            confirmation = b""
            if self.extended_write_code == 1:
                crc = segment_crc(
                    bytes([len(payload.data)])
                    + payload.address.to_bytes(3, "big")
                    + payload.data
                )
                if self.extended_crc is not None:
                    crc = self.extended_crc
                confirmation = crc.to_bytes(2, "big")
            return self._telegram(
                MemoryExtendedWriteResponse(
                    return_code=self.extended_write_code,
                    address=payload.address,
                    confirmation_data=confirmation,
                )
            )
        if isinstance(payload, MemoryWrite | UserMemoryWrite):
            self._store_memory(payload.address, payload.data)
            response = (
                UserMemoryResponse
                if isinstance(payload, UserMemoryWrite)
                else MemoryResponse
            )
            return self._telegram(response(address=payload.address, data=payload.data))
        if isinstance(payload, MemoryRead):
            if (
                self.memory_read_zero_above is not None
                and payload.count > self.memory_read_zero_above
            ):
                # Device quirk: too-large read -> empty reply (not a short one).
                return self._telegram(MemoryResponse(address=payload.address, data=b""))
            count = payload.count
            if self.readable_end is not None:
                count = max(0, min(count, self.readable_end - payload.address))
            data = self._read_memory(payload.address, count)
            return self._telegram(MemoryResponse(address=payload.address, data=data))
        if isinstance(payload, UserMemoryRead):
            data = bytes(
                self.memory.get(payload.address + i, 0) for i in range(payload.count)
            )
            return self._telegram(
                UserMemoryResponse(address=payload.address, data=data)
            )
        if isinstance(payload, PropertyValueWrite):
            # An absent object rejects the write with a 0-element response.
            if payload.object_index in self.absent_objects:
                return self._telegram(
                    PropertyValueResponse(
                        object_index=payload.object_index,
                        property_id=payload.property_id,
                        count=0,
                        start_index=payload.start_index,
                        data=b"",
                    )
                )
            # A_PropertyValue_Write is confirmed by a response carrying the
            # resulting value; apply the write, then answer with a read.
            self._handle_property_write(payload)
            return self._telegram(
                self._handle_property_read(
                    PropertyValueRead(
                        object_index=payload.object_index,
                        property_id=payload.property_id,
                        count=payload.count,
                        start_index=payload.start_index,
                    )
                )
            )
        if isinstance(payload, PropertyValueRead):
            return self._telegram(self._handle_property_read(payload))
        if isinstance(payload, DeviceDescriptorRead):
            return self._telegram(
                DeviceDescriptorResponse(descriptor=0, value=self.descriptor)
            )
        if isinstance(payload, AuthorizeRequest):
            self.authorize_keys.append(payload.key)
            return self._telegram(AuthorizeResponse(level=self.authorize_level))
        if isinstance(payload, FunctionPropertyCommand):
            self.function_properties[(payload.object_index, payload.property_id)] = (
                payload.data
            )
            return self._telegram(self._function_property_response(payload))
        if isinstance(payload, FunctionPropertyStateRead):
            return self._telegram(self._function_property_response(payload))
        if isinstance(payload, RestartMasterReset):
            self.master_reset = (payload.erase_code, payload.channel_number)
            self.restarted = True
            if self.master_reset_unanswered is not None:
                raise self.master_reset_unanswered("no master reset response")
            return self._telegram(
                RestartMasterResetResponse(
                    error_code=self.master_reset_error_code,
                    process_time=self.master_reset_process_time,
                )
            )
        raise AssertionError(f"unexpected request: {payload}")

    def _store_memory(self, address: int, data: bytes) -> None:
        """Write device memory and interpret a BIM M112 load-control record."""
        for index, byte in enumerate(data):
            self.memory[address + index] = byte
        if address != _M112_CONTROL or not data:
            return
        index = data[0] >> 4
        event = data[0] & 0x0F
        try:
            self.load_states[index] = _LOAD_TRANSITIONS[LoadEvent(event)]
        except (ValueError, KeyError):
            return

    def _read_memory(self, address: int, count: int) -> bytes:
        """Read device memory, serving M112 load-status bytes from LSM state."""
        end = address + count
        status_end = _M112_STATUS_LSM1 + _M112_STATUS_COUNT
        if count > 0 and address < status_end and end > _M112_STATUS_LSM1:
            out = bytearray()
            for offset in range(count):
                loc = address + offset
                if _M112_STATUS_LSM1 <= loc < status_end:
                    index = loc - _M112_STATUS_LSM1 + 1
                    if index in self.load_states:
                        out.append(self.load_states[index])
                    else:
                        out.append(self.memory.get(loc, 0))
                else:
                    out.append(self.memory.get(loc, 0))
            return bytes(out)
        return bytes(self.memory.get(address + i, 0) for i in range(count))

    def _handle_property_write(self, payload: PropertyValueWrite) -> None:
        """Apply a property write, interpreting load state control specially."""
        if payload.property_id == PID_LOAD_STATE_CONTROL and payload.data:
            event = payload.data[0]
            self.load_states[payload.object_index] = _LOAD_TRANSITIONS[LoadEvent(event)]
            return
        key = (payload.object_index, payload.property_id)
        width = len(payload.data) // payload.count
        data = bytearray(self.properties.get(key, b""))
        start = (payload.start_index - 1) * width
        end = start + len(payload.data)
        data.extend(bytes(max(0, end - len(data))))
        data[start:end] = payload.data
        self.properties[key] = bytes(data)
        self.property_element_sizes[key] = width

    def _handle_property_read(
        self, payload: PropertyValueRead
    ) -> PropertyValueResponse:
        """Answer a property read from the device model."""
        if payload.property_id == PID_LOAD_STATE_CONTROL:
            state = self.load_states.get(payload.object_index, LoadState.UNLOADED)
            data = bytes([state])
        elif payload.property_id == PID_OBJECT_TYPE:
            object_type = self.object_types.get(payload.object_index)
            # A missing object answers empty, as a real device does past its
            # last interface object; locate_object stops scanning on that.
            data = object_type.to_bytes(2, "big") if object_type is not None else b""
        elif payload.property_id == PID_TABLE_REFERENCE:
            data = self.table_references.get(payload.object_index, 0).to_bytes(
                4 if self.descriptor == 0x07B0 else 2, "big"
            )
        elif payload.object_index == 0 and payload.property_id == 14:
            data = self.properties.get((0, 14), b"\x00")
        else:
            data = self.properties.get((payload.object_index, payload.property_id), b"")
            width = self.property_element_sizes.get(
                (payload.object_index, payload.property_id)
            )
            if width is not None and payload.start_index > 0:
                start = (payload.start_index - 1) * width
                data = data[start : start + payload.count * width]
        return PropertyValueResponse(
            object_index=payload.object_index,
            property_id=payload.property_id,
            data=data,
            count=payload.count,
            start_index=payload.start_index,
        )

    def _function_property_response(
        self, payload: FunctionPropertyCommand | FunctionPropertyStateRead
    ) -> FunctionPropertyStateResponse:
        """Answer a function property command/read from the device model."""
        data = self.function_properties.get(
            (payload.object_index, payload.property_id), b""
        )
        return FunctionPropertyStateResponse(
            object_index=payload.object_index,
            property_id=payload.property_id,
            return_code=self.function_property_return_code,
            data=data,
        )

    @staticmethod
    def _telegram(payload: APCI) -> Telegram:
        """Wrap a response payload in an incoming telegram."""
        return Telegram(destination_address=_LOAD_STATE, payload=payload)


@pytest.fixture(autouse=True)
def _no_sync_interval(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No waiting between S-A_Sync requests in tests (a test that checks it sets its own)."""
    from xknxeditor.download import secure_session

    monkeypatch.setattr(secure_session, "_SYNC_INTERVAL", 0.0)
    secure_session._last_sync_response.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    secure_session._last_sync_response.clear()  # pyright: ignore[reportPrivateUsage]

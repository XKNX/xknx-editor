"""Shared test doubles for the download package."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
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

from xknxeditor.download.load_state import (
    PID_LOAD_STATE_CONTROL,
    LoadEvent,
    LoadState,
)
from xknxeditor.download.programmer import PID_OBJECT_TYPE, PID_TABLE_REFERENCE

_LOAD_STATE = IndividualAddress("1.1.1")


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

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        """Handle a payload the programmer does not expect an answer to."""
        self.sent.append(payload)
        if isinstance(payload, MemoryWrite | UserMemoryWrite):
            for index, byte in enumerate(payload.data):
                self.memory[payload.address + index] = byte
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
        if isinstance(payload, MemoryWrite | UserMemoryWrite):
            for index, byte in enumerate(payload.data):
                self.memory[payload.address + index] = byte
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
            data = bytes(
                self.memory.get(payload.address + i, 0) for i in range(payload.count)
            )
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
            return self._telegram(
                RestartMasterResetResponse(
                    error_code=self.master_reset_error_code,
                    process_time=self.master_reset_process_time,
                )
            )
        raise AssertionError(f"unexpected request: {payload}")

    def _handle_property_write(self, payload: PropertyValueWrite) -> None:
        """Apply a property write, interpreting load state control specially."""
        if payload.property_id == PID_LOAD_STATE_CONTROL and payload.data:
            event = payload.data[0]
            transitions = {
                LoadEvent.START_LOADING: LoadState.LOADING,
                LoadEvent.LOAD_COMPLETE: LoadState.LOADED,
                LoadEvent.UNLOAD: LoadState.UNLOADED,
                LoadEvent.ADDITIONAL: LoadState.LOADING,
            }
            self.load_states[payload.object_index] = transitions[LoadEvent(event)]
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

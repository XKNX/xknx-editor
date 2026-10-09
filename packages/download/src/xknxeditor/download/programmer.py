"""Low level device programming operations over a point-to-point connection.

The :class:`DeviceProgrammer` turns the primitive application layer services
provided by ``xknx`` into the operations a Load Procedure needs: chunked memory
writes, property writes, and driving a Load State Machine with read-back
verification. Those services are defined in KNX Standard v3.0.0, Chapter 3/3/7
"Application Layer": A_Memory_Read (section 3.5.3), A_Memory_Write (section 3.5.4),
A_PropertyValue_Read/A_PropertyValue_Write, and A_DeviceDescriptor_Read
(section 3.4.2.1). Memory reads/writes are split to the connection's maximum APDU
length.

It talks to any object implementing :class:`BusConnection`; at runtime this is
``xknx.management.P2PConnection``, in tests it is a fake.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from xknx.exceptions import ManagementConnectionError
from xknx.telegram.apci import (
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

from . import load_state
from .errors import (
    RESOURCE_READ_PROTECTED_ERROR,
    RESOURCE_WRITE_PROTECTED_ERROR,
    CompareMismatch,
    DownloadError,
    LoadStateError,
    PropertyAccessRejected,
    VerificationError,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from xknx.telegram import Telegram
    from xknx.telegram.apci import APCI

# Octets consumed by TPCI/APCI/count/address in a standard A_Memory_Write ASDU.
_MEMORY_OVERHEAD = 3
# A_Memory_Write encodes the byte count in 6 bits.
_MAX_MEMORY_CHUNK = 0x3F
# Octets consumed by APCI/object/property/count/index in A_PropertyValue_Write.
_PROPERTY_OVERHEAD = 5
# A_PropertyValue_Write encodes the element count in 4 bits.
_MAX_PROPERTY_ELEMENTS = 0xF
# Default APDU length every KNX device has to support.
DEFAULT_MAX_APDU_LENGTH = 15
# Key that requests free access; a device with no access key set grants its
# highest level (0) for it. Used when no per-device access key is available.
FREE_ACCESS_KEY = 0xFFFFFFFF
# Upper bound used when negotiating the APDU length up from the default.
MAX_NEGOTIATED_APDU_LENGTH = 254
# Upper bound for device communication through an interface.
MAX_COMMUNICATION_APDU_LENGTH = 239
# Device Object property carrying the device's maximum APDU length (2 octets).
PID_MAX_APDU_LENGTH = 56
# Property id carrying an interface object's type (PID_OBJECT_TYPE).
PID_OBJECT_TYPE = 1
# Property id carrying a loadable part's table base address (PID_TABLE_REFERENCE).
PID_TABLE_REFERENCE = 7
# Property id carrying a table object's table (PID_TABLE).
PID_TABLE = 23
# Interface object type of the Group Object Association Table (Associationtable Object).
ASSOCIATION_TABLE_OBJECT_TYPE = 2
# Highest interface object index scanned when locating an object by type.
_MAX_OBJECT_INDEX = 255
# A_Memory_Read/Write carry a 16-bit address, so they only reach the first 64
# KiB; at and above this boundary A_UserMemory_Read/Write (3-byte address) are
# used. Split transfers at this boundary so each block uses the correct service.
# see .references/ets_map.md
_USER_MEMORY_BOUNDARY = 0x10000
# A_UserMemory_Read/Write encode the octet count in 4 bits.
_MAX_USER_MEMORY_CHUNK = 0xF


class BusConnection(Protocol):
    """Subset of ``xknx.management.P2PConnection`` used for programming."""

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        """Send a payload and, by default, wait for the transport layer ACK."""
        ...

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        """Send a payload and wait for the device's response telegram."""
        ...


class ConnectionManager(Protocol):
    """Opens and closes point-to-point connections for a Load Procedure.

    A Load Procedure opens the connection on a Connect control and closes it on
    a Disconnect (and after a Restart). Implementations map ``open`` to a fresh
    T_Connect and ``close`` to T_Disconnect.
    """

    async def open(self) -> BusConnection:
        """Open a connection to the device and return it."""
        ...

    async def close(self) -> None:
        """Close the currently open connection, if any."""
        ...


class DeviceProgrammer:
    """Perform programming operations over an open point-to-point connection."""

    def __init__(
        self,
        connection: BusConnection,
        *,
        max_apdu_length: int = DEFAULT_MAX_APDU_LENGTH,
        apdu_overhead: int = 0,
    ) -> None:
        """Initialize with a connected bus connection and the negotiated APDU length.

        ``apdu_overhead`` is the number of octets a lower layer adds around each
        APDU on the wire (13 for a KNX Data Secure session, 0 otherwise). It is
        subtracted from ``max_apdu_length`` when sizing chunks so the fully
        encoded frame still fits the device's APDU length.
        """
        self.connection = connection
        self.diagnostic_context = "control=direct"
        self.max_apdu_length = max_apdu_length
        self.apdu_overhead = apdu_overhead
        self._object_index_cache: dict[tuple[int, int], int] = {}
        # Some devices cap an A_Memory_Read reply below the negotiated APDU. Once a
        # short reply reveals that cap, later reads request no more than it, so a
        # read never stalls re-asking oversized blocks.
        self._memory_read_cap: int | None = None
        self.memory_auto_verify = False
        self._memory_verify_known = False
        self.table_reference_width: int | None = None

    @property
    def _plain_apdu_length(self) -> int:
        """Usable plaintext APDU length after the wire overhead."""
        length = self.max_apdu_length - self.apdu_overhead
        if length <= 0:
            raise VerificationError("insufficient APDU budget")
        return length

    @property
    def memory_chunk_size(self) -> int:
        """Largest classic memory payload that fits into a single telegram."""
        return self._payload_budget(_MEMORY_OVERHEAD, _MAX_MEMORY_CHUNK)

    def _payload_budget(self, overhead: int, maximum: int) -> int:
        size = min(self._plain_apdu_length - overhead, maximum)
        if size < 1:
            raise VerificationError("insufficient APDU budget for service payload")
        return size

    def property_chunk_size(self, element_size: int) -> int:
        """Elements that fit a property value frame, including its response."""
        max_bytes = self._payload_budget(_PROPERTY_OVERHEAD, 250)
        if element_size < 0 or element_size > max_bytes:
            raise VerificationError(
                f"a single property element ({element_size} octets) does not fit "
                f"the APDU ({max_bytes} octets); element fragmentation is not implemented"
            )
        return (
            min(_MAX_PROPERTY_ELEMENTS, max_bytes // element_size)
            if element_size
            else _MAX_PROPERTY_ELEMENTS
        )

    async def read_device_descriptor(self) -> int:
        """Read device descriptor type 0 (the mask version)."""
        telegram = await self.connection.request(
            DeviceDescriptorRead(descriptor=0), DeviceDescriptorResponse
        )
        payload = telegram.payload
        if not isinstance(payload, DeviceDescriptorResponse):
            raise VerificationError("no device descriptor response received")
        return payload.value

    async def read_max_apdu_length(self) -> int:
        """Read the device's maximum APDU length from the Device Object.

        PID_MAX_APDU_LENGTH (property 56 of the Device Object, interface object
        index 0) holds the largest APDU the device accepts, in octets. Falls back
        to the mandatory default when the device does not expose it.
        """
        data = await self.read_property(0, PID_MAX_APDU_LENGTH)
        if not data:
            return DEFAULT_MAX_APDU_LENGTH
        return int.from_bytes(data, "big")

    async def authorize(self, key: int = FREE_ACCESS_KEY) -> int:
        """Authorize on the connection and return the granted access level.

        A_Authorize_Request presents a 4 octet key; the device answers with the
        access level it grants (0 = full access, 15 = none). Sent once per
        connection before writing, for masks that use access protection
        (KNX Standard v3.0.0, 3/3/7 A_Authorize; 3/5/2 DM_Authorize).
        """
        telegram = await self.connection.request(
            AuthorizeRequest(key=key), AuthorizeResponse
        )
        payload = telegram.payload
        if not isinstance(payload, AuthorizeResponse):
            raise VerificationError("no authorize response received")
        return payload.level

    def _block_count(self, address: int, remaining: int) -> int:
        """Octets to transfer next: the APDU chunk, clamped to the 64 KiB boundary.

        A single A_Memory_/A_UserMemory_ transfer must not straddle the 64 KiB
        boundary (the address space and APCI change there), so a block that would
        cross it is cut at the boundary.
        """
        limit = (
            self._payload_budget(4, 15)
            if address >= _USER_MEMORY_BOUNDARY
            else self.memory_chunk_size
        )
        count = min(limit, remaining)
        if address < _USER_MEMORY_BOUNDARY < address + count:
            count = _USER_MEMORY_BOUNDARY - address
        return count

    async def read_memory(self, address: int, size: int) -> bytes:
        """Read ``size`` octets starting at ``address``, chunked to the APDU length.

        Tolerant of a device that answers an A_Memory_Read with fewer octets than
        requested (some cap the reply below the negotiated APDU): the returned
        octets are valid, so the read advances by however many came back and asks
        for the rest, remembering the cap so it does not keep over-asking. Only a
        completely empty reply (no progress) is an error.
        """
        self._validate_memory_range(address, size)
        result = bytearray()
        offset = 0
        logger.debug("read_memory: addr=%#06x size=%d", address, size)
        while offset < size:
            block_address = address + offset
            count = self._block_count(block_address, size - offset)
            if self._memory_read_cap is not None:
                count = min(count, self._memory_read_cap)
            data = await self._read_block(block_address, count)
            if not data:
                # Some devices (observed on BIM M112 / mask 0701) answer certain A_Memory_Read
                # counts with a zero-length reply while serving smaller counts fine. Back the block
                # size off and retry rather than aborting the whole read; only a genuine
                # no-response at the minimum count is fatal.
                while not data and count > 1:
                    count = max(1, count // 2)
                    self._memory_read_cap = count
                    data = await self._read_block(block_address, count)
                if not data:
                    raise VerificationError(
                        f"no memory response for address {block_address:#06x}"
                    )
            if len(data) < count:
                self._memory_read_cap = len(data)
            result.extend(data)
            offset += len(data)
        return bytes(result)

    async def _read_block(self, address: int, count: int) -> bytes:
        """Read one block via A_Memory_Read or A_UserMemory_Read by address.

        Returns the response octets, which may be fewer than ``count`` (a valid
        short reply); the caller handles that. Only more octets than requested is
        a protocol violation.
        """
        if address >= _USER_MEMORY_BOUNDARY:
            request: APCI = UserMemoryRead(address=address, count=count)
            expected: type[APCI] = UserMemoryResponse
        else:
            request = MemoryRead(address=address, count=count)
            expected = MemoryResponse
        telegram = await self.connection.request(request, expected)
        payload = telegram.payload
        if not isinstance(payload, expected):
            raise VerificationError(f"no memory response for address {address:#06x}")
        assert isinstance(payload, MemoryResponse | UserMemoryResponse)
        if payload.address != address or payload.count != len(payload.data):
            raise VerificationError(
                f"memory response address/count mismatch at {address:#06x}"
            )
        if len(payload.data) > count:
            raise VerificationError(
                f"over-long memory response at {address:#06x}: "
                f"asked {count} got {len(payload.data)}"
            )
        return payload.data

    async def write_memory(
        self, address: int, data: bytes, *, verify: bool = False
    ) -> None:
        """Write ``data`` starting at ``address``, chunked to the APDU length.

        With memory auto-verify enabled, consume and validate every write echo.
        Otherwise ``verify`` reads each block back and compares it immediately.
        """
        self._validate_memory_range(address, len(data))
        if data:
            self._block_count(address, len(data))
            if not self._memory_verify_known:
                await self.read_property(0, 14)
        offset = 0
        logger.debug(
            "write_memory: addr=%#06x len=%d verify=%s", address, len(data), verify
        )
        while offset < len(data):
            block_address = address + offset
            count = self._block_count(block_address, len(data) - offset)
            block = data[offset : offset + count]
            await self._write_block(block_address, block)
            if verify and not self.memory_auto_verify:
                read_back = await self.read_memory(block_address, len(block))
                if read_back != block:
                    raise CompareMismatch(
                        f"memory verification failed at {block_address:#06x}: "
                        f"wrote {block.hex()} read {read_back.hex()}"
                    )
            offset += count

    @staticmethod
    def _validate_memory_range(address: int, size: int) -> None:
        if address < 0 or size < 0 or address + size > 0x100000:
            raise VerificationError(
                "memory range exceeds supported 20-bit address space"
            )

    async def _write_block(self, address: int, block: bytes) -> None:
        """Write one block via A_Memory_Write or A_UserMemory_Write by address."""
        request = (
            UserMemoryWrite(address=address, data=block)
            if address >= _USER_MEMORY_BOUNDARY
            else MemoryWrite(address=address, data=block)
        )
        if not self.memory_auto_verify:
            await self.connection.send_data(request)
            return
        expected = (
            UserMemoryResponse if address >= _USER_MEMORY_BOUNDARY else MemoryResponse
        )
        telegram = await self.connection.request(request, expected)
        response = telegram.payload
        if (
            not isinstance(response, expected)
            or response.address != address
            or response.count != len(block)
            or len(response.data) != len(block)
        ):
            raise VerificationError(f"memory write echo mismatch at {address:#06x}")
        if response.data != block:
            raise CompareMismatch(f"memory write echo mismatch at {address:#06x}")

    async def enable_memory_auto_verify(self) -> None:
        """Enable PID14 bit 2 on this connection and consume every write echo."""
        if self.memory_auto_verify:
            return
        status = await self.read_property(0, 14, require_nonzero=True)
        if len(status) != 1:
            raise VerificationError("invalid PID14 verify-mode control width")
        await self.write_property(0, 14, bytes([status[0] | 4]))

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
        require_nonzero: bool = False,
    ) -> bytes:
        """Read a property; required image captures reject absent elements."""
        telegram = await self.connection.request(
            PropertyValueRead(
                object_index=object_index,
                property_id=property_id,
                count=count,
                start_index=start_index,
            ),
            PropertyValueResponse,
        )
        if require_nonzero or property_id == load_state.PID_LOAD_STATE_CONTROL:
            self._log_property_response(telegram.payload, "read")
        response = _validate_property_response(
            telegram.payload,
            object_index,
            property_id,
            "read",
            start_index=start_index,
            count=count,
            require_nonzero=require_nonzero
            or property_id == load_state.PID_LOAD_STATE_CONTROL,
        )
        if object_index == 0 and property_id == 14 and start_index == count == 1:
            if response.count == 0 or not response.data:
                self.memory_auto_verify = False
            elif len(response.data) == 1:
                self.memory_auto_verify = bool(response.data[0] & 4)
            else:
                raise VerificationError("invalid PID14 verify-mode control width")
            self._memory_verify_known = True
        return response.data

    async def read_property_element_size(
        self, object_index: int, property_id: int
    ) -> int:
        from .property_layout import property_width

        telegram = await self.connection.request(
            PropertyDescriptionRead(object_index=object_index, property_id=property_id),
            PropertyDescriptionResponse,
        )
        response = telegram.payload
        if (
            not isinstance(response, PropertyDescriptionResponse)
            or response.object_index != object_index
            or response.property_id != property_id
            or response.max_count == 0
        ):
            raise VerificationError(
                f"invalid property description for object {object_index} property {property_id}"
            )
        return property_width(response.type_)

    async def read_association_table_wide(self) -> bool | None:
        """Whether the association table holds two octets per TSAP and ASAP.

        A System B Group Object Association Table is format 0 (one octet each,
        PDT_GENERIC_02) or format 1 (two octets each, PDT_GENERIC_04); the device
        tells which through the description of its PID_TABLE (KNX 3/5/1 4.17.5.2.5).
        ``None`` when the device does not tell.
        """
        try:
            index = await self.locate_object(ASSOCIATION_TABLE_OBJECT_TYPE)
            width = await self.read_property_element_size(index, PID_TABLE)
        except (DownloadError, ManagementConnectionError) as exc:
            logger.debug("association table format not readable: %s", exc)
            return None
        return {2: False, 4: True}.get(width)

    async def write_property(
        self,
        object_index: int,
        property_id: int,
        data: bytes,
        *,
        count: int = 1,
        start_index: int = 1,
        verify: bool = True,
    ) -> bytes:
        """Write a property value and return the resulting value.

        A_PropertyValue_Write is confirmed by A_PropertyValue_Response carrying
        the resulting value, so this waits for that response (via ``request``)
        rather than only the transport ACK - otherwise the buffered response
        would be mistaken for the answer to the next request.

        The element count is encoded in four bits and the data must fit the APDU,
        so a value spanning more than 15 elements or one frame is written in
        successive element ranges; the last response is returned.

        Ordinary unverified writes may accept a zero-element confirmation. Load
        control (PID 5) always requires a nonempty state response, independent of
        ``verify``.
        """
        if count <= 0:
            raise VerificationError(
                f"property write for object {object_index} property {property_id} "
                "has a non-positive element count (an unresolved wildcard count is "
                "not supported)"
            )
        if count > 1 and len(data) % count:
            raise VerificationError(
                f"property data ({len(data)} octets) is not divisible by the "
                f"element count {count} for object {object_index} property {property_id}"
            )
        element_size = (len(data) // count) if count > 1 else len(data)
        per_frame = self.property_chunk_size(element_size)

        result = b""
        element = 0
        while element < count:
            frame_count = min(per_frame, count - element)
            frame_data = (
                data[element * element_size : (element + frame_count) * element_size]
                if element_size
                else data
            )
            result = await self._write_property_frame(
                object_index,
                property_id,
                frame_data,
                frame_count,
                start_index + element,
                require_nonzero=verify,
            )
            element += frame_count
        return result

    async def _write_property_frame(
        self,
        object_index: int,
        property_id: int,
        data: bytes,
        count: int,
        start_index: int,
        *,
        require_nonzero: bool = True,
    ) -> bytes:
        """Send a single A_PropertyValue_Write and return the resulting value."""
        logger.debug(
            "property write: %s object-index=%d pid=%d count=%d start-index=%d data=%s",
            self.diagnostic_context,
            object_index,
            property_id,
            count,
            start_index,
            data.hex(),
        )
        telegram = await self.connection.request(
            PropertyValueWrite(
                object_index=object_index,
                property_id=property_id,
                count=count,
                start_index=start_index,
                data=data,
            ),
            PropertyValueResponse,
        )
        self._log_property_response(telegram.payload, "write")
        response = _validate_property_response(
            telegram.payload,
            object_index,
            property_id,
            "write",
            start_index=start_index,
            count=count,
            require_nonzero=require_nonzero
            or property_id == load_state.PID_LOAD_STATE_CONTROL,
        )
        if (
            require_nonzero
            and property_id != load_state.PID_LOAD_STATE_CONTROL
            and response.data != data
        ):
            error_type = (
                CompareMismatch
                if len(response.data) == len(data)
                else VerificationError
            )
            raise error_type(
                f"property verification failed for object {object_index} property "
                f"{property_id}: write echo mismatch, wrote {data.hex()} "
                f"read {response.data.hex()} [{self.diagnostic_context}]"
            )
        if (
            object_index == 0
            and property_id == 14
            and response.count
            and len(response.data) == 1
        ):
            self.memory_auto_verify = bool(response.data[0] & 4)
            self._memory_verify_known = True
        if response.count == 0:
            # NoVerify permits an empty ordinary property confirmation; this is
            # not evidence that the data was stored.
            # see .references/ets_map.md
            logger.debug(
                "unverified property write: %s object-index=%d pid=%d "
                "zero-element confirmation accepted; storage not verified",
                self.diagnostic_context,
                object_index,
                property_id,
            )
        return response.data

    def _log_property_response(self, payload: APCI | None, operation: str) -> None:
        if isinstance(payload, PropertyValueResponse):
            logger.debug(
                "property %s response: %s object-index=%d pid=%d count=%d start-index=%d data=%s",
                operation,
                self.diagnostic_context,
                payload.object_index,
                payload.property_id,
                payload.count,
                payload.start_index,
                payload.data.hex(),
            )
        else:
            logger.debug(
                "property %s response: %s unexpected=%r",
                operation,
                self.diagnostic_context,
                payload,
            )

    async def invoke_function_property(
        self, object_index: int, property_id: int, data: bytes
    ) -> bytes:
        """Call a Function Property and return the resulting state.

        A_FunctionPropertyCommand invokes a use-case specific function on an
        interface object and is confirmed by A_FunctionPropertyState_Response
        carrying a return code and the resulting state (KNX Standard v3.0.0,
        3/3/7 section 3.4.7.1). A non-zero return code means the device rejected
        the command.
        """
        telegram = await self.connection.request(
            FunctionPropertyCommand(
                object_index=object_index, property_id=property_id, data=data
            ),
            FunctionPropertyStateResponse,
        )
        return self._function_property_result(telegram, object_index, property_id)

    async def read_function_property(
        self, object_index: int, property_id: int
    ) -> bytes:
        """Read a Function Property state and return it.

        A_FunctionPropertyState_Read reads the state of a function property and is
        answered by A_FunctionPropertyState_Response (KNX Standard v3.0.0, 3/3/7
        section 3.4.7.2).
        """
        telegram = await self.connection.request(
            FunctionPropertyStateRead(
                object_index=object_index, property_id=property_id
            ),
            FunctionPropertyStateResponse,
        )
        return self._function_property_result(telegram, object_index, property_id)

    async def function_property_raw(
        self, object_index: int, property_id: int, data: bytes, *, command: bool
    ) -> bytes:
        """A Function Property command or state read returning the response's return code
        octet followed by its state data, without judging it."""
        request: APCI = (
            FunctionPropertyCommand(
                object_index=object_index, property_id=property_id, data=data
            )
            if command
            else FunctionPropertyStateRead(
                object_index=object_index, property_id=property_id, data=data
            )
        )
        telegram = await self.connection.request(request, FunctionPropertyStateResponse)
        payload = telegram.payload
        if not isinstance(payload, FunctionPropertyStateResponse):
            raise VerificationError(
                f"no function property response for object {object_index} "
                f"property {property_id}"
            )
        return bytes([payload.return_code]) + payload.data

    def _user_memory_chunk(self) -> int:
        return max(
            1,
            min(_MAX_USER_MEMORY_CHUNK, self.max_apdu_length - self.apdu_overhead - 5),
        )

    async def read_user_memory(self, address: int, size: int) -> bytes:
        """Read ``size`` octets with A_UserMemory_Read, whatever the address."""
        result = bytearray()
        while len(result) < size:
            block = address + len(result)
            count = min(self._user_memory_chunk(), size - len(result))
            telegram = await self.connection.request(
                UserMemoryRead(address=block, count=count), UserMemoryResponse
            )
            payload = telegram.payload
            if not isinstance(payload, UserMemoryResponse) or not payload.data:
                raise VerificationError(f"no user memory response at {block:#07x}")
            result.extend(payload.data[: size - len(result)])
        return bytes(result)

    async def write_user_memory(
        self, address: int, data: bytes, *, verify: bool = False
    ) -> None:
        """Write ``data`` with A_UserMemory_Write, whatever the address."""
        offset = 0
        while offset < len(data):
            block = data[offset : offset + self._user_memory_chunk()]
            await self.connection.send_data(
                UserMemoryWrite(address=address + offset, data=block)
            )
            if (
                verify
                and await self.read_user_memory(address + offset, len(block)) != block
            ):
                raise CompareMismatch(
                    f"user memory verification failed at {address + offset:#07x}"
                )
            offset += len(block)

    @staticmethod
    def _function_property_result(
        telegram: Telegram, object_index: int, property_id: int
    ) -> bytes:
        """Validate a Function Property response and return its state data."""
        payload = telegram.payload
        if not isinstance(payload, FunctionPropertyStateResponse):
            raise VerificationError(
                f"no function property response for object {object_index} "
                f"property {property_id}"
            )
        if payload.return_code != 0:
            raise LoadStateError(
                f"function property {property_id} on object {object_index} "
                f"returned error code {payload.return_code:#04x}"
            )
        return payload.data

    async def read_table_reference(self, object_index: int) -> int:
        """Read a loadable part's table base address (PID_TABLE_REFERENCE).

        The reference width depends on the realisation type - 2 octets for the
        memory-mapped BCU/System 7 families, 4 octets (PDT_GENERIC_04, KNX 3/5/1
        4.2.7) for System B - so the raw value is taken as-is rather than fixed
        to one width. A zero reference means the segment has not been allocated
        (or allocation failed) and must not be used as a write target (KNX 3/5/3
        3.5.1.4).
        """
        data = await self.read_property(
            object_index, PID_TABLE_REFERENCE, require_nonzero=True
        )
        if not data:
            raise VerificationError(f"empty table reference for object {object_index}")
        if len(data) not in (2, 4) or (
            self.table_reference_width is not None
            and len(data) != self.table_reference_width
        ):
            raise VerificationError(
                f"invalid table reference width {len(data)} for object {object_index}"
            )
        reference = int.from_bytes(data, "big")
        if reference == 0:
            raise LoadStateError(
                f"object {object_index} reports a zero table reference "
                "(segment not allocated)"
            )
        logger.debug(
            "read_table_reference: object=%d base=%#06x", object_index, reference
        )
        return reference

    async def locate_object(self, object_type: int, occurrence: int = 0) -> int:
        """Resolve the object index of the ``occurrence``-th object of a type.

        ``occurrence`` is zero-based, matching the product application program XML (``Occurrence="0"``
        is the first instance). Scans interface objects reading ``PID_OBJECT_TYPE``
        until the requested occurrence is found. Cached per programmer instance.
        """
        cached = self._object_index_cache.get((object_type, occurrence))
        if cached is not None:
            return cached
        ordinal = 0
        for index in range(_MAX_OBJECT_INDEX + 1):
            try:
                data = await self.read_property(index, PID_OBJECT_TYPE)
            except (VerificationError, DownloadError):
                break
            if len(data) < 2:
                break
            found_type = int.from_bytes(data[:2], "big")
            if found_type == object_type:
                if ordinal == occurrence:
                    self._object_index_cache[(object_type, occurrence)] = index
                    return index
                ordinal += 1
        raise LoadStateError(
            f"interface object type {object_type} occurrence {occurrence} not found"
        )

    async def read_load_state(self, object_index: int) -> load_state.LoadState:
        """Read the current state of an object's Load State Machine."""
        data = await self.read_property(object_index, load_state.PID_LOAD_STATE_CONTROL)
        if not data:
            raise LoadStateError(f"empty load state for object {object_index}")
        state = _decode_load_state(data[0], object_index)
        logger.debug(
            "load state read: %s object-index=%d data=%s state=%s",
            self.diagnostic_context,
            object_index,
            data.hex(),
            state.name,
        )
        return state

    async def send_load_event(
        self,
        object_index: int,
        event: bytes,
        expected: load_state.LoadState,
        *,
        also_accept: load_state.LoadState | None = None,
        retries: int = 30,
        retry_delay: float = 1.0,
    ) -> None:
        """Write a load event and verify the machine reaches ``expected``.

        ``LOAD_COMPLETE`` triggers a checksum calculation that can take a while,
        and a device may report a transient ``UNLOADING``/``LOAD_COMPLETING``
        state first, so the state is polled up to ``retries`` times.
        ``also_accept`` permits an alternative result for a specific control,
        such as ``LOADED`` after ``LdCtrlLoad`` on devices that skip ``LOADING``.
        """
        accepted = (expected,) if also_accept is None else (expected, also_accept)
        target = " or ".join(state.name for state in accepted)
        logger.debug(
            "send_load_event: %s object-index=%d event=%s expected=%s",
            self.diagnostic_context,
            object_index,
            event.hex(),
            target,
        )
        resulting = await self.write_property(
            object_index, load_state.PID_LOAD_STATE_CONTROL, event
        )
        state = _decode_load_state(resulting[0], object_index)
        logger.debug(
            "send_load_event: %s object-index=%d target=%s initial=%s",
            self.diagnostic_context,
            object_index,
            target,
            state.name,
        )
        for poll in range(1, max(retries, 0) + 1):
            if state in accepted:
                return
            if state == load_state.LoadState.ERROR:
                raise LoadStateError(
                    f"object {object_index} entered ERROR state (expected {target})"
                )
            await asyncio.sleep(retry_delay)
            state = await self.read_load_state(object_index)
            logger.debug(
                "load state poll: %s object-index=%d poll=%d/%d state=%s expected=%s",
                self.diagnostic_context,
                object_index,
                poll,
                retries,
                state.name,
                target,
            )
        if state not in accepted:
            raise LoadStateError(
                f"object {object_index} did not reach {target}, last state {state.name}"
            )

    async def restart(self) -> None:
        """Restart the device (also closes the transport connection).

        Sent without waiting for a transport ACK on purpose: a Basic Restart has
        no application-layer response and the device tears the connection down
        immediately, so it often restarts before (or instead of) ACKing. Waiting
        for the ACK would usually time out and just delay the teardown the caller
        already performs. The connection-oriented confirmation the standard
        mentions (3/3/7 3.4.2.2) is therefore not relied upon here; a Master
        Reset, which *is* application-layer confirmed, uses the response path.
        """
        logger.debug("restart: sending Basic Restart (no ACK)")
        await self.connection.send_data(Restart(), wait_for_ack=False)
        self.memory_auto_verify = False
        self._memory_verify_known = False

    async def master_reset(self, erase_code: int, channel_number: int) -> int:
        """Perform a Master Reset and return the device's process time in seconds.

        A Master Reset is an A_Restart with restart_type = 1, carrying an erase
        code and channel number (KNX Standard v3.0.0, 3/3/7 section 3.4.2.2; the
        erase codes are defined with DM_Restart in 3/5/2). Unlike a Basic Restart
        it is confirmed at the application layer by A_Restart_Master_Reset_Response
        with an error code and the process time the device needs before it is
        reachable again. A non-zero error code means the device refused the reset.
        The device restarts afterwards, tearing down the connection.
        """
        telegram = await self.connection.request(
            RestartMasterReset(erase_code=erase_code, channel_number=channel_number),
            RestartMasterResetResponse,
        )
        logger.debug(
            "master_reset: erase_code=%d channel=%d", erase_code, channel_number
        )
        payload = telegram.payload
        if not isinstance(payload, RestartMasterResetResponse):
            raise LoadStateError("no master reset response received")
        if payload.error_code != 0:
            raise LoadStateError(
                f"device refused master reset (erase code {erase_code}, channel "
                f"{channel_number}): error code {payload.error_code:#04x}"
            )
        self.memory_auto_verify = False
        self._memory_verify_known = False
        return payload.process_time


def _validate_property_response(
    payload: APCI | None,
    object_index: int,
    property_id: int,
    operation: str,
    *,
    start_index: int,
    count: int,
    require_nonzero: bool = False,
) -> PropertyValueResponse:
    """Return the response, or raise if it is missing or mismatched.

    A response echoes the addressed object and property; a differing echo means a
    stale/buffered telegram was mistaken for the answer. With ``require_nonzero``
    a count (nr_of_elem) of 0 is treated as the device rejecting the access (KNX
    3/3/7 3.4.4.1/3.4.4.2). Ordinary reads leave it off: object location and
    APDU-length negotiation tolerate absent properties. PID 5 reads/writes always
    require a nonzero count and state data. Element addressing must match too.
    """
    if not isinstance(payload, PropertyValueResponse):
        raise VerificationError(
            f"no property {operation} response for object {object_index} "
            f"property {property_id}, got {payload!r}"
        )
    if payload.object_index != object_index or payload.property_id != property_id:
        raise VerificationError(
            f"property {operation} response addresses object "
            f"{payload.object_index} property {payload.property_id}, "
            f"expected object {object_index} property {property_id}"
        )
    if payload.start_index != start_index:
        raise VerificationError(
            f"property {operation} response start index {payload.start_index}, "
            f"expected {start_index} for object {object_index} property {property_id}"
        )
    if payload.count != count and payload.count != 0:
        raise VerificationError(
            f"property {operation} response element count {payload.count}, "
            f"expected {count} for object {object_index} property {property_id}"
        )
    if require_nonzero and payload.count == 0:
        raise PropertyAccessRejected(
            f"device rejected property {operation}: object {object_index} "
            f"property {property_id} returned 0 elements",
            error_code=RESOURCE_READ_PROTECTED_ERROR
            if operation == "read"
            else RESOURCE_WRITE_PROTECTED_ERROR,
        )
    if property_id == load_state.PID_LOAD_STATE_CONTROL:
        if not payload.data:
            raise LoadStateError(f"empty load state response for object {object_index}")
        _decode_load_state(payload.data[0], object_index)
    return payload


def _decode_load_state(value: int, object_index: int) -> load_state.LoadState:
    """Map a raw load state octet to :class:`LoadState`, erroring on unknowns."""
    try:
        return load_state.LoadState(value)
    except ValueError as exc:
        raise LoadStateError(
            f"object {object_index} reported unknown load state {value:#04x}"
        ) from exc

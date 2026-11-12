"""Shared test doubles for the recover package."""

from __future__ import annotations

from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import (
    APCI,
    DeviceDescriptorRead,
    DeviceDescriptorResponse,
    MemoryRead,
    MemoryResponse,
    PropertyValueRead,
    PropertyValueResponse,
)

from xknxeditor.download.programmer import PID_OBJECT_TYPE

_SOURCE = IndividualAddress("1.1.1")


class FakeConnection:
    """A minimal point-to-point connection answering the reads recover uses.

    Answers A_DeviceDescriptor_Read from ``descriptor`` and A_PropertyValue_Read
    from ``object_types`` (PID_OBJECT_TYPE, for object location) and ``properties``
    (a ``(object_index, property_id) -> bytes`` map). A_Memory_Read is answered from
    ``memory`` (an ``address -> octets`` map of memory ranges). With ``property_services``
    off it leaves A_PropertyValue_Read unanswered, as a BCU 1 does.
    """

    def __init__(
        self,
        *,
        descriptor: int = 0x0705,
        object_types: dict[int, int] | None = None,
        properties: dict[tuple[int, int], bytes] | None = None,
        memory: dict[int, bytes] | None = None,
        property_services: bool = True,
    ) -> None:
        self.descriptor = descriptor
        self.object_types = object_types or {}
        self.properties = properties or {}
        self.memory = memory or {}
        self.property_services = property_services
        self.requests: list[APCI] = []

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        raise AssertionError(f"unexpected send_data: {payload}")

    async def request(
        self, payload: APCI, expected: type[APCI] | None = None
    ) -> Telegram:
        self.requests.append(payload)
        if isinstance(payload, DeviceDescriptorRead):
            return _telegram(
                DeviceDescriptorResponse(descriptor=0, value=self.descriptor)
            )
        if isinstance(payload, PropertyValueRead):
            if not self.property_services:
                raise ManagementConnectionError("no PropertyValueResponse received")
            return _telegram(self._read_property(payload))
        if isinstance(payload, MemoryRead):
            return _telegram(self._read_memory(payload))
        raise AssertionError(f"unexpected request: {payload}")

    def _read_memory(self, payload: MemoryRead) -> MemoryResponse:
        for start, octets in self.memory.items():
            offset = payload.address - start
            if 0 <= offset < len(octets):
                data = octets[offset : offset + payload.count]
                return MemoryResponse(
                    address=payload.address, count=len(data), data=data
                )
        raise ManagementConnectionError("no MemoryResponse received")

    def _read_property(self, payload: PropertyValueRead) -> PropertyValueResponse:
        if payload.property_id == PID_OBJECT_TYPE:
            object_type = self.object_types.get(payload.object_index)
            data = object_type.to_bytes(2, "big") if object_type is not None else b""
        else:
            data = self.properties.get((payload.object_index, payload.property_id), b"")
        # Echo the request's element addressing like a real device: start_index and a
        # count of 1 for a present value, 0 for an absent one (the device signalling
        # "no such element"), which the programmer's response validation checks.
        return PropertyValueResponse(
            object_index=payload.object_index,
            property_id=payload.property_id,
            count=payload.count if data else 0,
            start_index=payload.start_index,
            data=data,
        )


class FakeManagement:
    """Stand-in for ``xknx.management`` mapping addresses to connections."""

    def __init__(self, connections: dict[str, FakeConnection]) -> None:
        self._connections = connections
        self.connected: list[str] = []
        self.disconnected: list[str] = []

    async def connect(self, address: IndividualAddress) -> FakeConnection:
        key = str(address)
        connection = self._connections.get(key)
        if connection is None:
            raise ManagementConnectionError(f"no device at {key}")
        self.connected.append(key)
        return connection

    async def disconnect(self, address: IndividualAddress) -> None:
        self.disconnected.append(str(address))


class FakeXknx:
    """A stand-in exposing only the ``management`` API recover's scan needs."""

    def __init__(self, connections: dict[str, FakeConnection]) -> None:
        self.management = FakeManagement(connections)


def _telegram(payload: APCI) -> Telegram:
    return Telegram(destination_address=_SOURCE, payload=payload)
